#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail
set +x # Never trace populated credential variables.
umask 077
example=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
project=$(cd -- "$example/../.." && pwd)
state=$project/.workspaces/pi-no-fork
print_only=false
if [[ ${1:-} == --print ]]; then print_only=true; shift; fi
action=${1:-help}
# .env is trusted operator input. Print mode never executes it.
if ! $print_only && [[ -f $example/.env ]]; then
  set -a
  source "$example/.env"
  set +a
fi
host_ip=${EGRESS_GATE_HOST_IP:-172.17.0.1}
cli=$state/bin/openshell
openshell=(env XDG_CONFIG_HOME="$state/config" "$cli" --gateway pi-admission --gateway-endpoint https://127.0.0.1:17672)
runtime_env=(env OPENSHELL_LOCAL_TLS_DIR="$state/tls" XDG_CONFIG_HOME="$state/config" XDG_STATE_HOME="$state/state" XDG_DATA_HOME="$state/data")
run() {
  if $print_only; then printf '%q ' "$@"; printf '\n'; else "$@"; fi
}
cd "$project"
case "$action" in
  prepare)
    if ! $print_only && [[ $(uname -s) != Linux || $(uname -m) != x86_64 ]]; then
      echo "This pinned POC launcher supports Linux x86_64 with Docker." >&2; exit 1
    fi
    run uv sync --frozen
    run mkdir -p "$state/bin"
    for component in openshell openshell-gateway openshell-sandbox; do
      target=x86_64-unknown-linux-musl
      [[ $component != openshell-gateway ]] || target=x86_64-unknown-linux-gnu
      archive=$component-$target.tar.gz
      case "$component" in
        openshell) checksum=4fb4476d80a1875a0b83547ec3aba999cf0a2e2d75f95f2f709b622e2103520e ;;
        openshell-gateway) checksum=59c6da724eae7a00c28826f9191efbdf4fbaa5c768afdc8dea6a80a949ebcc89 ;;
        openshell-sandbox) checksum=0bb160f73e5007338b94e3c868f66f50c71cd65c27c932ed9a4fa67c49e6d423 ;;
      esac
      run curl --fail --location --silent --show-error "https://github.com/NVIDIA/OpenShell/releases/download/v0.0.116/$archive" -o "$state/bin/$archive"
      if $print_only; then
        printf 'printf "%%s  %%s\\n" %q %q | sha256sum --check\n' "$checksum" "$state/bin/$archive"
      else
        printf '%s  %s\n' "$checksum" "$state/bin/$archive" | sha256sum --check
      fi
      run tar -xzf "$state/bin/$archive" -C "$state/bin" "$component"
      run chmod 755 "$state/bin/$component"
    done
    run uv run --frozen python "$example/prepare.py" --state "$state" --host-ip "$host_ip"
    run docker build --tag pi-admission:local "$state/image"
    ;;
  serve)
    run uv run --frozen egress-gate serve --listen 0.0.0.0:50051 --admission-config "$state/admission.json"
    ;;
  gateway)
    run "${runtime_env[@]}" "$state/bin/openshell-gateway" --config "$state/gateway.toml" --port 17672 --bind-address 0.0.0.0 --db-url "sqlite:$state/gateway.db"
    ;;
  setup)
    if ! $print_only; then
      : "${PI_MODEL_API_KEY:?Set PI_MODEL_API_KEY in the example .env}"
      export PI_MODEL_API_KEY
      EGRESS_ADMISSION_TOKEN=$(uv run --frozen python -c 'import json,sys; print(json.load(open(sys.argv[1]))["bearer_token"])' "$state/admission.json")
      export EGRESS_ADMISSION_TOKEN
    fi
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
    run "${openshell[@]}" sandbox exec --tty --name pi-admission -- /usr/local/bin/node /app/dist/src/cli.js --admission https://host.openshell.internal:5443/v1/admission
    ;;
  verify)
    run "${openshell[@]}" sandbox exec --no-tty --name pi-admission -- /usr/local/bin/node /app/dist/src/verify.js --admission https://host.openshell.internal:5443/v1/admission
    ;;
  cleanup)
    run "${openshell[@]}" sandbox delete pi-admission
    for provider in model admission; do
      run "${openshell[@]}" provider delete "pi-admission-$provider"
      run "${openshell[@]}" provider profile delete "pi-admission-$provider"
    done
    run uv run --frozen python -c 'import pathlib,sys; pathlib.Path(sys.argv[1]).unlink(missing_ok=True)' "$state/sandbox-id"
    printf 'Sandbox and its sessions removed. Stop serve and gateway with Ctrl-C.\n'
    printf 'Host configuration and downloaded artifacts remain in %s.\n' "$state"
    ;;
  help)
    printf 'Usage: ./demo.sh [--print] ACTION\n\n'
    printf '  prepare  Download pinned upstream binaries; generate local TLS/config; build image\n'
    printf '  serve    Run Egress Gate (keep this terminal open)\n'
    printf '  gateway  Run isolated OpenShell gateway (keep this terminal open)\n'
    printf '  setup    Create providers and sandbox; bind admission identity\n'
    printf '  launch   Start a new interactive Pi-powered session\n'
    printf '  verify   Run real allow/deny/redact, tools, skill, compaction and bypass checks\n'
    printf '  cleanup  Delete this sandbox/providers, including saved sessions\n'
    printf '\n--print shows commands without executing .env, requiring secrets, or changing state.\n'
    ;;
  *) echo "Unknown action. Run ./demo.sh help." >&2; exit 2 ;;
esac
