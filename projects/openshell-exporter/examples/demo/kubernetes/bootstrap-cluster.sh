#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
resolve_tools

if "$MINIKUBE_BIN" status -p "$PROFILE" >/dev/null 2>&1; then
  note "preserving running Minikube profile $PROFILE"
else
  note "starting Minikube profile $PROFILE"
  "$MINIKUBE_BIN" start -p "$PROFILE" --driver=docker --container-runtime=containerd \
    --kubernetes-version="v$KUBECTL_VERSION" --cpus=6 --memory=12288 --disk-size=80g \
    --addons=storage-provisioner
fi
"$MINIKUBE_BIN" update-context -p "$PROFILE" >/dev/null
k wait --for=condition=Ready nodes --all --timeout=5m >/dev/null
note "Minikube context $CONTEXT is ready"
