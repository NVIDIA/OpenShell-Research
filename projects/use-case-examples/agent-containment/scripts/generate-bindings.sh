#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Regenerate the Python gRPC bindings from the vendored OpenShell protos.
# Refresh proto/ from the release named in deploy/versions.env first.

set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
out="${project_dir}/src/egress_watchdog/bindings"

cd "${project_dir}"
uv run --group dev python -m grpc_tools.protoc -Iproto \
  --python_out="${out}" --pyi_out="${out}" --grpc_python_out="${out}" \
  proto/extension.proto proto/supervisor_middleware.proto

# protoc emits top-level imports; make them absolute package imports.
sed -i.bak -E 's/^import (extension_pb2|supervisor_middleware_pb2) as/from egress_watchdog.bindings import \1 as/' \
  "${out}"/*_pb2.py "${out}"/*_pb2_grpc.py
rm -f "${out}"/*.bak
