#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

# OpenShell v0.0.116, unchanged upstream protocol. The generator is isolated
# because it requires protobuf 6; the application uses the patched protobuf 7.
revision=d1155aa70042d3e2ee49dbfa15346b108b7c1d92
binding_tmp=$(mktemp -d)
trap 'rm -r -- "$binding_tmp"' EXIT
mkdir -p "$binding_tmp/egress_gate/bindings"
curl --fail --silent --show-error \
  "https://raw.githubusercontent.com/NVIDIA/OpenShell/$revision/proto/supervisor_middleware.proto" \
  -o "$binding_tmp/egress_gate/bindings/supervisor_middleware.proto"
cp "$binding_tmp/egress_gate/bindings/supervisor_middleware.proto" proto/supervisor_middleware.proto
uvx --from grpcio-tools==1.81.1 python -m grpc_tools.protoc \
  -I "$binding_tmp" --python_out=src --pyi_out=src --grpc_python_out=src \
  "$binding_tmp/egress_gate/bindings/supervisor_middleware.proto"
