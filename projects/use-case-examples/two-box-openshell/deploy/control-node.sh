#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# The control node: k3s server, Agent Sandbox, the OpenShell gateway, the
# operator CLI, and the `sandboxes` sandbox template. Prints the command to run
# on each sandbox node.
#
# Usage:
#   deploy/control-node.sh                     install k3s and everything above
#   deploy/control-node.sh --existing-cluster  skip k3s and use $KUBECONFIG as is
#   deploy/control-node.sh --reset             uninstall k3s and remove operator state
#
# Environment:
#   CONTROL_IP   address sandbox nodes use to reach this node (default: first host IP)
#   TWOBOX_HOME  operator state: CLI, kubeconfig, gateway config (default: ~/.twobox)

set -euo pipefail

deploy_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=versions.env
source "${deploy_dir}/versions.env"

TWOBOX_HOME="${TWOBOX_HOME:-${HOME}/.twobox}"
CONTROL_IP="${CONTROL_IP:-$(hostname -I | awk '{print $1}')}"
GATEWAY_NAME=twobox
JOIN_TOKEN_FILE=/etc/rancher/k3s/twobox-join-token

main() {
  case "${1:-}" in
    --reset) reset; return ;;
    --existing-cluster) use_existing_cluster ;;
    "") install_k3s_server ;;
    *) echo "usage: $0 [--existing-cluster|--reset]" >&2; exit 2 ;;
  esac

  install_helm
  install_openshell_cli
  install_agent_sandbox
  install_gateway
  register_gateway
  create_sandbox_template
  write_operator_env
  print_next_steps "${1:-}"
}

install_k3s_server() {
  echo "==> k3s ${K3S_VERSION} server on ${CONTROL_IP}"
  # Sandbox nodes join with a join-only token. Without one, k3s hands them the
  # server token, which can fetch the cluster's bootstrap data and CA keys.
  sudo mkdir -p /etc/rancher/k3s
  if ! sudo test -s "${JOIN_TOKEN_FILE}"; then
    openssl rand -hex 32 | sudo tee "${JOIN_TOKEN_FILE}" >/dev/null
    sudo chmod 600 "${JOIN_TOKEN_FILE}"
  fi
  curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION="${K3S_VERSION}" sh -s - server \
    --disable=traefik \
    --agent-token-file "${JOIN_TOKEN_FILE}" \
    --node-ip "${CONTROL_IP}" \
    --flannel-iface "$(interface_of "${CONTROL_IP}")" \
    --tls-san "${CONTROL_IP}" \
    --node-label "${CONTROL_LABEL}"
  mkdir -p "${TWOBOX_HOME}"
  sudo cat /etc/rancher/k3s/k3s.yaml >"${TWOBOX_HOME}/kubeconfig"
  chmod 600 "${TWOBOX_HOME}/kubeconfig"
  export KUBECONFIG="${TWOBOX_HOME}/kubeconfig"
  # The node object appears a few seconds after the service starts.
  for _ in $(seq 1 60); do
    kubectl get nodes -o name 2>/dev/null | grep -q . && break
    sleep 2
  done
  kubectl wait --for=condition=Ready node --all --timeout=120s
}

use_existing_cluster() {
  : "${KUBECONFIG:?--existing-cluster needs KUBECONFIG set}"
  command -v kubectl >/dev/null || { echo "kubectl is required" >&2; exit 1; }
  if ! kubectl get nodes -l "${CONTROL_LABEL}" -o name | grep -q .; then
    echo "no node labelled ${CONTROL_LABEL} in this cluster" >&2
    exit 1
  fi
}

install_helm() {
  mkdir -p "${TWOBOX_HOME}/bin"
  export PATH="${TWOBOX_HOME}/bin:${PATH}"
  if helm version --short 2>/dev/null | grep -q "^${HELM_VERSION}"; then return; fi
  echo "==> helm ${HELM_VERSION}"
  local arch tmp
  arch="$(go_arch)"
  tmp="$(mktemp -d)"
  curl -fsSL "https://get.helm.sh/helm-${HELM_VERSION}-linux-${arch}.tar.gz" | tar -xz -C "${tmp}"
  install -m 0755 "${tmp}/linux-${arch}/helm" "${TWOBOX_HOME}/bin/helm"
  rm -rf "${tmp}"
}

install_openshell_cli() {
  if openshell --version 2>/dev/null | grep -qx "openshell ${OPENSHELL_VERSION}"; then return; fi
  echo "==> openshell CLI ${OPENSHELL_VERSION}"
  local asset tmp base
  asset="openshell-$(uname -m)-unknown-linux-musl.tar.gz"
  base="https://github.com/NVIDIA/OpenShell/releases/download/v${OPENSHELL_VERSION}"
  tmp="$(mktemp -d)"
  curl -fsSL -o "${tmp}/${asset}" "${base}/${asset}"
  curl -fsSL -o "${tmp}/checksums.txt" "${base}/openshell-checksums-sha256.txt"
  (cd "${tmp}" && grep " ${asset}\$" checksums.txt | sha256sum -c --quiet)
  tar -xzf "${tmp}/${asset}" -C "${TWOBOX_HOME}/bin" openshell
  rm -rf "${tmp}"
}

install_agent_sandbox() {
  echo "==> Agent Sandbox ${AGENT_SANDBOX_VERSION}"
  kubectl apply -f "https://github.com/kubernetes-sigs/agent-sandbox/releases/download/${AGENT_SANDBOX_VERSION}/sandbox.yaml" >/dev/null
  kubectl -n agent-sandbox-system rollout status deploy/agent-sandbox-controller --timeout=180s
}

install_gateway() {
  echo "==> OpenShell gateway ${OPENSHELL_VERSION} (chart)"
  helm upgrade --install openshell oci://ghcr.io/nvidia/openshell/helm-chart \
    --version "${OPENSHELL_VERSION}" \
    --namespace openshell --create-namespace \
    --values "${deploy_dir}/values.yaml" \
    --set "service.nodePort=${GATEWAY_NODE_PORT}" \
    --set "pkiInitJob.serverIpAddresses={${CONTROL_IP}}" \
    --wait --timeout 10m
}

register_gateway() {
  export XDG_CONFIG_HOME="${TWOBOX_HOME}/config"
  # `gateway add --local` imports its client bundle from OPENSHELL_LOCAL_TLS_DIR,
  # or else from a package-installed gateway under ~/.local/state/openshell/tls.
  # Point it at this cluster's bundle so an existing local gateway's is never used.
  local tls="${TWOBOX_HOME}/tls"
  mkdir -p "${tls}/client"
  client_tls_file ca.crt >"${tls}/ca.crt"
  client_tls_file tls.crt >"${tls}/client/tls.crt"
  client_tls_file tls.key >"${tls}/client/tls.key"
  chmod 600 "${tls}/client/tls.key"
  rm -rf "${XDG_CONFIG_HOME}/openshell/gateways/${GATEWAY_NAME}"
  OPENSHELL_LOCAL_TLS_DIR="${tls}" \
    openshell gateway add "https://127.0.0.1:${GATEWAY_NODE_PORT}" --local --name "${GATEWAY_NAME}"
  openshell gateway select "${GATEWAY_NAME}" >/dev/null 2>&1 || true
  openshell status
}

create_sandbox_template() {
  local key="${SANDBOX_TAINT%%=*}" rest="${SANDBOX_TAINT#*=}"
  local value="${rest%%:*}" effect="${rest#*:}"
  local driver_config
  driver_config="$(printf '{"kubernetes":{"pod":{"node_selector":{"%s":"%s"},"tolerations":[{"key":"%s","operator":"Equal","value":"%s","effect":"%s"}]}}}' \
    "${SANDBOX_LABEL%%=*}" "${SANDBOX_LABEL#*=}" "${key}" "${value}" "${effect}")"
  openshell sandbox template delete sandboxes >/dev/null 2>&1 || true
  openshell sandbox template create sandboxes --image "${SANDBOX_IMAGE}" --driver-config-json "${driver_config}"
}

write_operator_env() {
  cat >"${TWOBOX_HOME}/env" <<EOF
# Source this on the control node to use the two-box cluster and gateway.
export TWOBOX_HOME="${TWOBOX_HOME}"
export PATH="${TWOBOX_HOME}/bin:$(dirname "$(command -v kubectl)"):\${PATH}"
export KUBECONFIG="${KUBECONFIG}"
export XDG_CONFIG_HOME="${TWOBOX_HOME}/config"
export OPENSHELL_GATEWAY="${GATEWAY_NAME}"
export CONTROL_IP="${CONTROL_IP}"
EOF
}

print_next_steps() {
  echo
  echo "the control node is ready. Operator environment: source ${TWOBOX_HOME}/env"
  if [[ "$1" == "--existing-cluster" ]]; then return; fi
  echo "On each sandbox node, run:"
  echo "  deploy/sandbox-node.sh $(sudo cat /var/lib/rancher/k3s/server/agent-token) ${CONTROL_IP}"
}

reset() {
  if [[ -x /usr/local/bin/k3s-uninstall.sh ]]; then sudo /usr/local/bin/k3s-uninstall.sh; fi
  sudo rm -f "${JOIN_TOKEN_FILE}"
  rm -rf "${TWOBOX_HOME}"
}

# Flannel tunnels over the default-route interface unless told otherwise, even
# when --node-ip names another one, so both nodes pin it to the chosen address.
interface_of() {
  local interface
  interface="$(ip -o -4 addr show | awk -v ip="$1" '$4 ~ "^" ip "/" {print $2; exit}')"
  if [[ -z "${interface}" ]]; then
    echo "no local interface has address $1" >&2
    exit 1
  fi
  echo "${interface}"
}

client_tls_file() {
  kubectl -n openshell get secret openshell-client-tls -o jsonpath="{.data.${1//./\\.}}" | base64 -d
}

go_arch() {
  case "$(uname -m)" in
    x86_64) echo amd64 ;;
    aarch64 | arm64) echo arm64 ;;
    *) echo "unsupported architecture: $(uname -m)" >&2; exit 1 ;;
  esac
}

main "$@"
