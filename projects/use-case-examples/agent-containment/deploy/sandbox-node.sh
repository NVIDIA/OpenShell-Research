#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# A sandbox node (the sandbox node, and any further boxes): a k3s agent node marked so that
# only sandbox workloads run on it. Nothing else is installed here: no gateway,
# no supervisor, no credentials, no CLI. Run it on each sandbox node.
#
# Usage:
#   deploy/sandbox-node.sh TOKEN CONTROL_IP   join the cluster the control node created
#   deploy/sandbox-node.sh --reset            uninstall the k3s agent
#
# Environment:
#   NODE_IP  address the control node uses to reach this node (default: first host IP)

set -euo pipefail

deploy_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=versions.env
source "${deploy_dir}/versions.env"

if [[ "${1:-}" == "--reset" ]]; then
  if [[ -x /usr/local/bin/k3s-agent-uninstall.sh ]]; then sudo /usr/local/bin/k3s-agent-uninstall.sh; fi
  exit 0
fi
if [[ $# -ne 2 ]]; then
  echo "usage: $0 TOKEN CONTROL_IP | --reset" >&2
  exit 2
fi

NODE_IP="${NODE_IP:-$(hostname -I | awk '{print $1}')}"
# Flannel tunnels over the default-route interface unless told otherwise, even
# when --node-ip names another one, so pin it to the interface holding NODE_IP.
NODE_INTERFACE="$(ip -o -4 addr show | awk -v ip="${NODE_IP}" '$4 ~ "^" ip "/" {print $2; exit}')"
if [[ -z "${NODE_INTERFACE}" ]]; then
  echo "no local interface has address ${NODE_IP}" >&2
  exit 1
fi

echo "==> k3s ${K3S_VERSION} agent on ${NODE_IP}, joining ${2}"
curl -sfL https://get.k3s.io | \
  INSTALL_K3S_VERSION="${K3S_VERSION}" K3S_URL="https://${2}:6443" K3S_TOKEN="${1}" \
  sh -s - agent \
    --node-ip "${NODE_IP}" \
    --flannel-iface "${NODE_INTERFACE}" \
    --node-label "${SANDBOX_LABEL}" \
    --node-taint "${SANDBOX_TAINT}"

echo "Sandbox node joined. Run deploy/verify.sh on the control node."
