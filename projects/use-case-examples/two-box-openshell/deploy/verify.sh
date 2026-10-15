#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# The only definition of "it works". Run on the control node (control node) after
# sandbox-node.sh has joined the sandbox node.
# Creates one sandbox from the sandboxes template, checks where each pod landed,
# and sends one allowed and one disallowed request. Exits non-zero on failure.
#
# Usage: deploy/verify.sh [--keep]   --keep leaves the verify sandbox running

set -euo pipefail

deploy_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=versions.env
source "${deploy_dir}/versions.env"
# shellcheck disable=SC1091
source "${TWOBOX_HOME:-${HOME}/.twobox}/env"

SANDBOX=twobox-verify
failures=0
watchdog_log="${TWOBOX_HOME}/watchdog/watchdog.jsonl"
policy="${deploy_dir}/demo-policy.yaml"
if kubectl -n openshell get secret twobox-watchdog-ca >/dev/null 2>&1; then
  policy="${deploy_dir}/watched-policy.yaml"
fi

check() {
  local description="$1"
  shift
  if "$@"; then
    echo "PASS  ${description}"
  else
    echo "FAIL  ${description}"
    failures=$((failures + 1))
  fi
}

box_of_node() {
  kubectl get node "$1" -o jsonpath='{.metadata.labels.openshell\.io/box}'
}

box_of_pod() {
  box_of_node "$(kubectl -n openshell get pod "$1" -o jsonpath='{.spec.nodeName}')"
}

node_ready_with_label() {
  kubectl get nodes -l "$1" \
    -o jsonpath='{range .items[*]}{.status.conditions[?(@.type=="Ready")].status}{"\n"}{end}' |
    grep -qx True
}

sandbox_box_reserved() {
  kubectl get nodes -l "${SANDBOX_LABEL}" -o jsonpath='{.items[*].spec.taints[*].key}' |
    grep -qw "${SANDBOX_TAINT%%=*}"
}

sandbox_curl() {
  openshell sandbox exec -n "${SANDBOX}" --no-tty -- \
    curl -sS -m 10 -o /dev/null -w '%{http_code}' "$1" 2>/dev/null || true
}

check "Control node is Ready" node_ready_with_label "${CONTROL_LABEL}"
check "Sandbox node is Ready" node_ready_with_label "${SANDBOX_LABEL}"
check "Sandbox node accepts only sandbox workloads" sandbox_box_reserved

if ! openshell sandbox get "${SANDBOX}" >/dev/null 2>&1; then
  openshell sandbox create --template sandboxes --name "${SANDBOX}" \
    --policy "${policy}" --detach --no-tty -- sleep infinity >/dev/null
fi

workload_pod="default--${SANDBOX}"
kubectl -n openshell wait --for=condition=Ready "pod/${workload_pod}" --timeout=300s >/dev/null
pair="$(kubectl -n openshell get pod "${workload_pod}" -o jsonpath='{.metadata.labels.openshell\.ai/boundary-pair}')"
supervisor_pod="$(kubectl -n openshell get pod \
  -l "openshell.ai/boundary-pair=${pair},openshell.ai/boundary-role=supervisor" -o name)"
supervisor_pod="${supervisor_pod#pod/}"

check "Gateway runs on the control node" test "$(box_of_pod openshell-0)" = control
check "Sandbox workload runs on the sandbox node" test "$(box_of_pod "${workload_pod}")" = sandboxes
check "Its supervisor runs on the control node" test "$(box_of_pod "${supervisor_pod}")" = control
check "Allowed request (curl to httpbin.org) succeeds" test "$(sandbox_curl https://httpbin.org/get)" = 200
check "Unlisted host (example.com) is refused" test "$(sandbox_curl https://example.com/)" = 000
if [[ "${policy}" == *watched-policy.yaml ]]; then
  check "Watchdog accounted for the request" \
    grep -q "\"sandbox\": \"${SANDBOX}\", .*\"host\": \"httpbin.org\"" "${watchdog_log}"
fi

if [[ "${1:-}" != "--keep" ]]; then
  openshell sandbox delete "${SANDBOX}" >/dev/null
fi

if ((failures > 0)); then
  echo "${failures} check(s) failed"
  exit 1
fi
echo "All checks passed"
