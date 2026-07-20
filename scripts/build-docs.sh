#!/usr/bin/env sh
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -eu

python_bin=""

for candidate in python3.12 python3.11 python3.10 python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" - <<'PY'
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY
    then
      python_bin="$candidate"
      break
    fi
  fi
done

if [ -z "$python_bin" ]; then
  echo "Python 3.10 or newer is required to build the Zensical documentation." >&2
  exit 1
fi

"$python_bin" -m venv --clear .venv-docs
. .venv-docs/bin/activate

python -m pip install --upgrade pip
python -m pip install -r requirements-docs.txt

python scripts/render-dev-notes.py
zensical build --clean --strict
REQUIRE_RENDERED_404=1 python tests/test_docs_404.py
