#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

DEMO_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=${OPENSHELL_DEMO_REPO_ROOT:-$(cd -- "$DEMO_DIR/../../.." && pwd)}
RUNTIME_DIR=${OPENSHELL_DEMO_RUNTIME_DIR:-$DEMO_DIR/runtime}
TOOLS_DIR="$RUNTIME_DIR/tools"
VALUES_DIR="$RUNTIME_DIR/values"
DEMO_ENV="$RUNTIME_DIR/demo.env"
mkdir -p "$TOOLS_DIR" "$VALUES_DIR"
umask 077

PROFILE=${OPENSHELL_DEMO_PROFILE:-openshell-demo}
CONTEXT=${OPENSHELL_DEMO_CONTEXT:-openshell-demo}
NODE_CONTAINER=${OPENSHELL_DEMO_NODE_CONTAINER:-openshell-demo}
OBS_NAMESPACE=${OPENSHELL_DEMO_OBS_NAMESPACE:-openshell-observability}
GATEWAY_NAMESPACE=${OPENSHELL_DEMO_GATEWAY_NAMESPACE:-openshell}
SANDBOX_NAMESPACE=${OPENSHELL_DEMO_SANDBOX_NAMESPACE:-openshell-sandboxes}
PROVIDER_CREDENTIAL_NAMESPACE=${OPENSHELL_DEMO_PROVIDER_CREDENTIAL_NAMESPACE:-openshell-provider-credentials}
GATEWAY_ID=${OPENSHELL_DEMO_GATEWAY_ID:-omnistation-kubernetes-demo}
OPEN_SHELL_VERSION=0.0.113
OPEN_SHELL_CHART=oci://ghcr.io/nvidia/openshell/helm-chart
OPEN_SHELL_CHART_DIGEST=sha256:365f41e65eead00698157c918d76bd3680cfff12e80bd75d2ec5e4587424bb33
AGENT_SANDBOX_VERSION=0.5.3
AGENT_SANDBOX_URL=https://github.com/kubernetes-sigs/agent-sandbox/releases/download/v0.5.3/sandbox-with-extensions.yaml
AGENT_SANDBOX_SHA256=e21a561002a800f78d05d45cb80d773f1651ba6cc0e2b6b9d5110846db031c4d
AGENT_SANDBOX_IMAGE=registry.k8s.io/agent-sandbox/agent-sandbox-controller:v0.5.3
AGENT_SANDBOX_SOURCE_URL=https://codeload.github.com/kubernetes-sigs/agent-sandbox/tar.gz/refs/tags/v0.5.3
AGENT_SANDBOX_SOURCE_SHA256=2ada9807b0159ce44de6d3278a46abf5f3d5e920f3cadf0f224f7dff61a96074
AGENT_SANDBOX_GO_IMAGE=docker.io/library/golang:1.26.8-bookworm@sha256:9fdc884aacc3bec89b20ffc69f4bb369c78210e3e4f600387b5128b12c199f81
AGENT_SANDBOX_RUNTIME_IMAGE=gcr.io/distroless/static-debian13:nonroot@sha256:1c2c046bc09ed40fad370b599a0b1ae7987f55b01e247cf27a7c27cd97e5bbc7
MINIKUBE_VERSION=1.38.1
MINIKUBE_SHA256=099477eaf248bcb5bcea8ce78a2898e93ac01461c35189da1848c3de82ecd22e
KUBECTL_VERSION=1.35.0
KUBECTL_SHA256=a2e984a18a0c063279d692533031c1eff93a262afcc0afdc517375432d060989
HELM_VERSION=3.22.0
HELM_SHA256=1e4ab49e429626cf6c6958d914248b78c9730803c2751b87627e171dc800e7bb
NVIDIA_ENV_FILE=${OPENSHELL_DEMO_NVIDIA_ENV_FILE:-$HOME/.config/openshell-exporter/nvidia.env}

fail() { printf 'kubernetes demo: %s\n' "$*" >&2; exit 1; }
note() { printf 'kubernetes demo: %s\n' "$*"; }

load_nvidia_api_key() {
  if [[ -n ${NVIDIA_API_KEY:-} ]]; then
    export NVIDIA_API_KEY
    return
  fi
  [[ -f "$NVIDIA_ENV_FILE" ]] || fail "NVIDIA_API_KEY is unset and $NVIDIA_ENV_FILE does not exist"
  local mode
  mode=$(stat -c '%a' "$NVIDIA_ENV_FILE")
  [[ "$mode" == 600 || "$mode" == 400 ]] || fail "$NVIDIA_ENV_FILE must have mode 0600 or 0400"
  # shellcheck disable=SC1090
  source "$NVIDIA_ENV_FILE"
  [[ -n ${NVIDIA_API_KEY:-} ]] || fail "$NVIDIA_ENV_FILE does not define NVIDIA_API_KEY"
  export NVIDIA_API_KEY
}

sha256_check() {
  local expected=$1 file=$2 actual
  actual=$(sha256sum "$file" | awk '{print $1}')
  [[ "$actual" == "$expected" ]] || fail "checksum mismatch for $file: expected $expected, got $actual"
}

download() {
  local url=$1 output=$2
  curl --fail --location --proto '=https' --tlsv1.2 --retry 5 --retry-delay 2 --retry-all-errors --silent --show-error "$url" -o "$output"
}

bootstrap_tool() {
  local name=$1 version=$2 url=$3 checksum=$4
  local target="$TOOLS_DIR/$name"
  if [[ ! -x "$target" ]]; then
    note "downloading pinned $name $version" >&2
    download "$url" "$target.download"
    sha256_check "$checksum" "$target.download"
    mv "$target.download" "$target"
    chmod 0755 "$target"
  fi
  printf '%s\n' "$target"
}

resolve_tools() {
  MINIKUBE_BIN=$(command -v minikube || true)
  KUBECTL_BIN=$(command -v kubectl || true)
  HELM_BIN=$(command -v helm || true)

  if [[ -z "$MINIKUBE_BIN" ]]; then
    MINIKUBE_BIN=$(bootstrap_tool minikube "$MINIKUBE_VERSION" "https://github.com/kubernetes/minikube/releases/download/v${MINIKUBE_VERSION}/minikube-linux-amd64" "$MINIKUBE_SHA256")
  fi
  if [[ -z "$KUBECTL_BIN" ]]; then
    KUBECTL_BIN=$(bootstrap_tool kubectl "$KUBECTL_VERSION" "https://dl.k8s.io/release/v${KUBECTL_VERSION}/bin/linux/amd64/kubectl" "$KUBECTL_SHA256")
  fi
  if [[ -z "$HELM_BIN" ]]; then
    local archive="$TOOLS_DIR/helm.tar.gz"
    if [[ ! -x "$TOOLS_DIR/helm" ]]; then
      download "https://get.helm.sh/helm-v${HELM_VERSION}-linux-amd64.tar.gz" "$archive"
      sha256_check "$HELM_SHA256" "$archive"
      tar -xzf "$archive" -C "$TOOLS_DIR" linux-amd64/helm
      mv "$TOOLS_DIR/linux-amd64/helm" "$TOOLS_DIR/helm"
      rmdir "$TOOLS_DIR/linux-amd64"
      rm -f "$archive"
      chmod 0755 "$TOOLS_DIR/helm"
    fi
    HELM_BIN="$TOOLS_DIR/helm"
  fi
  export MINIKUBE_BIN KUBECTL_BIN HELM_BIN
}

k() { "$KUBECTL_BIN" --context "$CONTEXT" "$@"; }
h() { "$HELM_BIN" --kube-context "$CONTEXT" "$@"; }

random_token() { openssl rand -hex 32; }

secret_token() {
  local namespace=$1 name=$2 token
  if k -n "$namespace" get secret "$name" >/dev/null 2>&1; then
    return
  fi
  token=$(random_token)
  k -n "$namespace" create secret generic "$name" --from-literal=token="$token" >/dev/null
}

copy_secret() {
  local from_ns=$1 from_name=$2 to_ns=$3 to_name=$4
  local ca cert key
  ca=$(k -n "$from_ns" get secret "$from_name" -o jsonpath='{.data.ca\.crt}')
  cert=$(k -n "$from_ns" get secret "$from_name" -o jsonpath='{.data.tls\.crt}')
  key=$(k -n "$from_ns" get secret "$from_name" -o jsonpath='{.data.tls\.key}')
  [[ -n "$ca" && -n "$cert" && -n "$key" ]] || fail "$from_ns/$from_name is missing mTLS data"
  k -n "$to_ns" create secret generic "$to_name" \
    --from-literal=ca.crt="$(printf '%s' "$ca" | base64 --decode)" \
    --from-literal=tls.crt="$(printf '%s' "$cert" | base64 --decode)" \
    --from-literal=tls.key="$(printf '%s' "$key" | base64 --decode)" \
    --dry-run=client -o yaml | k apply -f - >/dev/null
}

wait_deployment() {
  local namespace=$1 name=$2
  k -n "$namespace" rollout status "deployment/$name" --timeout=10m
}

wait_certificate() {
  local namespace=$1 name=$2
  k -n "$namespace" wait --for=condition=Ready "certificate/$name" --timeout=5m >/dev/null
}

wait_deployments_by_instance() {
  local namespace=$1 instance=$2 deployment
  local -a deployments
  mapfile -t deployments < <(k -n "$namespace" get deployments -l "app.kubernetes.io/instance=$instance" -o name)
  ((${#deployments[@]} > 0)) || fail "no deployments found for Helm release $instance in $namespace"
  for deployment in "${deployments[@]}"; do
    k -n "$namespace" rollout status "$deployment" --timeout=10m
  done
}

wait_certificates_by_instance() {
  local namespace=$1 instance=$2
  k -n "$namespace" wait --for=condition=Ready certificate \
    -l "app.kubernetes.io/instance=$instance" --timeout=5m >/dev/null
}
