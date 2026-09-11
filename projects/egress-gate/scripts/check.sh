#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

uv_run=(uv run --frozen)
if [[ $# -gt 0 ]]; then
  if [[ $1 != "--python" || $# -ne 2 ]]; then
    echo "usage: scripts/check.sh [--python VERSION]" >&2
    exit 2
  fi
  uv_run+=(--python "$2")
fi

npm --prefix examples/pi-attested-admission/pi-harness ci --ignore-scripts --no-audit --no-fund
npm --prefix examples/pi-attested-admission/pi-harness run build
"${uv_run[@]}" pytest -q
"${uv_run[@]}" ruff format --check .
"${uv_run[@]}" ruff check .
"${uv_run[@]}" ty check
"${uv_run[@]}" python -c "import egress_gate"
npm --prefix examples/pi-attested-admission/pi-harness test
"${uv_run[@]}" pip-audit \
  --progress-spinner off \
  --local
