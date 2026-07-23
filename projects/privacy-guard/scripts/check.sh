#!/usr/bin/env bash
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

"${uv_run[@]}" pytest -q
"${uv_run[@]}" ruff format --check .
"${uv_run[@]}" ruff check .
"${uv_run[@]}" ty check
"${uv_run[@]}" python -c "import privacy_guard"
uv build
