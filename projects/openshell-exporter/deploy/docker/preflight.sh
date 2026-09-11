#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

if [[ -f .env ]]; then
  set -a
  source ./.env
  set +a
fi

fail() { printf 'preflight: %s\n' "$*" >&2; exit 1; }
require_var() {
  local name=$1 value
  value=${!name:-}
  [[ -n "$value" ]] || fail "$name is required"
}
require_dir() {
  [[ -d "$1" && -r "$1" ]] || fail "directory is not readable: $1"
}
require_file() {
  [[ -f "$1" && -r "$1" && -s "$1" ]] || fail "required file is missing, empty, or unreadable: $1"
}
require_mtls_pair() {
  local enabled=$1 directory=$2 label=$3
  case "$enabled" in
    true)
      require_file "$directory/tls.crt"
      require_file "$directory/tls.key"
      ;;
    false) ;;
    *) fail "$label must be true or false" ;;
  esac
}
require_https() {
  [[ "$2" == https://* ]] || fail "$1 must use HTTPS"
}

profile=${DOCKER_PROFILE:-core}
case "$profile" in
  core)
    source_config=${EXPORTER_CONFIG_FILE:-./config.yaml}
    compose=(docker compose --env-file .env -f compose.yaml)
    ;;
  full)
    source_config=${EXPORTER_CONFIG_FILE:-./config.full.yaml}
    compose=(docker compose --env-file .env -f compose.yaml -f compose.full.yaml)
    ;;
  complete)
    source_config=${EXPORTER_CONFIG_FILE:-./config.complete.yaml}
    compose=(docker compose --env-file .env -f compose.yaml -f compose.full.yaml -f compose.complete.yaml)
    ;;
  *) fail "DOCKER_PROFILE must be core, full, or complete" ;;
esac

runtime_config_dir=${DOCKER_RUNTIME_CONFIG_DIR:-./.runtime}
install -d -m 0750 "$runtime_config_dir"
runtime_config="$runtime_config_dir/$profile.yaml"
if [[ $(realpath "$source_config") != $(realpath -m "$runtime_config") ]]; then
  install -m 0444 "$source_config" "$runtime_config"
fi
export EXPORTER_CONFIG_FILE
EXPORTER_CONFIG_FILE=$(realpath "$runtime_config")

case ${SOURCE_BEARER_ENABLED:-true} in
  true) export OPENSHELL_TOKEN_FILE_INTERNAL=/run/secrets/source/token ;;
  false) export OPENSHELL_TOKEN_FILE_INTERNAL= ;;
  *) fail "SOURCE_BEARER_ENABLED must be true or false" ;;
esac

for name in OPENSHELL_EXPORTER_IMAGE OPENSHELL_ENDPOINT OPENSHELL_GATEWAY_ID   OPENSHELL_LOG_DIR SOURCE_SECRETS_DIR OPENSHELL_EXPORT_URL   DESTINATION_SECRETS_DIR CHECKPOINT_DIR QUEUE_DIR RECOVERY_DIR; do
  require_var "$name"
done
require_https "OpenShell endpoint" "$OPENSHELL_ENDPOINT"
require_https "CloudEvents destination" "$OPENSHELL_EXPORT_URL"
require_dir "$OPENSHELL_LOG_DIR"
require_dir "$SOURCE_SECRETS_DIR"
require_dir "$DESTINATION_SECRETS_DIR"
require_file "$SOURCE_SECRETS_DIR/ca.pem"
if [[ ${SOURCE_BEARER_ENABLED:-true} == true ]]; then
  require_file "$SOURCE_SECRETS_DIR/token"
fi
require_file "$DESTINATION_SECRETS_DIR/token"
require_file "$DESTINATION_SECRETS_DIR/ca.pem"
[[ $(wc -c <"$DESTINATION_SECRETS_DIR/token") -ge 32 ]] ||
  fail 'destination bearer secret must be at least 32 bytes'
if [[ ${SOURCE_BEARER_ENABLED:-true} == true ]]; then
  cmp -s "$SOURCE_SECRETS_DIR/token" "$DESTINATION_SECRETS_DIR/token" &&
    fail 'source and destination credentials must be distinct'
fi
require_mtls_pair "${SOURCE_MTLS_ENABLED:-false}" "$SOURCE_SECRETS_DIR" SOURCE_MTLS_ENABLED
if [[ ${SOURCE_BEARER_ENABLED:-true} == false && ${SOURCE_MTLS_ENABLED:-false} == false ]]; then
  fail 'enable source bearer authentication, source mTLS, or both'
fi
require_mtls_pair "${DESTINATION_MTLS_ENABLED:-false}" "$DESTINATION_SECRETS_DIR" DESTINATION_MTLS_ENABLED
if [[ ${SOURCE_MTLS_ENABLED:-false} == true ]]; then
  export OPENSHELL_CLIENT_CERT_FILE_INTERNAL=/run/secrets/source/tls.crt
  export OPENSHELL_CLIENT_KEY_FILE_INTERNAL=/run/secrets/source/tls.key
else
  export OPENSHELL_CLIENT_CERT_FILE_INTERNAL=
  export OPENSHELL_CLIENT_KEY_FILE_INTERNAL=
fi
if [[ ${DESTINATION_MTLS_ENABLED:-false} == true ]]; then
  export DESTINATION_CLIENT_CERT_FILE_INTERNAL=/run/secrets/destination/tls.crt
  export DESTINATION_CLIENT_KEY_FILE_INTERNAL=/run/secrets/destination/tls.key
else
  export DESTINATION_CLIENT_CERT_FILE_INTERNAL=
  export DESTINATION_CLIENT_KEY_FILE_INTERNAL=
fi

persistent_dirs=("$CHECKPOINT_DIR" "$QUEUE_DIR" "$RECOVERY_DIR")
if [[ "$profile" == full || "$profile" == complete ]]; then
  for name in NEMO_RELAY_LOG_DIR NEMO_RELAY_INPUT_SECRETS_DIR     OTLP_DESTINATION_SECRETS_DIR OTLP_GRPC_ENDPOINT OTLP_HTTP_ENDPOINT     OTLP_GRPC_QUEUE_DIR OTLP_HTTP_QUEUE_DIR; do
    require_var "$name"
  done
  require_https "OTLP HTTP destination" "$OTLP_HTTP_ENDPOINT"
  require_dir "$NEMO_RELAY_LOG_DIR"
  require_dir "$NEMO_RELAY_INPUT_SECRETS_DIR"
  require_dir "$OTLP_DESTINATION_SECRETS_DIR"
  for name in token tls.crt tls.key client-ca.pem; do
    require_file "$NEMO_RELAY_INPUT_SECRETS_DIR/$name"
  done
  require_file "$OTLP_DESTINATION_SECRETS_DIR/token"
  require_file "$OTLP_DESTINATION_SECRETS_DIR/ca.pem"
  [[ $(wc -c <"$NEMO_RELAY_INPUT_SECRETS_DIR/token") -ge 32 ]] ||
    fail 'Relay input bearer secret must be at least 32 bytes'
  [[ $(wc -c <"$OTLP_DESTINATION_SECRETS_DIR/token") -ge 32 ]] ||
    fail 'OTLP destination bearer secret must be at least 32 bytes'
  source_credentials=("$DESTINATION_SECRETS_DIR/token")
  if [[ ${SOURCE_BEARER_ENABLED:-true} == true ]]; then
    source_credentials+=("$SOURCE_SECRETS_DIR/token")
  fi
  for left in "${source_credentials[@]}"; do
    cmp -s "$left" "$NEMO_RELAY_INPUT_SECRETS_DIR/token" &&
      fail 'Relay input credential must have a distinct trust boundary'
    cmp -s "$left" "$OTLP_DESTINATION_SECRETS_DIR/token" &&
      fail 'OTLP destination credential must have a distinct trust boundary'
  done
  cmp -s "$NEMO_RELAY_INPUT_SECRETS_DIR/token" "$OTLP_DESTINATION_SECRETS_DIR/token" &&
    fail 'Relay input and OTLP destination credentials must be distinct'
  require_mtls_pair "${OTLP_DESTINATION_MTLS_ENABLED:-false}"     "$OTLP_DESTINATION_SECRETS_DIR" OTLP_DESTINATION_MTLS_ENABLED
  if [[ ${OTLP_DESTINATION_MTLS_ENABLED:-false} == true ]]; then
    export OTLP_CLIENT_CERT_FILE_INTERNAL=/run/secrets/otlp-destination/tls.crt
    export OTLP_CLIENT_KEY_FILE_INTERNAL=/run/secrets/otlp-destination/tls.key
  else
    export OTLP_CLIENT_CERT_FILE_INTERNAL=
    export OTLP_CLIENT_KEY_FILE_INTERNAL=
  fi
  persistent_dirs+=("$OTLP_GRPC_QUEUE_DIR" "$OTLP_HTTP_QUEUE_DIR")
fi
if [[ "$profile" == complete ]]; then
  for name in OPENSHELL_NATIVE_OTLP_INPUT_SECRETS_DIR FORWARDED_OCSF_INPUT_SECRETS_DIR; do
    require_var "$name"
  done
  case "${OPENSHELL_NATIVE_OTLP_PUBLISH_ADDRESS:-127.0.0.1}" in
    0.0.0.0|::|"[::]") fail "native OpenShell OTLP must not publish on an unauthenticated wildcard address" ;;
  esac
  require_dir "$OPENSHELL_NATIVE_OTLP_INPUT_SECRETS_DIR"
  require_file "$OPENSHELL_NATIVE_OTLP_INPUT_SECRETS_DIR/tls.crt"
  require_file "$OPENSHELL_NATIVE_OTLP_INPUT_SECRETS_DIR/tls.key"
  require_dir "$FORWARDED_OCSF_INPUT_SECRETS_DIR"
  for name in token tls.crt tls.key client-ca.pem; do
    require_file "$FORWARDED_OCSF_INPUT_SECRETS_DIR/$name"
  done
  [[ $(wc -c <"$FORWARDED_OCSF_INPUT_SECRETS_DIR/token") -ge 32 ]] ||
    fail "forwarded OCSF bearer secret must be at least 32 bytes"
  input_credentials=("$DESTINATION_SECRETS_DIR/token" \
    "$NEMO_RELAY_INPUT_SECRETS_DIR/token" "$OTLP_DESTINATION_SECRETS_DIR/token")
  if [[ ${SOURCE_BEARER_ENABLED:-true} == true ]]; then
    input_credentials+=("$SOURCE_SECRETS_DIR/token")
  fi
  for token in "${input_credentials[@]}"; do
    cmp -s "$token" "$FORWARDED_OCSF_INPUT_SECRETS_DIR/token" &&
      fail "forwarded OCSF credential must have a distinct trust boundary"
  done
fi

for path in "${persistent_dirs[@]}"; do
  [[ -d "$path" ]] || fail "persistent directory does not exist: $path"
done
for ((i=0; i<${#persistent_dirs[@]}; i++)); do
  for ((j=i+1; j<${#persistent_dirs[@]}; j++)); do
    [[ "${persistent_dirs[i]}" != "${persistent_dirs[j]}" ]] ||
      fail 'every checkpoint, queue, and recovery directory must be distinct'
  done
done
[[ -f "$EXPORTER_CONFIG_FILE" && -r "$EXPORTER_CONFIG_FILE" ]] ||
  fail "Collector config is unreadable: $EXPORTER_CONFIG_FILE"

minimum_free_kib=${MIN_FREE_KIB:-1048576}
capacity_paths=("$OPENSHELL_LOG_DIR" "${persistent_dirs[@]}")
[[ "$profile" == core ]] || capacity_paths+=("$NEMO_RELAY_LOG_DIR")
for capacity_path in "${capacity_paths[@]}"; do
  available_kib=$(df -Pk "$capacity_path" | awk 'NR == 2 {print $4}')
  [[ -n "$available_kib" && "$available_kib" -ge "$minimum_free_kib" ]] ||
    fail "less than ${minimum_free_kib} KiB free at $capacity_path"
done

docker info >/dev/null 2>&1 || fail 'Docker daemon is not reachable'
docker compose version >/dev/null 2>&1 || fail 'Docker Compose v2 is required'
"$(dirname -- "${BASH_SOURCE[0]}")/../../scripts/prepare-image.sh" docker "$OPENSHELL_EXPORTER_IMAGE" "${OPENSHELL_EXPORTER_PULL_POLICY:-never}"
docker_run=(docker run --pull=never --rm --network none --read-only --user 65532:65532
  --cap-drop ALL --security-opt no-new-privileges
  -v "$CHECKPOINT_DIR:/checkpoints"
  -v "$QUEUE_DIR:/cloudevents-queue"
  -v "$RECOVERY_DIR:/recovery")
writable=(/checkpoints /cloudevents-queue /recovery)
if [[ "$profile" == full || "$profile" == complete ]]; then
  docker_run+=(-v "$OTLP_GRPC_QUEUE_DIR:/otlp-grpc-queue"
    -v "$OTLP_HTTP_QUEUE_DIR:/otlp-http-queue")
  writable+=(/otlp-grpc-queue /otlp-http-queue)
fi
"${docker_run[@]}" --entrypoint /usr/local/bin/healthcheck   "$OPENSHELL_EXPORTER_IMAGE" --writable "${writable[@]}" >/dev/null ||
  fail 'persistent directories are not writable by exporter UID 65532'
"${compose[@]}" config --quiet || fail 'Compose configuration is invalid'
"${compose[@]}" run --rm --no-deps exporter   validate --config /etc/openshell-event-exporter/config.yaml >/dev/null ||
  fail 'Collector configuration validation failed'
printf 'preflight: %s profile has a validated local or digest-pinned image, TLS/auth boundaries, writable persistence, source allow-lists, Compose rendering, and Collector config validation\n' "$profile"
