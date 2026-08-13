#!/usr/bin/env bash
set -euo pipefail
umask 077

runner_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
profile_resolver=(uv run --locked --script "$runner_dir/profile_resolver.py")

usage() {
  cat >&2 <<'EOF'
usage: run.sh --profile NAME --task NAME [OPTIONS]

Options:
  --output PATH             Write the final JSON object to PATH (default: stdout)
  --guidance PATH           Add a trusted guidance file (repeatable)
  --gateway-endpoint URL    Use an existing local OpenShell gateway
  --openshell-bin PATH      OpenShell CLI for an existing gateway
  --prepare-only DIR        Stage and validate without launching
  --timeout-seconds N       Sandbox invocation timeout (default: 1200)
EOF
}

profile=""
task=""
output="-"
gateway_endpoint=""
openshell_override=""
prepare_only=""
timeout_seconds=1200
guidance=()
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --profile) profile="${2:-}"; shift 2 ;;
    --task) task="${2:-}"; shift 2 ;;
    --output) output="${2:-}"; shift 2 ;;
    --guidance) guidance+=("${2:-}"); shift 2 ;;
    --gateway-endpoint) gateway_endpoint="${2:-}"; shift 2 ;;
    --openshell-bin) openshell_override="${2:-}"; shift 2 ;;
    --prepare-only) prepare_only="${2:-}"; shift 2 ;;
    --timeout-seconds) timeout_seconds="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "repository agents: unknown argument: $1" >&2; usage; exit 2 ;;
  esac
done
if [[ -z "$profile" || -z "$task" || ! "$timeout_seconds" =~ ^[1-9][0-9]*$ ]]; then
  usage
  exit 2
fi
if [[ -n "$openshell_override" && -z "$gateway_endpoint" ]]; then
  echo "repository agents: --openshell-bin requires --gateway-endpoint" >&2
  exit 2
fi

temp_parent="${RUNNER_TEMP:-/tmp}"
work_root="$(mktemp -d "$temp_parent/repository-agent.XXXXXX")"
cleanup() {
  if [[ -n "${gateway_pid:-}" ]]; then
    kill "$gateway_pid" 2>/dev/null || true
    wait "$gateway_pid" 2>/dev/null || true
  fi
  rm -rf "$work_root"
}
trap cleanup EXIT

model_id="${MODEL_ID_TOP:-}"
if [[ -n "$prepare_only" && -z "$model_id" ]]; then
  model_id=prepare-only-model
fi
if [[ -z "$model_id" ]]; then
  echo "repository agents: MODEL_ID_TOP must be set" >&2
  exit 2
fi
stage="$work_root/staged"
prepare_arguments=(
  prepare --runner-root "$runner_dir" --profile "$profile" --task "$task"
  --destination "$stage" --model-id "$model_id"
)
for path in "${guidance[@]}"; do
  prepare_arguments+=(--guidance "$path")
done
"${profile_resolver[@]}" "${prepare_arguments[@]}"

if [[ -n "$prepare_only" ]]; then
  if [[ -e "$prepare_only" || -L "$prepare_only" ]]; then
    echo "repository agents: --prepare-only destination already exists: $prepare_only" >&2
    exit 2
  fi
  mkdir -p "$(dirname "$prepare_only")"
  cp -R "$stage" "$prepare_only"
  exit 0
fi

provider_config_file="$work_root/provider-config"
"${profile_resolver[@]}" values "$stage/resolved.json" \
  provider.id provider.type provider.base_url_source_env provider.api_key_source_env \
  provider.base_url_config_key \
  >"$provider_config_file"
mapfile -t provider_config <"$provider_config_file"
provider_name="${provider_config[0]}"
provider_type="${provider_config[1]}"
base_source_env="${provider_config[2]}"
key_source_env="${provider_config[3]}"
base_url_config_key="${provider_config[4]}"
base_url="${!base_source_env:-}"
api_key="${!key_source_env:-}"
if [[ -n "$base_url" || -n "$api_key" ]]; then
  if [[ -z "$base_url" || -z "$api_key" ]]; then
    echo "repository agents: both $base_source_env and $key_source_env are required" >&2
    exit 2
  fi
  "${profile_resolver[@]}" validate-url "$base_url"
fi

if [[ -z "$gateway_endpoint" ]]; then
  runtime_root="$work_root/openshell"
  bin_dir="$runtime_root/bin"
  mkdir -p "$bin_dir" "$runtime_root/config" "$runtime_root/state"
  download_release_binary() {
    local archive_name="$1"
    local expected_sha256="$2"
    local binary_name="$3"
    local archive_path="$runtime_root/$archive_name"
    curl --fail --silent --show-error --location \
      "https://github.com/NVIDIA/OpenShell/releases/download/v0.0.104/$archive_name" \
      --output "$archive_path"
    printf '%s  %s\n' "$expected_sha256" "$archive_path" | sha256sum --check --status
    tar --extract --gzip --file "$archive_path" --directory "$bin_dir" "$binary_name"
    chmod 0755 "$bin_dir/$binary_name"
  }
  download_release_binary openshell-x86_64-unknown-linux-musl.tar.gz \
    a8db262ad9af996a3d9203fcc3d7ba90fe2c6cb0f3fffc9b7d1b9b44cd27df22 openshell
  download_release_binary openshell-gateway-x86_64-unknown-linux-gnu.tar.gz \
    577fc5cb9ef64bc0c1afc9b8fc85cdfe9ef546ebf40794085b31d78200928bb1 openshell-gateway
  download_release_binary openshell-sandbox-x86_64-unknown-linux-gnu.tar.gz \
    a02d2e5a3d0d7e2a399162b269cc2bf148829851f7469d437ba6ee95c3de7324 openshell-sandbox

  openshell_bin="$bin_dir/openshell"
  gateway_endpoint=https://127.0.0.1:17670
  export XDG_CONFIG_HOME="$runtime_root/config"
  export XDG_STATE_HOME="$runtime_root/state"
  tls_dir="$runtime_root/tls"
  export OPENSHELL_LOCAL_TLS_DIR="$tls_dir"
  "$bin_dir/openshell-gateway" generate-certs \
    --output-dir "$tls_dir" --server-san host.openshell.internal >/dev/null
  gateway_config="$runtime_root/gateway.toml"
  cat >"$gateway_config" <<EOF
[openshell]
version = 1

[openshell.gateway]
compute_drivers = ["docker"]
health_bind_address = "127.0.0.1:17671"

[openshell.gateway.auth]
allow_unauthenticated_users = false

[openshell.drivers.docker]
socket_path = "/var/run/docker.sock"
grpc_endpoint = "https://host.openshell.internal:17670"
supervisor_bin = "$bin_dir/openshell-sandbox"
image_pull_policy = "IfNotPresent"
guest_tls_ca = "$tls_dir/ca.crt"
guest_tls_cert = "$tls_dir/client/tls.crt"
guest_tls_key = "$tls_dir/client/tls.key"
EOF
  "$bin_dir/openshell-gateway" \
    --config "$gateway_config" --bind-address 0.0.0.0 \
    --tls-cert "$tls_dir/server/tls.crt" \
    --tls-key "$tls_dir/server/tls.key" \
    --tls-client-ca "$tls_dir/ca.crt" \
    --enable-mtls-auth true \
    --port 17670 >"$runtime_root/gateway.log" 2>&1 &
  gateway_pid="$!"
  for _ in {1..60}; do
    if curl --fail --silent http://127.0.0.1:17671/readyz >/dev/null; then break; fi
    if ! kill -0 "$gateway_pid" 2>/dev/null; then
      cat "$runtime_root/gateway.log" >&2
      exit 1
    fi
    sleep 1
  done
  curl --fail --silent http://127.0.0.1:17671/readyz >/dev/null
  "$openshell_bin" gateway add "$gateway_endpoint" --local --name openshell >/dev/null
  export OPENSHELL_GATEWAY=openshell
else
  openshell_bin="${openshell_override:-$(command -v openshell || true)}"
  if [[ -z "$openshell_bin" || ! -x "$openshell_bin" ]]; then
    echo "repository agents: an executable openshell CLI is required for --gateway-endpoint" >&2
    exit 2
  fi
fi

export OPENSHELL_GATEWAY_ENDPOINT="$gateway_endpoint"
"$openshell_bin" settings set --global --key providers_v2_enabled --value true --yes >/dev/null
if [[ -n "$base_url" ]]; then
  if "$openshell_bin" provider get "$provider_name" >/dev/null 2>&1; then
    "$openshell_bin" provider update "$provider_name" \
      --credential "$key_source_env" \
      --config "$base_url_config_key=$base_url" >/dev/null
  else
    "$openshell_bin" provider create \
      --name "$provider_name" --type "$provider_type" \
      --credential "$key_source_env" \
      --config "$base_url_config_key=$base_url" >/dev/null
  fi
elif ! "$openshell_bin" provider get "$provider_name" >/dev/null 2>&1; then
  echo "repository agents: provider '$provider_name' is not configured on the selected gateway" >&2
  exit 2
fi
"$openshell_bin" inference set \
  --provider "$provider_name" --model "$model_id" --timeout "$timeout_seconds" >/dev/null

unset "$base_source_env" "$key_source_env"
resource_args_file="$work_root/resource-args"
"${profile_resolver[@]}" resource-args "$stage/resolved.json" >"$resource_args_file"
mapfile -d '' -t resource_args <"$resource_args_file"
response="$work_root/response.json"
sandbox_name="agent-${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-1}-${profile}-${task}"
timeout "$timeout_seconds" "$openshell_bin" sandbox create \
  --name "$sandbox_name" \
  --from "$stage/sandbox" \
  --upload "$stage/workspace:/sandbox/task" \
  --no-git-ignore \
  --no-keep \
  --no-tty \
  --provider "$provider_name" \
  --no-auto-providers \
  -- bash /etc/openshell/agent-payload/runtime/exec.sh \
  /sandbox/task/prompt.md "$model_id" "${resource_args[@]}" \
  >"$response"

"${profile_resolver[@]}" validate-response "$response" "$stage/response.schema.json"
if [[ "$output" == "-" ]]; then
  cat "$response"
else
  if [[ -L "$output" ]]; then
    echo "repository agents: output must not be a symlink" >&2
    exit 2
  fi
  mkdir -p "$(dirname "$output")"
  cp "$response" "$output"
fi
