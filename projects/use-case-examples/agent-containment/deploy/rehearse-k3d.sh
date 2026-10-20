#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Development rehearsal only: two k3s nodes as containers on one Docker host,
# with the same labels and taint control-node.sh and sandbox-node.sh apply, then
# control-node.sh --existing-cluster on top. It exercises scheduling, the chart,
# the template, and the watchdog without sudo or a second machine. It shares one
# kernel, so it proves nothing about the physical boundary; use control-node.sh
# and sandbox-node.sh for that.
#
# Usage:
#   deploy/rehearse-k3d.sh           create the cluster and run control-node.sh on it
#   deploy/rehearse-k3d.sh --reset   delete the cluster and operator state
#
# Requires Docker and k3d (https://k3d.io) on PATH.

set -euo pipefail

deploy_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# The watchdog pod mounts this checkout, so the control node container needs it.
project_dir="$(dirname "${deploy_dir}")"
# shellcheck source=versions.env
source "${deploy_dir}/versions.env"

CLUSTER=twobox
export TWOBOX_HOME="${TWOBOX_HOME:-${HOME}/.twobox}"

if [[ "${1:-}" == "--reset" ]]; then
  k3d cluster delete "${CLUSTER}"
  rm -rf "${TWOBOX_HOME}"
  exit 0
fi

# k3d reads '@' as a node filter, so a literal '@' in the path is escaped.
if ! k3d cluster list "${CLUSTER}" >/dev/null 2>&1; then
  k3d cluster create "${CLUSTER}" \
    --image "rancher/k3s:${K3S_VERSION/+/-}" \
    --servers 1 --agents 1 --no-lb \
    --api-port 127.0.0.1:6550 \
    --port "127.0.0.1:${GATEWAY_NODE_PORT}:${GATEWAY_NODE_PORT}@server:0:direct" \
    --k3s-arg "--disable=traefik@server:0" \
    --volume "${project_dir//@/\\@}:${project_dir//@/\\@}@server:0" \
    --k3s-node-label "${CONTROL_LABEL}@server:0" \
    --k3s-node-label "${SANDBOX_LABEL}@agent:0" \
    --k3s-arg "--node-taint=${SANDBOX_TAINT}@agent:0" \
    --kubeconfig-update-default=false --kubeconfig-switch-context=false \
    --wait --timeout 180s
fi

mkdir -p "${TWOBOX_HOME}"
k3d kubeconfig get "${CLUSTER}" >"${TWOBOX_HOME}/kubeconfig"
chmod 600 "${TWOBOX_HOME}/kubeconfig"
export KUBECONFIG="${TWOBOX_HOME}/kubeconfig"
# The operator CLI on this host reaches the gateway through Docker's network
# gateway, not as the control node itself, so the gateway policy admits it.
operator_ip="$(docker network inspect "k3d-${CLUSTER}" -f '{{(index .IPAM.Config 0).Gateway}}')"
CONTROL_IP=127.0.0.1 OPERATOR_CIDR="${operator_ip}/32" "${deploy_dir}/control-node.sh" --existing-cluster
