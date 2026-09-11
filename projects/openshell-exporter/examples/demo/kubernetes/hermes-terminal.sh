#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/sandbox-control-lib.sh"

usage() {
  cat <<'EOF'
Usage: ./hermes-terminal.sh [SANDBOX_NAME]

Open interactive Hermes in an existing demo-owned sandbox. If SANDBOX_NAME is
omitted, create a uniquely named sandbox first. Exit Hermes with Ctrl-D or its
normal /exit command; the sandbox remains available for SOC investigation.
EOF
}

if [[ ${1:-} == -h || ${1:-} == --help ]]; then
  usage
  exit 0
fi
[[ -t 0 && -t 1 ]] || fail "Hermes terminal requires an interactive TTY"

name=${1:-"hermes-$(date -u '+%H%M%S')"}
validate_sandbox_name "$name"

trap stop_sandbox_control EXIT INT TERM
start_sandbox_control

if sandbox_json=$(control_openshell sandbox get "$name" -o json 2>/dev/null); then
  owner=$(jq -er '.labels.demo // empty' <<<"$sandbox_json")
  [[ "$owner" == openshell-event-exporter ]] \
    || fail "refusing to enter sandbox $name because it is not owned by this demo"
else
  sandbox_json=$(create_demo_sandbox "$name" kubernetes-hermes-relay-soc-interactive)
  note "created sandbox $name"
fi

sandbox_id=$(jq -er '.id' <<<"$sandbox_json")
policy_version=$(jq -er '.current_policy_version // empty' <<<"$sandbox_json" || true)
identity_args=(
  --env "HERMES_OPENSHELL_SANDBOX_ID=$sandbox_id"
  --env "HERMES_OPENSHELL_SANDBOX_NAME=$name"
)
if [[ "$policy_version" =~ ^[0-9]+$ ]]; then
  identity_args+=(--env "HERMES_OPENSHELL_POLICY_VERSION=$policy_version")
fi

note "opening Hermes in $name ($sandbox_id); telemetry will appear in Kibana and Grafana"
"$KUBECTL_BIN" --context "$CONTEXT" -n "$GATEWAY_NAMESPACE" exec -it "$CONTROL_POD" -- \
  env OPENSHELL_GATEWAY=kubernetes-demo openshell sandbox exec --name "$name" \
  "${identity_args[@]}" --workdir /sandbox --timeout 3600 --tty -- \
  hermes-correlated
