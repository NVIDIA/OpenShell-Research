#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

[[ $(uname -s) == Linux ]] || fail "this Omnistation demo currently supports Linux hosts"
[[ $(uname -m) == x86_64 ]] || fail "this demo currently requires linux/amd64"
for command in docker curl openssl sha256sum awk sed grep tar git jq rg; do
  command -v "$command" >/dev/null || fail "$command is required"
done
docker info >/dev/null 2>&1 || fail "Docker is not reachable"
docker buildx version >/dev/null 2>&1 || fail "Docker Buildx is required"
load_nvidia_api_key
resolve_tools
"$MINIKUBE_BIN" version --short | grep -F "v$MINIKUBE_VERSION" >/dev/null || fail "Minikube v$MINIKUBE_VERSION is required"
"$HELM_BIN" version --short | grep -F "v$HELM_VERSION" >/dev/null || fail "Helm v$HELM_VERSION is required"
"$KUBECTL_BIN" version --client -o json | grep -F "v$KUBECTL_VERSION" >/dev/null || fail "kubectl v$KUBECTL_VERSION is required"
note "Docker and pinned Minikube, kubectl, and Helm tools are ready"
