#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail
set +x # Never trace populated credential variables.
umask 077
example=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
state=$example/.workspaces
print_only=false
if [[ ${1:-} == --print ]]; then print_only=true; shift; fi
action=${1:-help}
shift || true
# .env is trusted operator input. Print mode never executes it.
if ! $print_only && [[ -f $example/.env ]]; then
  set -a
  source "$example/.env"
  set +a
fi
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
    if [[ $output == *"code: 'Some requested entity was not found'"* &&
          $output == *"message: \"$resource not found\""* ]]; then
      printf '%s already absent; continuing cleanup.\n' "$resource"
    else
      printf '%s\n' "$output" >&2
      return "$status"
    fi
  fi
}
admission_mode() {
  if [[ ${1:-} != --admission || (${2:-} != off && ${2:-} != on) || $# != 2 ]]; then
    echo "Usage: ./demo.sh $action --admission off|on" >&2
    exit 2
  fi
  printf '%s' "$2"
}
cd "$example"
case "$action" in
  prepare)
    if (( $# )); then echo "prepare takes no arguments" >&2; exit 2; fi
    if ! $print_only && [[ ! -f $example/models.json ]]; then
      echo 'Create models.json from models.json.example and configure your model first.' >&2
      exit 1
    fi
    run uv sync --frozen
    run uv run --frozen python "$example/prepare.py" --state "$state" --model "${PI_MODEL:-}"
    run docker build --tag pi-admission:local "$state/image"
    ;;
  setup)
    if (( $# )); then echo "setup takes no arguments" >&2; exit 2; fi
    if ! $print_only; then
      : "${OPENSHELL_GATEWAY:?Select your existing gateway in .env}"
      : "${PI_MODEL_API_KEY:?Set PI_MODEL_API_KEY in the example .env}"
      export PI_MODEL_API_KEY
    fi
    run "${openshell[@]}" gateway info
    run "${openshell[@]}" provider profile import --file "$state/model-provider.yaml"
    run "${openshell[@]}" provider create --name pi-admission-model --type pi-admission-model --credential PI_MODEL_API_KEY
    run "${openshell[@]}" sandbox create --name pi-admission --from pi-admission:local --policy "$state/policy.yaml" --provider pi-admission-model --detach -- /bin/sleep infinity
    ;;
  launch|verify)
    mode=$(admission_mode "$@")
    if ! $print_only; then : "${OPENSHELL_GATEWAY:?Select your existing gateway in .env}"; fi
    tty=--tty
    entry=cli
    if [[ $action == verify ]]; then tty=--no-tty; entry=verify; fi
    run "${openshell[@]}" sandbox exec "$tty" --name pi-admission -- /usr/local/bin/node --disable-warning=UNDICI-EHPA "/app/dist/src/$entry.js" --admission "$mode"
    ;;
  cleanup)
    if (( $# )); then echo "cleanup takes no arguments" >&2; exit 2; fi
    if ! $print_only; then : "${OPENSHELL_GATEWAY:?Select your existing gateway in .env}"; fi
    delete_if_present sandbox "${openshell[@]}" sandbox delete pi-admission
    delete_if_present provider "${openshell[@]}" provider delete pi-admission-model
    delete_if_present 'provider profile' "${openshell[@]}" provider profile delete pi-admission-model
    printf 'Sandbox and its sessions removed. The local Docker image is retained.\n'
    ;;
  help)
    printf 'Usage: ./demo.sh [--print] ACTION [OPTIONS]\n\n'
    printf '  prepare  Generate policy/model config and build the Pi image\n'
    printf '  setup    Create the model provider and shared sandbox\n'
    printf '  launch --admission off|on  Start a fresh interactive session\n'
    printf '  verify --admission off|on  Run the paid live check in one mode\n'
    printf '  cleanup  Delete the sandbox, provider, and sessions\n'
    printf '\n--print shows commands without executing .env, requiring secrets, or changing state.\n'
    ;;
  *) echo "Unknown action. Run ./demo.sh help." >&2; exit 2 ;;
esac
