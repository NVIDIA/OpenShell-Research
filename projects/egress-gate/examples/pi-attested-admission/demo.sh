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
delete_if_present() {
  local resource=$1 output status
  shift
  if $print_only; then run "$@"; return; fi
  if output=$("$@" 2>&1); then
    printf '%s\n' "$output"
  else
    status=$?
    # Older OpenShell releases return gRPC NotFound for an absent resource.
    if [[ $output == *"code: 'Some requested entity was not found'"* &&
          $output == *"message: \"$resource not found\""* ]]; then
      printf '%s already absent; continuing cleanup.\n' "$resource"
    else
      printf '%s\n' "$output" >&2
      return "$status"
    fi
  fi
}
registration() {
  run uv run --frozen python "$example/gateway-registration.py" "$1" --state "$state" --gateway "$gateway"
  if $print_only; then
    printf '# Helper edits only pi-egress, waits for health, and internally runs: '
    case "$OSTYPE" in
      darwin*) run brew services restart openshell ;;
      linux*) run systemctl --user restart openshell-gateway ;;
      *) printf 'no supported service manager\n' ;;
    esac
  fi
}
cd "$project"
case "$action" in
  prepare)
    if ! $print_only; then
      : "${EGRESS_GATE_HOST:?Set the service hostname or IPv4 address in .env}"
      : "${OPENSHELL_GATEWAY:?Select your existing gateway in .env}"
      if [[ ! -f $example/models.json ]]; then
        echo 'Create models.json from models.json.example and configure your model first.' >&2
        exit 1
      fi
    fi
    run uv sync --frozen
    if $print_only; then
      printf '%q ' "${openshell[@]}" gateway list --output json
      printf '| '
      run uv run --frozen python "$example/prepare.py" --state "$state" --host "$service_host" --gateway "$gateway" --model "${PI_MODEL:-}"
    else
      "${openshell[@]}" gateway list --output json | uv run --frozen python "$example/prepare.py" --state "$state" --host "$service_host" --gateway "$gateway" --model "${PI_MODEL:-}"
    fi
    run docker build --tag pi-admission:local "$state/image"
    ;;
  serve)
    run uv run --frozen egress-gate serve --listen 0.0.0.0:50051 --admission-config "$state/admission.json"
    ;;
  registration)
    run cat "$state/middleware.toml"
    ;;
  register|unregister)
    if ! $print_only; then : "${OPENSHELL_GATEWAY:?Select your existing gateway in .env}"; fi
    registration "$action"
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
    delete_if_present sandbox "${openshell[@]}" sandbox delete pi-admission
    for provider in model admission; do
      delete_if_present provider "${openshell[@]}" provider delete "pi-admission-$provider"
      delete_if_present 'provider profile' "${openshell[@]}" provider profile delete "pi-admission-$provider"
    done
    run uv run --frozen python -c 'import pathlib,sys; pathlib.Path(sys.argv[1]).unlink(missing_ok=True)' "$state/sandbox-id"
    registration unregister
    printf 'Sandbox and its sessions removed. Stop serve with Ctrl-C.\n'
    printf 'Host configuration remains in %s; the local Docker image is retained.\n' "$state"
    ;;
  help)
    printf 'Usage: ./demo.sh [--print] ACTION\n\n'
    printf '  prepare  Generate service TLS/config; build the Pi image\n'
    printf '  serve    Run Egress Gate (keep this terminal open)\n'
    printf '  register  Add middleware to the local gateway and restart it\n'
    printf '  unregister  Remove that registration and restart the gateway\n'
    printf '  registration  Show the TOML entry (manual deployments only; does not register)\n'
    printf '  setup    Create providers and sandbox; bind admission identity\n'
    printf '  launch   Start a new interactive Pi-powered session\n'
    printf '  verify   Run real allow/deny/redact, tools, skill, compaction and bypass checks\n'
    printf '  cleanup  Delete sandbox/providers/sessions; unregister middleware\n'
    printf '\n--print shows commands without executing .env, requiring secrets, or changing state.\n'
    ;;
  *) echo "Unknown action. Run ./demo.sh help." >&2; exit 2 ;;
esac
