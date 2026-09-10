#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

# Keep the protocol, bindings, lockfile and provenance under one owner.
exec uv run --frozen --project ../openshell-middleware-manager omm update . \
  --openshell-version v0.0.116 --check-command 'make check'
