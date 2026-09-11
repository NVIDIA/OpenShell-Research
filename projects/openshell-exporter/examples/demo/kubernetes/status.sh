#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
resolve_tools
k get pods -n "$GATEWAY_NAMESPACE" -o wide
k get pods,pvc,certificate -n "$OBS_NAMESPACE"
k get sandboxes,pods,pvc -n "$SANDBOX_NAMESPACE"
[[ ! -r "$RUNTIME_DIR/verification.json" ]] || jq . "$RUNTIME_DIR/verification.json"
