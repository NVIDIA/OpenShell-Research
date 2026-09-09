#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail
set +x # Never trace populated credential variables.
umask 077
example=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
project=$(cd -- "$example/../.." && pwd)
state=$project/.workspaces/pi-admission
print_only=false
if [[ ${1:-} == --print ]]; then print_only=true; shift; fi
action=${1:-help}
# .env is trusted operator input. Print mode never executes it.
if ! $print_only && [[ -f $example/.env ]]; then
  set -a
  source "$example/.env"
  set +a
fi
service_host=${EGRESS_GATE_HOST:-YOUR_SERVICE_HOST}
gateway=${OPENSHELL_GATEWAY:-YOUR_GATEWAY}
openshell=(openshell --gateway "$gateway")
run() {
  if $print_only; then printf '%q ' "$@"; printf '\n'; else "$@"; fi
}
cd "$project"
case "$action" in
  prepare)
    if ! $print_only; then
      : "${EGRESS_GATE_HOST:?Set the service hostname or IPv4 address in .env}"
      : "${OPENSHELL_GATEWAY_PUBLIC_KEY:?Set the gateway public PEM path in .env}"
      : "${OPENSHELL_GATEWAY_ISSUER:?Set the gateway JWT issuer in .env}"
    fi
    run uv sync --frozen
    run uv run --frozen python "$example/prepare.py" --state "$state" --host "$service_host" --gateway-public-key "${OPENSHELL_GATEWAY_PUBLIC_KEY:-/path/to/gateway-public.pem}" --gateway-issuer "${OPENSHELL_GATEWAY_ISSUER:-YOUR_GATEWAY_ISSUER}"
    run docker build --tag pi-admission:local "$state/image"
    ;;
  serve)
    run uv run --frozen egress-gate serve --listen 0.0.0.0:50051 --admission-config "$state/admission.json"
    ;;
  registration)
    run cat "$state/middleware.toml"
    ;;
  setup)
    if ! $print_only; then
      : "${OPENSHELL_GATEWAY:?Select your existing gateway in .env}"
      : "${PI_MODEL_API_KEY:?Set PI_MODEL_API_KEY in the example .env}"
      export PI_MODEL_API_KEY
      EGRESS_ADMISSION_TOKEN=$(uv run --frozen python -c 'import json,sys; print(json.load(open(sys.argv[1]))["bearer_token"])' "$state/admission.json")
      export EGRESS_ADMISSION_TOKEN
    fi
    run "${openshell[@]}" gateway info
    for provider in model admission; do
      run "${openshell[@]}" provider profile import --file "$state/$provider-provider.yaml"
      variable=PI_MODEL_API_KEY
      [[ $provider != admission ]] || variable=EGRESS_ADMISSION_TOKEN
      run "${openshell[@]}" provider create --name "pi-admission-$provider" --type "pi-admission-$provider" --credential "$variable"
    done
    run "${openshell[@]}" sandbox create --name pi-admission --from pi-admission:local --policy "$state/policy.yaml" --provider pi-admission-model --provider pi-admission-admission --detach -- /bin/sleep infinity
    if $print_only; then
      printf '%q ' "${openshell[@]}" sandbox get pi-admission --output json
      printf '| uv run --frozen python %q --state %q\n' "$example/bind-sandbox.py" "$state"
    else
      "${openshell[@]}" sandbox get pi-admission --output json | uv run --frozen python "$example/bind-sandbox.py" --state "$state"
    fi
    ;;
  launch)
    if ! $print_only; then : "${OPENSHELL_GATEWAY:?Select your existing gateway in .env}" "${EGRESS_GATE_HOST:?Set the service host in .env}"; fi
    run "${openshell[@]}" sandbox exec --tty --name pi-admission -- /usr/local/bin/node /app/dist/src/cli.js --admission "https://$service_host:5443/v1/admission"
    ;;
  verify)
    if ! $print_only; then : "${OPENSHELL_GATEWAY:?Select your existing gateway in .env}" "${EGRESS_GATE_HOST:?Set the service host in .env}"; fi
    run "${openshell[@]}" sandbox exec --no-tty --name pi-admission -- /usr/local/bin/node /app/dist/src/verify.js --admission "https://$service_host:5443/v1/admission"
    ;;
  cleanup)
    if ! $print_only; then : "${OPENSHELL_GATEWAY:?Select your existing gateway in .env}"; fi
    run "${openshell[@]}" sandbox delete pi-admission
    for provider in model admission; do
      run "${openshell[@]}" provider delete "pi-admission-$provider"
      run "${openshell[@]}" provider profile delete "pi-admission-$provider"
    done
    run uv run --frozen python -c 'import pathlib,sys; pathlib.Path(sys.argv[1]).unlink(missing_ok=True)' "$state/sandbox-id"
    printf 'Sandbox and its sessions removed. Stop serve with Ctrl-C; the gateway is unchanged.\n'
    printf 'Host configuration remains in %s; the local Docker image is retained.\n' "$state"
    ;;
  help)
    printf 'Usage: ./demo.sh [--print] ACTION\n\n'
    printf '  prepare  Generate service TLS/config; build the Pi image\n'
    printf '  serve    Run Egress Gate (keep this terminal open)\n'
    printf '  registration  Print middleware config for your gateway operator\n'
    printf '  setup    Create providers and sandbox; bind admission identity\n'
    printf '  launch   Start a new interactive Pi-powered session\n'
    printf '  verify   Run real allow/deny/redact, tools, skill, compaction and bypass checks\n'
    printf '  cleanup  Delete this sandbox/providers, including saved sessions\n'
    printf '\n--print shows commands without executing .env, requiring secrets, or changing state.\n'
    ;;
  *) echo "Unknown action. Run ./demo.sh help." >&2; exit 2 ;;
esac
