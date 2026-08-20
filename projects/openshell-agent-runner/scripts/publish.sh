#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

PROJECT_DIRECTORY=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PYPIRC_REPOSITORY="openshell-research"

usage() {
    echo "Usage: $0 VERSION [--dry-run]"
    echo
    echo "Build and publish openshell-agent-runner using the '$PYPIRC_REPOSITORY'"
    echo "repository configured in ~/.pypirc."
}

if [[ $# -lt 1 || $# -gt 2 ]]; then
    usage >&2
    exit 2
fi

if [[ "$1" == "-h" || "$1" == "--help" ]]; then
    usage
    exit 0
fi

VERSION="$1"
DRY_RUN=false

if [[ $# -eq 2 ]]; then
    if [[ "$2" != "--dry-run" ]]; then
        usage >&2
        exit 2
    fi
    DRY_RUN=true
fi

if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+(rc[0-9]+)?$ ]]; then
    echo "publish: invalid version '$VERSION'; expected X.Y.Z or X.Y.ZrcN" >&2
    exit 2
fi

cd "$PROJECT_DIRECTORY"

if [[ -n "$(git status --porcelain)" ]]; then
    echo "publish: the working tree must be clean" >&2
    exit 1
fi

if [[ "$(git branch --show-current)" != "main" ]]; then
    echo "publish: releases must be created from main" >&2
    exit 1
fi

TAG="v$VERSION"
if git rev-parse --verify --quiet "refs/tags/$TAG" >/dev/null; then
    echo "publish: tag '$TAG' already exists" >&2
    exit 1
fi

echo "Running release checks..."
uv sync --locked
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run pytest
uv run python -m compileall -q src tests
bash -n src/openshell_agent_runner/harnesses/pi/runtime/image/exec.sh

echo "Building distributions..."
uv build --clear --no-sources
uv run --with twine python -m twine check dist/*

if [[ "$DRY_RUN" == true ]]; then
    echo "Dry run complete; no tag was created and nothing was uploaded."
    exit 0
fi

git tag "$TAG"

# Rebuild on the tag so uv-dynamic-versioning emits the requested release version.
uv build --clear --no-sources
uv run --with twine python -m twine check dist/*

shopt -s nullglob
WHEELS=(dist/openshell_agent_runner-"$VERSION"-*.whl)
if [[ ${#WHEELS[@]} -ne 1 ]]; then
    echo "publish: expected one wheel for version '$VERSION'" >&2
    echo "publish: remove the local tag before retrying: git tag -d '$TAG'" >&2
    exit 1
fi

echo "Uploading openshell-agent-runner $VERSION with .pypirc repository '$PYPIRC_REPOSITORY'..."
if ! uv run --with twine python -m twine upload \
    --repository "$PYPIRC_REPOSITORY" dist/*; then
    echo "publish: upload failed; remove the local tag before retrying: git tag -d '$TAG'" >&2
    exit 1
fi

git push origin "$TAG"
echo "Published openshell-agent-runner $VERSION and pushed $TAG."
