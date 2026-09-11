#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

[[ -f .env ]] || { printf 'docker deployment: copy .env.example to .env and edit it\n' >&2; exit 1; }
set -a
source ./.env
set +a

case ${SOURCE_BEARER_ENABLED:-true} in
  true) export OPENSHELL_TOKEN_FILE_INTERNAL=/run/secrets/source/token ;;
  false) export OPENSHELL_TOKEN_FILE_INTERNAL= ;;
  *) printf 'docker deployment: SOURCE_BEARER_ENABLED must be true or false\n' >&2; exit 1 ;;
esac

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
if [[ ${OTLP_DESTINATION_MTLS_ENABLED:-false} == true ]]; then
  export OTLP_CLIENT_CERT_FILE_INTERNAL=/run/secrets/otlp-destination/tls.crt
  export OTLP_CLIENT_KEY_FILE_INTERNAL=/run/secrets/otlp-destination/tls.key
else
  export OTLP_CLIENT_CERT_FILE_INTERNAL=
  export OTLP_CLIENT_KEY_FILE_INTERNAL=
fi

compose=(docker compose --env-file .env -f compose.yaml)
case "${DOCKER_PROFILE:-core}" in
  core)
    source_config=${EXPORTER_CONFIG_FILE:-./config.yaml}
    ;;
  full)
    source_config=${EXPORTER_CONFIG_FILE:-./config.full.yaml}
    compose+=(-f compose.full.yaml)
    ;;
  complete)
    source_config=${EXPORTER_CONFIG_FILE:-./config.complete.yaml}
    compose+=(-f compose.full.yaml -f compose.complete.yaml)
    ;;
  *)
    printf 'docker deployment: DOCKER_PROFILE must be core, full, or complete\n' >&2
    exit 1
    ;;
esac

# A restrictive checkout umask can leave tracked YAML readable only by the
# operator. Keep a stable, non-secret runtime copy that UID 65532 can read and
# remains available across Docker restarts.
runtime_config_dir=${DOCKER_RUNTIME_CONFIG_DIR:-./.runtime}
install -d -m 0750 "$runtime_config_dir"
runtime_config="$runtime_config_dir/${DOCKER_PROFILE:-core}.yaml"
install -m 0444 "$source_config" "$runtime_config"
export EXPORTER_CONFIG_FILE
EXPORTER_CONFIG_FILE=$(realpath "$runtime_config")

case "${1:-}" in
  validate) exec ./preflight.sh ;;
  up)
    ./preflight.sh
    exec "${compose[@]}" up -d exporter
    ;;
  down) exec "${compose[@]}" down ;;
  status) exec "${compose[@]}" ps exporter ;;
  logs) exec "${compose[@]}" logs -f exporter ;;
  *) printf 'usage: manage.sh validate|up|down|status|logs\n' >&2; exit 2 ;;
esac
