#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/podman"

fail() { printf 'podman deployment: %s\n' "$*" >&2; exit 1; }
require() { command -v "$1" >/dev/null 2>&1 || fail "missing required command: $1"; }
required() {
  local name=$1
  [[ -n "${!name:-}" ]] || fail "$name is required"
}
require_dir() {
  [[ -d "$1" && -r "$1" ]] || fail "directory is not readable: $1"
}
require_file() {
  [[ -f "$1" && -r "$1" && -s "$1" ]] || fail "required file is missing, empty, or unreadable: $1"
}

validate_resource_controls() {
  local probe_name="${EXPORTER_CONTAINER_NAME:-openshell-event-exporter}-resource-probe-$$"
  local probe_log
  probe_log=$(mktemp)
  "$ROOT/../scripts/prepare-image.sh" podman "$OPENSHELL_EXPORTER_IMAGE" "${OPENSHELL_EXPORTER_PULL_POLICY:-never}"
  if podman run --rm --name "$probe_name" --pull=never --network=none \
    --userns=keep-id:uid=65532,gid=65532 --read-only --cap-drop=all \
    --security-opt=no-new-privileges --pids-limit="${EXPORTER_PIDS_LIMIT:-256}" \
    --cpus="${EXPORTER_CPUS:-2}" --memory="${EXPORTER_MEMORY:-768m}" \
    "$OPENSHELL_EXPORTER_IMAGE" --version >"$probe_log" 2>&1; then
    rm -f "$probe_log"
    return
  fi
  podman rm -f "$probe_name" >/dev/null 2>&1 || true
  rm -f "$probe_log"
  fail "rootless Podman cannot enforce CPU, memory, and PID limits; use Podman 5.x with a delegated cgroup v2 user session (for example, administrator-enabled loginctl linger), then log in again"
}

load_env() {
  [[ -f .env ]] || fail "copy .env.example to deploy/podman/.env and edit it"
  set -a
  source ./.env
  set +a
}

profile_config() {
  case "${PODMAN_PROFILE:-core}" in
    core) printf '%s' config.yaml ;;
    full) printf '%s' config.full.yaml ;;
    complete) printf '%s' config.complete.yaml ;;
    *) fail "PODMAN_PROFILE must be core, full, or complete" ;;
  esac
}

validate() {
  load_env
  require podman
  require awk
  require cmp
  require df
  require wc
  podman info >/dev/null || fail "Podman is not reachable"

  local profile=${PODMAN_PROFILE:-core}
  local config
  config=$(profile_config)
  require_file "$ROOT/podman/$config"

  local name
  for name in OPENSHELL_EXPORTER_IMAGE OPENSHELL_ENDPOINT OPENSHELL_GATEWAY_ID \
    OPENSHELL_LOG_DIR CHECKPOINT_DIR QUEUE_DIR RECOVERY_DIR \
    SOURCE_CA_FILE OPENSHELL_EXPORT_URL DESTINATION_TOKEN_FILE DESTINATION_CA_FILE; do
    required "$name"
  done
  validate_resource_controls
  [[ "$OPENSHELL_ENDPOINT" == https://* ]] ||
    fail "OpenShell endpoint must use HTTPS"
  [[ "$OPENSHELL_EXPORT_URL" == https://* ]] ||
    fail "destination endpoint must use HTTPS"
  require_dir "$OPENSHELL_LOG_DIR"

  local persistent_paths=("$CHECKPOINT_DIR" "$QUEUE_DIR" "$RECOVERY_DIR")
  local credential_files=("$DESTINATION_TOKEN_FILE")
  local path
  for path in "${persistent_paths[@]}"; do
    install -d -m 0700 "$path"
  done

  for path in "$SOURCE_CA_FILE" \
    "$DESTINATION_TOKEN_FILE" "$DESTINATION_CA_FILE"; do
    require_file "$path"
  done
  case ${SOURCE_BEARER_ENABLED:-true} in
    true)
      required SOURCE_TOKEN_FILE
      require_file "$SOURCE_TOKEN_FILE"
      credential_files+=("$SOURCE_TOKEN_FILE")
      ;;
    false) ;;
    *) fail "SOURCE_BEARER_ENABLED must be true or false" ;;
  esac
  (( $(wc -c <"$DESTINATION_TOKEN_FILE") >= 32 )) ||
    fail "destination bearer token must contain at least 32 bytes"
  if [[ ${SOURCE_BEARER_ENABLED:-true} == true ]]; then
    cmp -s "$SOURCE_TOKEN_FILE" "$DESTINATION_TOKEN_FILE" &&
      fail "source and destination credentials must differ"
  fi

  local cert_var key_var cert key
  for cert_var in OPENSHELL_CLIENT_CERT_FILE DESTINATION_CLIENT_CERT_FILE; do
    key_var=${cert_var/CERT/KEY}
    cert=${!cert_var:-}
    key=${!key_var:-}
    if [[ -n "$cert$key" ]]; then
      require_file "$cert"
      require_file "$key"
    fi
  done
  if [[ ${SOURCE_BEARER_ENABLED:-true} == false && -z ${OPENSHELL_CLIENT_CERT_FILE:-}${OPENSHELL_CLIENT_KEY_FILE:-} ]]; then
    fail "enable source bearer authentication, source mTLS, or both"
  fi

  if [[ "$profile" == full || "$profile" == complete ]]; then
    for name in NEMO_RELAY_LOG_DIR NEMO_RELAY_INPUT_SECRETS_DIR \
      OTLP_DESTINATION_SECRETS_DIR OTLP_GRPC_ENDPOINT OTLP_HTTP_ENDPOINT \
      OTLP_GRPC_QUEUE_DIR OTLP_HTTP_QUEUE_DIR NEMO_RELAY_OTLP_GRPC_LISTEN \
      NEMO_RELAY_OTLP_HTTP_LISTEN; do
      required "$name"
    done
    [[ "$OTLP_HTTP_ENDPOINT" == https://* ]] ||
      fail "OTLP HTTP destination must use HTTPS"
    require_dir "$NEMO_RELAY_LOG_DIR"
    require_dir "$NEMO_RELAY_INPUT_SECRETS_DIR"
    require_dir "$OTLP_DESTINATION_SECRETS_DIR"
    for name in token tls.crt tls.key client-ca.pem; do
      require_file "$NEMO_RELAY_INPUT_SECRETS_DIR/$name"
    done
    require_file "$OTLP_DESTINATION_SECRETS_DIR/token"
    require_file "$OTLP_DESTINATION_SECRETS_DIR/ca.pem"
    (( $(wc -c <"$NEMO_RELAY_INPUT_SECRETS_DIR/token") >= 32 )) ||
      fail "Relay input bearer token must contain at least 32 bytes"
    (( $(wc -c <"$OTLP_DESTINATION_SECRETS_DIR/token") >= 32 )) ||
      fail "OTLP destination bearer token must contain at least 32 bytes"
    credential_files+=("$NEMO_RELAY_INPUT_SECRETS_DIR/token" "$OTLP_DESTINATION_SECRETS_DIR/token")
    persistent_paths+=("$OTLP_GRPC_QUEUE_DIR" "$OTLP_HTTP_QUEUE_DIR")
    install -d -m 0700 "$OTLP_GRPC_QUEUE_DIR" "$OTLP_HTTP_QUEUE_DIR"
    case "${OTLP_DESTINATION_MTLS_ENABLED:-false}" in
      true)
        require_file "$OTLP_DESTINATION_SECRETS_DIR/tls.crt"
        require_file "$OTLP_DESTINATION_SECRETS_DIR/tls.key"
        ;;
      false) ;;
      *) fail "OTLP_DESTINATION_MTLS_ENABLED must be true or false" ;;
    esac
  fi

  if [[ "$profile" == complete ]]; then
    for name in OPENSHELL_NATIVE_OTLP_INPUT_SECRETS_DIR \
      OPENSHELL_NATIVE_OTLP_GRPC_LISTEN FORWARDED_OCSF_INPUT_SECRETS_DIR \
      FORWARDED_OCSF_OTLP_HTTP_LISTEN; do
      required "$name"
    done
    [[ "$OPENSHELL_NATIVE_OTLP_GRPC_LISTEN" != 0.0.0.0:* &&
       "$OPENSHELL_NATIVE_OTLP_GRPC_LISTEN" != \[::\]:* ]] ||
      fail "native OpenShell OTLP must not bind an unauthenticated wildcard address"
    require_dir "$OPENSHELL_NATIVE_OTLP_INPUT_SECRETS_DIR"
    require_file "$OPENSHELL_NATIVE_OTLP_INPUT_SECRETS_DIR/tls.crt"
    require_file "$OPENSHELL_NATIVE_OTLP_INPUT_SECRETS_DIR/tls.key"
    require_dir "$FORWARDED_OCSF_INPUT_SECRETS_DIR"
    for name in token tls.crt tls.key client-ca.pem; do
      require_file "$FORWARDED_OCSF_INPUT_SECRETS_DIR/$name"
    done
    (( $(wc -c <"$FORWARDED_OCSF_INPUT_SECRETS_DIR/token") >= 32 )) ||
      fail "forwarded OCSF bearer token must contain at least 32 bytes"
    credential_files+=("$FORWARDED_OCSF_INPUT_SECRETS_DIR/token")
  fi

  local i j
  for ((i=0; i<${#persistent_paths[@]}; i++)); do
    for ((j=i+1; j<${#persistent_paths[@]}; j++)); do
      [[ "${persistent_paths[i]}" != "${persistent_paths[j]}" ]] ||
        fail "every checkpoint, queue, and recovery path must be distinct"
    done
  done
  for ((i=0; i<${#credential_files[@]}; i++)); do
    for ((j=i+1; j<${#credential_files[@]}; j++)); do
      cmp -s "${credential_files[i]}" "${credential_files[j]}" &&
        fail "source, input, and destination credentials must be distinct"
    done
  done

  local capacity_paths=("$OPENSHELL_LOG_DIR" "${persistent_paths[@]}")
  [[ "$profile" == core ]] || capacity_paths+=("$NEMO_RELAY_LOG_DIR")
  local minimum_free_kib=${MIN_FREE_KIB:-1048576}
  for path in "${capacity_paths[@]}"; do
    local available
    available=$(df -Pk "$path" | awk 'NR == 2 {print $4}')
    (( available >= minimum_free_kib )) ||
      fail "less than ${minimum_free_kib} KiB free at $path"
  done
  printf 'podman deployment: %s profile configuration, credentials, persistence, and runtime are valid\n' "$profile"
}

container_name() { printf '%s' "${EXPORTER_CONTAINER_NAME:-openshell-event-exporter}"; }

run_up() {
  validate
  local name profile config
  name=$(container_name)
  profile=${PODMAN_PROFILE:-core}
  config=$(profile_config)
  if podman container exists "$name"; then
    fail "container $name already exists; run down before replacing it"
  fi

  local optional_mounts=()
  local source_auth_mounts=()
  local profile_env=()
  local profile_mounts=()
  local otlp_client_cert_internal= otlp_client_key_internal=
  local source_token_internal=
  if [[ ${SOURCE_BEARER_ENABLED:-true} == true ]]; then
    source_token_internal=/run/secrets/source-token
    source_auth_mounts+=( -v "$SOURCE_TOKEN_FILE:/run/secrets/source-token:ro,Z" )
  fi
  if [[ ${OTLP_DESTINATION_MTLS_ENABLED:-false} == true ]]; then
    otlp_client_cert_internal=/run/secrets/otlp-destination/tls.crt
    otlp_client_key_internal=/run/secrets/otlp-destination/tls.key
  fi
  [[ -z "${OPENSHELL_CLIENT_CERT_FILE:-}" ]] ||
    optional_mounts+=( -v "$OPENSHELL_CLIENT_CERT_FILE:/run/secrets/source-client.pem:ro,Z"
      -v "$OPENSHELL_CLIENT_KEY_FILE:/run/secrets/source-client-key.pem:ro,Z" )
  [[ -z "${DESTINATION_CLIENT_CERT_FILE:-}" ]] ||
    optional_mounts+=( -v "$DESTINATION_CLIENT_CERT_FILE:/run/secrets/destination-client.pem:ro,Z"
      -v "$DESTINATION_CLIENT_KEY_FILE:/run/secrets/destination-client-key.pem:ro,Z" )

  if [[ "$profile" == full || "$profile" == complete ]]; then
    profile_env+=(
      -e 'NEMO_RELAY_LOG_GLOB=/var/log/nemo-relay/*.jsonl'
      -e NEMO_RELAY_OTLP_TOKEN_FILE=/run/secrets/nemo-relay-input/token
      -e NEMO_RELAY_OTLP_TLS_CERT_FILE=/run/secrets/nemo-relay-input/tls.crt
      -e NEMO_RELAY_OTLP_TLS_KEY_FILE=/run/secrets/nemo-relay-input/tls.key
      -e NEMO_RELAY_OTLP_CLIENT_CA_FILE=/run/secrets/nemo-relay-input/client-ca.pem
      -e NEMO_RELAY_OTLP_GRPC_LISTEN -e NEMO_RELAY_OTLP_HTTP_LISTEN
      -e OTLP_DESTINATION_TOKEN_FILE=/run/secrets/otlp-destination/token
      -e OTLP_CA_FILE=/run/secrets/otlp-destination/ca.pem
      -e OTLP_CLIENT_CERT_FILE="$otlp_client_cert_internal"
      -e OTLP_CLIENT_KEY_FILE="$otlp_client_key_internal"
      -e OTLP_GRPC_ENDPOINT -e OTLP_HTTP_ENDPOINT
      -e EXPORTER_OTLP_GRPC_QUEUE_DIR=/var/lib/openshell-exporter/queues/otlp-grpc
      -e EXPORTER_OTLP_HTTP_QUEUE_DIR=/var/lib/openshell-exporter/queues/otlp-http
    )
    profile_mounts+=(
      -v "$NEMO_RELAY_LOG_DIR:/var/log/nemo-relay:ro,Z"
      -v "$NEMO_RELAY_INPUT_SECRETS_DIR:/run/secrets/nemo-relay-input:ro,Z"
      -v "$OTLP_DESTINATION_SECRETS_DIR:/run/secrets/otlp-destination:ro,Z"
      -v "$OTLP_GRPC_QUEUE_DIR:/var/lib/openshell-exporter/queues/otlp-grpc:Z"
      -v "$OTLP_HTTP_QUEUE_DIR:/var/lib/openshell-exporter/queues/otlp-http:Z"
    )
  fi

  if [[ "$profile" == complete ]]; then
    profile_env+=(
      -e OPENSHELL_NATIVE_OTLP_GRPC_LISTEN
      -e OPENSHELL_NATIVE_OTLP_TLS_CERT_FILE=/run/secrets/native-otlp-input/tls.crt
      -e OPENSHELL_NATIVE_OTLP_TLS_KEY_FILE=/run/secrets/native-otlp-input/tls.key
      -e FORWARDED_OCSF_TOKEN_FILE=/run/secrets/forwarded-ocsf-input/token
      -e FORWARDED_OCSF_TLS_CERT_FILE=/run/secrets/forwarded-ocsf-input/tls.crt
      -e FORWARDED_OCSF_TLS_KEY_FILE=/run/secrets/forwarded-ocsf-input/tls.key
      -e FORWARDED_OCSF_CLIENT_CA_FILE=/run/secrets/forwarded-ocsf-input/client-ca.pem
      -e FORWARDED_OCSF_OTLP_HTTP_LISTEN
    )
    profile_mounts+=(
      -v "$OPENSHELL_NATIVE_OTLP_INPUT_SECRETS_DIR:/run/secrets/native-otlp-input:ro,Z"
      -v "$FORWARDED_OCSF_INPUT_SECRETS_DIR:/run/secrets/forwarded-ocsf-input:ro,Z"
    )
  fi

  podman run -d --pull=never --name "$name" --restart=unless-stopped --network=host \
    --userns=keep-id:uid=65532,gid=65532 --read-only --cap-drop=all \
    --security-opt=no-new-privileges --pids-limit=256 \
    --cpus="${EXPORTER_CPUS:-2}" --memory="${EXPORTER_MEMORY:-768m}" \
    --tmpfs /tmp:rw,noexec,nosuid,nodev,size=32m,mode=0700 \
    -e OPENSHELL_ENDPOINT -e OPENSHELL_GATEWAY_ID -e OPENSHELL_WORKSPACE \
    -e OPENSHELL_ALLOW_INSECURE_HTTP=false \
    -e OPENSHELL_SANDBOX_SELECTOR -e OPENSHELL_EXPORT_URL \
    -e OPENSHELL_TOKEN_FILE="$source_token_internal" \
    -e OPENSHELL_CA_FILE=/run/secrets/source-ca.pem \
    -e OPENSHELL_CLIENT_CERT_FILE="${OPENSHELL_CLIENT_CERT_FILE:+/run/secrets/source-client.pem}" \
    -e OPENSHELL_CLIENT_KEY_FILE="${OPENSHELL_CLIENT_KEY_FILE:+/run/secrets/source-client-key.pem}" \
    -e 'OPENSHELL_OCSF_LOG_GLOB=/var/log/openshell/openshell-ocsf.*.log' \
    -e 'OPENSHELL_OPERATIONAL_LOG_GLOB=/var/log/openshell/openshell.*.log' \
    -e DESTINATION_TOKEN_FILE_INTERNAL=/run/secrets/destination-token \
    -e DESTINATION_CA_FILE_INTERNAL=/run/secrets/destination-ca.pem \
    -e DESTINATION_CLIENT_CERT_FILE="${DESTINATION_CLIENT_CERT_FILE:+/run/secrets/destination-client.pem}" \
    -e DESTINATION_CLIENT_KEY_FILE="${DESTINATION_CLIENT_KEY_FILE:+/run/secrets/destination-client-key.pem}" \
    -e EXPORTER_CHECKPOINT_DIR=/var/lib/openshell-exporter/checkpoints \
    -e EXPORTER_QUEUE_DIR=/var/lib/openshell-exporter/queues/cloudevents \
    -e EXPORTER_RECOVERY_FILE=/var/lib/openshell-exporter/recovery/openshell-events.json \
    -e EXPORTER_HEALTH_ENDPOINT=127.0.0.1:13133 -e EXPORTER_METRICS_HOST=127.0.0.1 \
    -e EXPORTER_SOURCE_INSTANCE=podman-gateway \
    -v "$ROOT/podman/$config:/etc/openshell-event-exporter/config.yaml:ro,Z" \
    -v "$OPENSHELL_LOG_DIR:/var/log/openshell:ro,Z" \
    -v "$SOURCE_CA_FILE:/run/secrets/source-ca.pem:ro,Z" \
    -v "$DESTINATION_TOKEN_FILE:/run/secrets/destination-token:ro,Z" \
    -v "$DESTINATION_CA_FILE:/run/secrets/destination-ca.pem:ro,Z" \
    -v "$CHECKPOINT_DIR:/var/lib/openshell-exporter/checkpoints:Z" \
    -v "$QUEUE_DIR:/var/lib/openshell-exporter/queues/cloudevents:Z" \
    -v "$RECOVERY_DIR:/var/lib/openshell-exporter/recovery:Z" \
    "${source_auth_mounts[@]}" "${optional_mounts[@]}" "${profile_env[@]}" "${profile_mounts[@]}" \
    "$OPENSHELL_EXPORTER_IMAGE"
}

action=${1:-}
case "$action" in
  validate) validate ;;
  up) run_up ;;
  down) load_env; podman rm -f "$(container_name)" ;;
  status) load_env; podman ps -a --filter "name=^$(container_name)$" ;;
  logs) load_env; podman logs -f "$(container_name)" ;;
  *) fail "usage: manage.sh validate|up|down|status|logs" ;;
esac
