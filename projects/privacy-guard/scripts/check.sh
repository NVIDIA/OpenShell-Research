#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

uv_run=(uv run --frozen)
artifact_python=()
if [[ $# -gt 0 ]]; then
  if [[ $1 != "--python" || $# -ne 2 ]]; then
    echo "usage: scripts/check.sh [--python VERSION]" >&2
    exit 2
  fi
  uv_run+=(--python "$2")
  artifact_python+=(--python "$2")
fi

"${uv_run[@]}" pytest -q
"${uv_run[@]}" ruff format --check .
"${uv_run[@]}" ruff check .
"${uv_run[@]}" ty check
"${uv_run[@]}" python -c "import privacy_guard"
uv build
"${uv_run[@]}" python scripts/check_artifacts.py

artifact_check_dir=$(mktemp -d)
trap 'rm -rf "$artifact_check_dir"' EXIT
uv venv --clear "${artifact_python[@]}" "$artifact_check_dir/venv"

wheels=(dist/*.whl)
if [[ ${#wheels[@]} -ne 1 ]]; then
  echo "expected exactly one built wheel, found ${#wheels[@]}" >&2
  exit 1
fi

uv pip install \
  --offline \
  --python "$artifact_check_dir/venv/bin/python" \
  "${wheels[0]}"
cp scripts/external_engine_typing.py "$artifact_check_dir/external_engine_typing.py"
"${uv_run[@]}" ty check \
  --project "$artifact_check_dir" \
  --python "$artifact_check_dir/venv" \
  "$artifact_check_dir/external_engine_typing.py"
