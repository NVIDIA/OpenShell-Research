#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_PROJECT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

MIDDLEWARE_PORT=50051
CAPTURE_PORT=18080
GATEWAY_CONFIG="${HOME}/.config/openshell/gateway.toml"
GATEWAY_BACKUP="${GATEWAY_CONFIG}.privacy-guard-e2e.bak"
GATEWAY_ABSENT_MARKER="${GATEWAY_CONFIG}.privacy-guard-e2e.absent"
SANDBOX_NAME="privacy-guard-e2e-$$"
SANDBOX_IMAGE="${PRIVACY_GUARD_E2E_SANDBOX_IMAGE:-ghcr.io/nvidia/openshell-community/sandboxes/base@sha256:aeef1c63f00e2913ea002ccb3aaf925f338b5c5d70e63576f0d95c16a138044e}"
EXPECTED_TEXT="hello [email]"

temporary_directory=""
middleware_pid=""
capture_pid=""
gateway_config_changed=false

log() {
  printf '[privacy-guard-e2e] %s\n' "$*"
}

fail() {
  printf '[privacy-guard-e2e] ERROR: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "required command not found: $1"
}

wait_for_tcp_port() {
  local port="$1"
  local attempt
  for attempt in $(seq 1 60); do
    if python3 -c "import socket; s=socket.create_connection(('127.0.0.1', ${port}), 0.2); s.close()" 2>/dev/null; then
      return 0
    fi
    sleep 0.25
  done
  return 1
}

wait_for_gateway() {
  local attempt
  for attempt in $(seq 1 60); do
    if openshell status >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

restore_gateway_config() {
  if [[ "${gateway_config_changed}" != true ]]; then
    return
  fi

  if [[ -f "${GATEWAY_BACKUP}" ]]; then
    mv "${GATEWAY_BACKUP}" "${GATEWAY_CONFIG}"
  elif [[ -f "${GATEWAY_ABSENT_MARKER}" ]]; then
    rm -f "${GATEWAY_CONFIG}" "${GATEWAY_ABSENT_MARKER}"
  else
    printf '[privacy-guard-e2e] ERROR: gateway backup state is missing; not restarting gateway\n' >&2
    return
  fi
  gateway_config_changed=false
  brew services restart openshell >/dev/null
  wait_for_gateway || printf '[privacy-guard-e2e] WARNING: restored gateway did not become ready\n' >&2
}

cleanup() {
  set +e
  openshell sandbox delete "${SANDBOX_NAME}" >/dev/null 2>&1
  restore_gateway_config
  if [[ -n "${middleware_pid}" ]]; then
    kill "${middleware_pid}" >/dev/null 2>&1
    wait "${middleware_pid}" >/dev/null 2>&1
  fi
  if [[ -n "${capture_pid}" ]]; then
    kill "${capture_pid}" >/dev/null 2>&1
    wait "${capture_pid}" >/dev/null 2>&1
  fi
  if [[ -n "${temporary_directory}" ]]; then
    rm -rf "${temporary_directory}"
  fi
}

trap cleanup EXIT INT TERM

if [[ "$(uname -s)" != Darwin ]]; then
  fail "this automated example currently supports the Homebrew macOS gateway"
fi

for command_name in brew docker openshell python3 uv; do
  require_command "${command_name}"
done

if [[ -e "${GATEWAY_BACKUP}" || -e "${GATEWAY_ABSENT_MARKER}" ]]; then
  fail "stale gateway recovery files exist; follow README.md recovery instructions"
fi

host_address="${PRIVACY_GUARD_E2E_HOST_ADDRESS:-$(ipconfig getifaddr en0 || true)}"
[[ "${host_address}" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || \
  fail "set PRIVACY_GUARD_E2E_HOST_ADDRESS to a host IPv4 address reachable from Docker"
docker image inspect "${SANDBOX_IMAGE}" >/dev/null 2>&1 || \
  fail "sandbox image is not local; pull ${SANDBOX_IMAGE} or set PRIVACY_GUARD_E2E_SANDBOX_IMAGE"

temporary_directory="$(mktemp -d)"
capture_file="${temporary_directory}/captured-request.json"
middleware_log="${temporary_directory}/middleware.log"
capture_log="${temporary_directory}/capture.log"

log "starting Privacy Guard and capture endpoint on ${host_address}"
uv run --project "${PYTHON_PROJECT}" python "${SCRIPT_DIR}/middleware_server.py" \
  --listen "0.0.0.0:${MIDDLEWARE_PORT}" >"${middleware_log}" 2>&1 &
middleware_pid=$!
python3 "${SCRIPT_DIR}/capture_server.py" --port "${CAPTURE_PORT}" \
  --output "${capture_file}" >"${capture_log}" 2>&1 &
capture_pid=$!

wait_for_tcp_port "${MIDDLEWARE_PORT}" || {
  tail -n 40 "${middleware_log}" >&2
  fail "Privacy Guard did not start"
}
wait_for_tcp_port "${CAPTURE_PORT}" || {
  tail -n 40 "${capture_log}" >&2
  fail "capture server did not start"
}

mkdir -p "$(dirname "${GATEWAY_CONFIG}")"
if [[ -f "${GATEWAY_CONFIG}" ]]; then
  grep -q 'name = "privacy-guard-e2e"' "${GATEWAY_CONFIG}" && \
    fail "gateway already contains a privacy-guard-e2e registration"
  cp -p "${GATEWAY_CONFIG}" "${GATEWAY_BACKUP}"
else
  touch "${GATEWAY_ABSENT_MARKER}"
  : >"${GATEWAY_CONFIG}"
fi
gateway_config_changed=true

cat >>"${GATEWAY_CONFIG}" <<EOF

[[openshell.supervisor.middleware]]
name = "privacy-guard-e2e"
grpc_endpoint = "http://${host_address}:${MIDDLEWARE_PORT}"
max_body_bytes = 4194304
timeout = "5s"
EOF

log "restarting OpenShell with temporary middleware registration"
brew services restart openshell >/dev/null
wait_for_gateway || {
  tail -n 80 /opt/homebrew/var/log/openshell/openshell-gateway.err.log >&2 || true
  fail "OpenShell gateway did not accept the middleware registration"
}

request_body='{"messages":[{"role":"user","content":"hello user@example.com"}],"model":"capture-model"}'
log "creating disposable sandbox and sending provider-shaped request"
openshell sandbox create \
  --name "${SANDBOX_NAME}" \
  --from "${SANDBOX_IMAGE}" \
  --no-auto-providers \
  --policy "${SCRIPT_DIR}/policy.yaml" \
  --no-keep \
  --no-tty \
  -- \
  curl --fail-with-body --silent --show-error \
    --request POST \
    --header 'content-type: application/json' \
    --data "${request_body}" \
    "http://host.openshell.internal:${CAPTURE_PORT}/v1/chat/completions"

for attempt in $(seq 1 40); do
  [[ -f "${capture_file}" ]] && break
  sleep 0.25
done
[[ -f "${capture_file}" ]] || fail "capture endpoint did not receive the request"

python3 - "${capture_file}" "${EXPECTED_TEXT}" <<'PY'
import json
import pathlib
import sys

captured = json.loads(pathlib.Path(sys.argv[1]).read_bytes())
actual = captured["messages"][0]["content"]
if actual != sys.argv[2]:
    raise SystemExit(f"unexpected reconstructed content: {actual!r}")
if captured["model"] != "capture-model":
    raise SystemExit("untouched provider-shaped field changed")
PY

log "SUCCESS: OpenShell forwarded reconstructed content: ${EXPECTED_TEXT}"
