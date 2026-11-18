#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

PROJECT_DIRECTORY=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
RELEASE_URL="https://github.com/NVIDIA/OpenShell/releases"
SANDBOX_BASE_IMAGE="${OMM_VERIFY_SANDBOX_BASE_IMAGE:-nvcr.io/nvidia/base/ubuntu:24.04}"
UPSTREAM_HOST="host.openshell.internal"

usage() {
    echo "Usage: $0 [--openshell-version TAG|latest] [--language python|rust|all] [--work-dir DIR]"
    echo
    echo "Check that omm starters work with a real OpenShell release. The script downloads"
    echo "that release's gateway and CLI into a work directory, generates a starter with"
    echo "omm create, refreshes it with omm update, runs the starter's documented checks,"
    echo "and sends sandbox traffic through the running starter. It installs nothing."
    echo
    echo "Requires Linux, Docker, curl, openssl, and uv. Rust also needs cargo with"
    echo "rustfmt and clippy. Set OMM_VERIFY_HOST to an IPv4 address that the gateway"
    echo "and sandbox supervisors can both reach when detection picks the wrong one."
    echo "The work directory is kept when a check fails or --work-dir names it."
}

OPENSHELL_VERSION="latest"
REQUESTED_LANGUAGE="all"
WORK_DIRECTORY=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --openshell-version | --language | --work-dir)
            if [[ $# -lt 2 ]]; then
                usage >&2
                exit 2
            fi
            case "$1" in
                --openshell-version) OPENSHELL_VERSION="$2" ;;
                --language) REQUESTED_LANGUAGE="$2" ;;
                --work-dir) WORK_DIRECTORY="$2" ;;
            esac
            shift
            ;;
        -h | --help)
            usage
            exit 0
            ;;
        *)
            usage >&2
            exit 2
            ;;
    esac
    shift
done

case "$REQUESTED_LANGUAGE" in
    all) LANGUAGES=(python rust) ;;
    python | rust) LANGUAGES=("$REQUESTED_LANGUAGE") ;;
    *)
        echo "verify: --language must be 'python', 'rust', or 'all'" >&2
        exit 2
        ;;
esac

fail() {
    echo "FAIL $1" >&2
    exit 1
}

pass() {
    echo "PASS $1"
}

[[ "$(uname -s)" == "Linux" ]] || fail "this check supports Linux hosts only"
REQUIRED_COMMANDS=(curl docker openssl sha256sum tar uv)
if [[ " ${LANGUAGES[*]} " == *" rust "* ]]; then
    REQUIRED_COMMANDS+=(cargo)
fi
for COMMAND in "${REQUIRED_COMMANDS[@]}"; do
    command -v "$COMMAND" >/dev/null 2>&1 || fail "missing required command: $COMMAND"
done
docker info >/dev/null 2>&1 || fail "the Docker daemon is not reachable"
# Generated projects must use their own environments, not the caller's.
unset VIRTUAL_ENV

case "$(uname -m)" in
    x86_64) ARCHITECTURE="x86_64" ;;
    aarch64 | arm64) ARCHITECTURE="aarch64" ;;
    *) fail "unsupported architecture: $(uname -m)" ;;
esac

if [[ "$OPENSHELL_VERSION" == "latest" ]]; then
    LATEST_URL=$(curl -fsSL -o /dev/null -w '%{url_effective}' "$RELEASE_URL/latest")
    OPENSHELL_VERSION="${LATEST_URL##*/}"
elif [[ "$OPENSHELL_VERSION" != v* ]]; then
    OPENSHELL_VERSION="v$OPENSHELL_VERSION"
fi
[[ "$OPENSHELL_VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+([+-][0-9A-Za-z._-]+)?$ ]] ||
    fail "invalid OpenShell version: $OPENSHELL_VERSION"

RUN_ID="omm-verify-$$-$RANDOM"
KEEP_WORK_DIRECTORY=true
if [[ -z "$WORK_DIRECTORY" ]]; then
    WORK_DIRECTORY=$(mktemp -d)
    KEEP_WORK_DIRECTORY=false
fi
mkdir -p "$WORK_DIRECTORY"
WORK_DIRECTORY=$(cd "$WORK_DIRECTORY" && pwd)
[[ ! -e "$WORK_DIRECTORY/python" && ! -e "$WORK_DIRECTORY/rust" ]] ||
    fail "work directory already holds a generated starter: $WORK_DIRECTORY"
LOG_DIRECTORY="$WORK_DIRECTORY/logs"
BIN_DIRECTORY="$WORK_DIRECTORY/openshell"
SANDBOX_IMAGE="omm-verify/sandbox-curl:$RUN_ID"
mkdir -p "$LOG_DIRECTORY" "$BIN_DIRECTORY" "$WORK_DIRECTORY/xdg"

declare -A MIDDLEWARE_PORT MIDDLEWARE_PID
GATEWAY_PID=""
UPSTREAM_PID=""
SANDBOXES=()
SANDBOX_IMAGE_BUILT=false

# Keep the downloaded OpenShell away from the caller's OpenShell settings and state.
OPENSHELL_ENVIRONMENT=(env)
while IFS= read -r VARIABLE; do
    OPENSHELL_ENVIRONMENT+=(-u "$VARIABLE")
done < <(compgen -e | grep '^OPENSHELL_' || true)
OPENSHELL_ENVIRONMENT+=(
    "XDG_CONFIG_HOME=$WORK_DIRECTORY/xdg/config" "XDG_DATA_HOME=$WORK_DIRECTORY/xdg/data"
    "XDG_STATE_HOME=$WORK_DIRECTORY/xdg/state" "XDG_CACHE_HOME=$WORK_DIRECTORY/xdg/cache"
)

openshell_cli() {
    "${OPENSHELL_ENVIRONMENT[@]}" "$BIN_DIRECTORY/openshell" \
        --gateway-endpoint "http://127.0.0.1:$GATEWAY_PORT" "$@"
}

stop_process() {
    local pid="$1"
    if [[ -n "$pid" ]]; then
        kill "$pid" 2>/dev/null || true
        wait "$pid" 2>/dev/null || true
    fi
}

cleanup() {
    local status=$?
    trap - EXIT
    local sandbox language
    for sandbox in "${SANDBOXES[@]}"; do
        openshell_cli sandbox delete "$sandbox" >>"$LOG_DIRECTORY/cleanup.log" 2>&1 || true
    done
    # The gateway removes sandbox containers asynchronously after it accepts a deletion.
    local containers
    for _ in {1..30}; do
        containers=$(docker ps -aq --filter "label=openshell.ai/sandbox-namespace=$RUN_ID")
        [[ -n "$containers" ]] || break
        sleep 1
    done
    stop_process "$GATEWAY_PID"
    # Only this run uses this sandbox label, so anything left under it is this run's to remove.
    containers=$(docker ps -aq --filter "label=openshell.ai/sandbox-namespace=$RUN_ID")
    if [[ -n "$containers" ]]; then
        echo "INFO removing sandbox containers that the gateway left behind" >&2
        # shellcheck disable=SC2086 # one container ID per word
        docker rm -f $containers >>"$LOG_DIRECTORY/cleanup.log" 2>&1 || true
    fi
    for language in "${!MIDDLEWARE_PID[@]}"; do
        stop_process "${MIDDLEWARE_PID[$language]}"
    done
    stop_process "$UPSTREAM_PID"
    if [[ "$SANDBOX_IMAGE_BUILT" == true ]]; then
        docker rmi "$SANDBOX_IMAGE" >>"$LOG_DIRECTORY/cleanup.log" 2>&1 || true
    fi
    if [[ "$status" -eq 0 && "$KEEP_WORK_DIRECTORY" == false ]]; then
        rm -rf "${WORK_DIRECTORY:?}"
    else
        echo "INFO work directory: $WORK_DIRECTORY" >&2
    fi
    exit "$status"
}
trap cleanup EXIT

run_logged() {
    local label="$1" log="$LOG_DIRECTORY/$2.log"
    shift 2
    if ! "$@" >>"$log" 2>&1; then
        tail -n 40 "$log" >&2
        fail "$label (see $log)"
    fi
    pass "$label"
}

free_port() {
    uv run --no-project python -c \
        'import socket; s = socket.socket(); s.bind(("", 0)); print(s.getsockname()[1])'
}

wait_for_port() {
    local port="$1" pid="$2" label="$3"
    for _ in {1..60}; do
        kill -0 "$pid" 2>/dev/null || fail "$label exited before listening"
        if (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then
            return
        fi
        sleep 0.5
    done
    fail "$label did not listen on port $port"
}

# Each run_* function replaces its background subshell, so $! is the service itself.
run_middleware() {
    local language="$1" listen="0.0.0.0:${MIDDLEWARE_PORT[$1]}"
    if [[ "$language" == "python" ]]; then
        exec "$WORK_DIRECTORY/python/.venv/bin/omm-verify-python" --listen "$listen"
    fi
    exec "$WORK_DIRECTORY/rust-target/debug/omm-verify-rust" "$listen"
}

run_upstream() {
    exec "$WORK_DIRECTORY/upstream-environment/bin/python" "$WORK_DIRECTORY/upstream.py" \
        "$UPSTREAM_PORT"
}

run_gateway() {
    exec "${OPENSHELL_ENVIRONMENT[@]}" "$BIN_DIRECTORY/openshell-gateway" \
        --compute-driver docker --config "$WORK_DIRECTORY/gateway.toml" \
        --bind-address 127.0.0.1 --port "$GATEWAY_PORT" --health-port "$HEALTH_PORT" \
        --metrics-port 0 --log-level info --disable-tls \
        --db-url "sqlite://$WORK_DIRECTORY/gateway.db"
}

start_middleware() {
    local language="$1"
    run_middleware "$language" >>"$LOG_DIRECTORY/middleware-$language.log" 2>&1 &
    MIDDLEWARE_PID[$language]=$!
    wait_for_port "${MIDDLEWARE_PORT[$language]}" "${MIDDLEWARE_PID[$language]}" \
        "$language middleware"
}

# Run curl in the sandbox. Prints the response body, then the status code on the last line.
sandbox_request() {
    local sandbox="$1" payload="$2"
    openshell_cli sandbox exec --name "$sandbox" --no-tty -- \
        curl -sS --max-time 20 -w '\n%{http_code}' \
        -X POST "http://$UPSTREAM_HOST:$UPSTREAM_PORT/echo" \
        -H 'content-type: text/plain' --data "$payload" 2>>"$LOG_DIRECTORY/requests.log"
}

upstream_request_count() {
    grep -c '^request ' "$LOG_DIRECTORY/upstream.log" || true
}

# ---------------------------------------------------------------------------------------
echo "INFO verifying omm against OpenShell $OPENSHELL_VERSION for: ${LANGUAGES[*]}"

# run_logged calls this inside an `if`, where `set -e` is off, so each command returns on failure.
download_release_archive() {
    local archive="$1" checksums="$2" file
    for file in "$archive" "$checksums"; do
        curl -fsSL -o "$BIN_DIRECTORY/$file" \
            "$RELEASE_URL/download/$OPENSHELL_VERSION/$file" || return 1
    done
    (cd "$BIN_DIRECTORY" && grep " $archive\$" "$checksums" | sha256sum --check --status) ||
        return 1
    tar -xzf "$BIN_DIRECTORY/$archive" -C "$BIN_DIRECTORY"
}
run_logged "downloaded and checksum-verified the OpenShell $OPENSHELL_VERSION gateway" setup \
    download_release_archive "openshell-gateway-$ARCHITECTURE-unknown-linux-gnu.tar.gz" \
    openshell-gateway-checksums-sha256.txt
run_logged "downloaded and checksum-verified the OpenShell $OPENSHELL_VERSION CLI" setup \
    download_release_archive "openshell-$ARCHITECTURE-unknown-linux-musl.tar.gz" \
    openshell-checksums-sha256.txt
[[ "$("$BIN_DIRECTORY/openshell-gateway" --version)" == "openshell-gateway ${OPENSHELL_VERSION#v}" ]] ||
    fail "downloaded gateway does not report version ${OPENSHELL_VERSION#v}"

# --- generate each starter, refresh it with omm update, and run its documented checks ---
omm() {
    uv run --project "$PROJECT_DIRECTORY" omm "$@"
}

for LANGUAGE in "${LANGUAGES[@]}"; do
    PROJECT="$WORK_DIRECTORY/$LANGUAGE"
    run_logged "omm create --language $LANGUAGE --openshell-version $OPENSHELL_VERSION" \
        "create-$LANGUAGE" \
        omm create "omm-verify-$LANGUAGE" --language "$LANGUAGE" \
        --openshell-version "$OPENSHELL_VERSION" --output "$PROJECT"
    grep -q "\"openshell_version\": \"$OPENSHELL_VERSION\"" \
        "$PROJECT/.openshell-middleware-manifest.json" ||
        fail "$LANGUAGE manifest does not record $OPENSHELL_VERSION"
    run_logged "omm update --openshell-version $OPENSHELL_VERSION ($LANGUAGE)" \
        "update-$LANGUAGE" omm update "$PROJECT" --openshell-version "$OPENSHELL_VERSION"

    cd "$PROJECT"
    if [[ "$LANGUAGE" == "python" ]]; then
        run_logged "python starter: uv sync --locked" checks-python uv sync --locked
        run_logged "python starter: ruff format --check" checks-python \
            uv run ruff format --check .
        run_logged "python starter: ruff check" checks-python uv run ruff check .
        run_logged "python starter: ty check" checks-python uv run ty check
        run_logged "python starter: pytest" checks-python uv run pytest
        run_logged "python starter: uv build" checks-python uv build
    else
        export CARGO_TARGET_DIR="$WORK_DIRECTORY/rust-target"
        run_logged "rust starter: cargo fmt --check" checks-rust cargo fmt --check
        run_logged "rust starter: cargo clippy -D warnings" checks-rust \
            cargo clippy --all-targets --all-features -- -D warnings
        run_logged "rust starter: cargo test --locked" checks-rust cargo test --locked
        run_logged "rust starter: cargo build --locked" checks-rust cargo build --locked
    fi
    cd "$WORK_DIRECTORY"
done

# --- start the starters, an echo upstream, and the OpenShell gateway ----------------------
HOST_ADDRESS="${OMM_VERIFY_HOST:-}"
if [[ -z "$HOST_ADDRESS" ]]; then
    HOST_ADDRESS=$(ip -4 route get 1.1.1.1 2>/dev/null |
        awk '{ for (i = 1; i < NF; i++) if ($i == "src") { print $(i + 1); exit } }' || true)
fi
[[ "$HOST_ADDRESS" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ && "$HOST_ADDRESS" != 127.* ]] ||
    fail "set OMM_VERIFY_HOST to a non-loopback IPv4 address of this host"

for LANGUAGE in "${LANGUAGES[@]}"; do
    MIDDLEWARE_PORT[$LANGUAGE]=$(free_port)
    start_middleware "$LANGUAGE"
done

UPSTREAM_PORT=$(free_port)
cat >"$WORK_DIRECTORY/upstream.py" <<'UPSTREAM'
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        print(f"request {self.command} {self.path} {len(body)} bytes", flush=True)
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        pass


ThreadingHTTPServer(("0.0.0.0", int(sys.argv[1])), Handler).serve_forever()
UPSTREAM
uv venv --quiet "$WORK_DIRECTORY/upstream-environment"
run_upstream >"$LOG_DIRECTORY/upstream.log" 2>&1 &
UPSTREAM_PID=$!
wait_for_port "$UPSTREAM_PORT" "$UPSTREAM_PID" "echo upstream"

mkdir -p "$WORK_DIRECTORY/jwt"
openssl genpkey -algorithm ed25519 -out "$WORK_DIRECTORY/jwt/signing.pem" 2>/dev/null
openssl pkey -in "$WORK_DIRECTORY/jwt/signing.pem" -pubout \
    -out "$WORK_DIRECTORY/jwt/public.pem" 2>/dev/null
echo "$RUN_ID" >"$WORK_DIRECTORY/jwt/kid"

# A unique sandbox label keeps this gateway from managing any other gateway's containers.
cat >"$WORK_DIRECTORY/gateway.toml" <<GATEWAY
[openshell]
version = 2

[openshell.gateway.auth]
allow_unauthenticated_users = true

[openshell.gateway.gateway_jwt]
signing_key_path = "$WORK_DIRECTORY/jwt/signing.pem"
public_key_path = "$WORK_DIRECTORY/jwt/public.pem"
kid_path = "$WORK_DIRECTORY/jwt/kid"
gateway_id = "$RUN_ID"

[openshell.drivers.docker]
sandbox_label = "$RUN_ID"
GATEWAY
for LANGUAGE in "${LANGUAGES[@]}"; do
    # The registration that the generated README documents.
    cat >>"$WORK_DIRECTORY/gateway.toml" <<REGISTRATION

[[openshell.supervisor.middleware]]
name = "omm-verify-$LANGUAGE"
grpc_endpoint = "http://$HOST_ADDRESS:${MIDDLEWARE_PORT[$LANGUAGE]}"
allow_insecure_transport = true
max_payload_bytes = 4194304
timeout = "500ms"
REGISTRATION
done

GATEWAY_PORT=$(free_port)
HEALTH_PORT=$(free_port)
run_gateway >"$LOG_DIRECTORY/gateway.log" 2>&1 &
GATEWAY_PID=$!
GATEWAY_HEALTHY=false
for _ in {1..60}; do
    if ! kill -0 "$GATEWAY_PID" 2>/dev/null; then
        tail -n 20 "$LOG_DIRECTORY/gateway.log" >&2
        fail "the gateway rejected the registered starters at startup"
    fi
    if curl -fsS "http://127.0.0.1:$HEALTH_PORT/healthz" >/dev/null 2>&1; then
        GATEWAY_HEALTHY=true
        break
    fi
    sleep 1
done
[[ "$GATEWAY_HEALTHY" == true ]] || fail "the gateway did not become healthy"
pass "OpenShell $OPENSHELL_VERSION gateway started after Describe on every registered starter"

openshell_cli gateway info -o json >"$LOG_DIRECTORY/gateway-info.json" 2>>"$LOG_DIRECTORY/setup.log" ||
    fail "openshell gateway info"
cat >"$WORK_DIRECTORY/check_negotiation.py" <<'NEGOTIATION'
import json
import sys

extensions = json.load(open(sys.argv[1]))["extensions"]
extension = next(item for item in extensions if item["configured_name"] == sys.argv[2])
assert extension["kind"] == "supervisor-middleware", extension
assert extension["protocol_version"] == "1.0", extension
assert "openshell.supervisor-middleware.http-hooks" in extension["required_capabilities"], extension
NEGOTIATION
for LANGUAGE in "${LANGUAGES[@]}"; do
    run_logged "gateway negotiated protocol 1.0 with HTTP hooks for the $LANGUAGE starter" setup \
        uv run --no-project python "$WORK_DIRECTORY/check_negotiation.py" \
        "$LOG_DIRECTORY/gateway-info.json" "omm-verify-$LANGUAGE"
done

# --- send sandbox traffic through each starter --------------------------------------------
mkdir -p "$WORK_DIRECTORY/image"
printf 'FROM %s\nRUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*\n' \
    "$SANDBOX_BASE_IMAGE" >"$WORK_DIRECTORY/image/Dockerfile"
run_logged "built a sandbox image with curl" setup \
    docker build --quiet --tag "$SANDBOX_IMAGE" "$WORK_DIRECTORY/image"
SANDBOX_IMAGE_BUILT=true

for LANGUAGE in "${LANGUAGES[@]}"; do
    NAME="omm-verify-$LANGUAGE"
    SANDBOX="omm-${LANGUAGE:0:2}-$RANDOM"
    PAYLOAD="omm verification payload for $LANGUAGE"
    # The attachment that the generated README documents.
    cat >"$WORK_DIRECTORY/policy-$LANGUAGE.yaml" <<POLICY
version: 1

network_middlewares:
  $NAME:
    middleware: $NAME
    endpoints:
      include:
        - $UPSTREAM_HOST

network_policies:
  echo-upstream:
    name: echo-upstream
    endpoints:
      - host: $UPSTREAM_HOST
        port: $UPSTREAM_PORT
        protocol: rest
        rules:
          - allow:
              method: POST
              path: /echo
    binaries:
      - path: /usr/bin/curl
POLICY
    SANDBOXES+=("$SANDBOX")
    run_logged "gateway admitted a sandbox policy that attaches the $LANGUAGE starter" setup \
        openshell_cli sandbox create --name "$SANDBOX" --from "$SANDBOX_IMAGE" \
        --policy "$WORK_DIRECTORY/policy-$LANGUAGE.yaml" --no-tty --detach -- sleep infinity

    RESPONSE=""
    for _ in {1..30}; do
        RESPONSE=$(sandbox_request "$SANDBOX" "$PAYLOAD") && [[ "$RESPONSE" == *$'\n200' ]] && break
        sleep 1
    done
    [[ "$RESPONSE" == "$PAYLOAD"$'\n200' ]] ||
        fail "$LANGUAGE starter did not pass the request through unchanged: $RESPONSE"
    pass "sandbox request passed through the $LANGUAGE starter unchanged (HTTP 200)"

    INVOCATION_LOGGED=false
    for _ in {1..20}; do
        openshell_cli logs "$SANDBOX" -n 500 >"$LOG_DIRECTORY/sandbox-$LANGUAGE.log" 2>&1 || true
        if grep 'http_event_kind:middleware_invocation' "$LOG_DIRECTORY/sandbox-$LANGUAGE.log" |
            grep -q 'ALLOWED.*failed:false'; then
            INVOCATION_LOGGED=true
            break
        fi
        sleep 1
    done
    [[ "$INVOCATION_LOGGED" == true ]] ||
        fail "OpenShell did not log an allowed middleware invocation for the $LANGUAGE starter"
    pass "OpenShell logged an allowed middleware invocation for the $LANGUAGE starter"

    # Negative control: the request must depend on the starter answering.
    UPSTREAM_REQUESTS=$(upstream_request_count)
    stop_process "${MIDDLEWARE_PID[$LANGUAGE]}"
    unset "MIDDLEWARE_PID[$LANGUAGE]"
    RESPONSE=$(sandbox_request "$SANDBOX" "$PAYLOAD") || true
    [[ "$RESPONSE" == *'"error":"middleware_failed"'*"\"middleware\":\"$NAME\""*$'\n403' ]] ||
        fail "OpenShell did not block the request while the $LANGUAGE starter was down: $RESPONSE"
    [[ "$(upstream_request_count)" == "$UPSTREAM_REQUESTS" ]] ||
        fail "the blocked request still reached the upstream"
    pass "OpenShell blocked the request while the $LANGUAGE starter was stopped (HTTP 403)"

    start_middleware "$LANGUAGE"
    RESPONSE=""
    for _ in {1..15}; do
        RESPONSE=$(sandbox_request "$SANDBOX" "$PAYLOAD") && [[ "$RESPONSE" == *$'\n200' ]] && break
        sleep 1
    done
    [[ "$RESPONSE" == "$PAYLOAD"$'\n200' ]] ||
        fail "requests did not recover after the $LANGUAGE starter restarted: $RESPONSE"
    pass "requests passed again after the $LANGUAGE starter restarted"
done

echo "ALL PASS omm works with OpenShell $OPENSHELL_VERSION (${LANGUAGES[*]})"
