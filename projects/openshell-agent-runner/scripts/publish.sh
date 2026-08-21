#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

PROJECT_DIRECTORY=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PYPIRC_REPOSITORY="openshell-research"

usage() {
    echo "Usage: $0 VERSION [--dry-run] [--allow-non-main] [--retry-artifact wheel|sdist|both]"
    echo
    echo "Build and publish openshell-agent-runner using the '$PYPIRC_REPOSITORY'"
    echo "repository configured in ~/.pypirc."
}

if [[ $# -lt 1 ]]; then
    usage >&2
    exit 2
fi

if [[ "$1" == "-h" || "$1" == "--help" ]]; then
    usage
    exit 0
fi

VERSION="$1"
DRY_RUN=false
ALLOW_NON_MAIN=false
RETRY_ARTIFACT=""
shift

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)
            DRY_RUN=true
            ;;
        --allow-non-main)
            ALLOW_NON_MAIN=true
            ;;
        --retry-artifact)
            if [[ $# -lt 2 ]]; then
                usage >&2
                exit 2
            fi
            RETRY_ARTIFACT="$2"
            shift
            ;;
        *)
            usage >&2
            exit 2
            ;;
    esac
    shift
done

if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+(rc[0-9]+)?$ ]]; then
    echo "publish: invalid version '$VERSION'; expected X.Y.Z or X.Y.ZrcN" >&2
    exit 2
fi
if [[ -n "$RETRY_ARTIFACT" && "$RETRY_ARTIFACT" != "wheel" && "$RETRY_ARTIFACT" != "sdist" && "$RETRY_ARTIFACT" != "both" ]]; then
    echo "publish: --retry-artifact must be 'wheel', 'sdist', or 'both'" >&2
    exit 2
fi
if [[ "$DRY_RUN" == true && -n "$RETRY_ARTIFACT" ]]; then
    echo "publish: --retry-artifact cannot be combined with --dry-run" >&2
    exit 2
fi

cd "$PROJECT_DIRECTORY"

if [[ -n "$(git status --porcelain)" ]]; then
    echo "publish: the working tree must be clean" >&2
    exit 1
fi

CURRENT_BRANCH=$(git branch --show-current)
if [[ "$CURRENT_BRANCH" != "main" && "$ALLOW_NON_MAIN" != true ]]; then
    echo "publish: releases must be created from main; pass --allow-non-main to override" >&2
    exit 1
fi

TAG="v$VERSION"
git fetch origin main --tags

if [[ "$ALLOW_NON_MAIN" != true ]] && \
    [[ "$(git rev-parse HEAD)" != "$(git rev-parse refs/remotes/origin/main)" ]]; then
    echo "publish: local main must match origin/main" >&2
    exit 1
fi

TAG_CREATED=false
TAG_PUBLIC=false
if git rev-parse --verify --quiet "refs/tags/$TAG" >/dev/null; then
    if [[ "$(git rev-list -n 1 "$TAG")" != "$(git rev-parse HEAD)" ]]; then
        echo "publish: tag '$TAG' exists on another commit" >&2
        exit 1
    fi
else
    git tag "$TAG"
    TAG_CREATED=true
fi

cleanup_local_tag() {
    if [[ "$TAG_CREATED" == true && "$TAG_PUBLIC" != true ]]; then
        git tag -d "$TAG" >/dev/null
    fi
}
trap cleanup_local_tag EXIT

REMOTE_TAG=$(git ls-remote --tags origin "refs/tags/$TAG" | cut -f1)
if [[ -n "$REMOTE_TAG" ]]; then
    if [[ "$REMOTE_TAG" != "$(git rev-parse HEAD)" ]]; then
        echo "publish: remote tag '$TAG' exists on another commit" >&2
        exit 1
    fi
    TAG_PUBLIC=true
fi

if [[ -n "$RETRY_ARTIFACT" && "$TAG_PUBLIC" != true ]]; then
    echo "publish: --retry-artifact requires the matching remote tag '$TAG'" >&2
    exit 1
fi
if [[ -z "$RETRY_ARTIFACT" && "$DRY_RUN" != true && "$TAG_PUBLIC" == true ]]; then
    echo "publish: remote tag '$TAG' already exists; retry the missing artifact or artifacts with --retry-artifact" >&2
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

shopt -s nullglob
WHEELS=(dist/openshell_agent_runner-"$VERSION"-*.whl)
SDISTS=(dist/openshell_agent_runner-"$VERSION".tar.gz)
if [[ ${#WHEELS[@]} -ne 1 || ${#SDISTS[@]} -ne 1 ]]; then
    echo "publish: expected one wheel and one source distribution for '$VERSION'" >&2
    exit 1
fi
ARTIFACTS=("${WHEELS[@]}" "${SDISTS[@]}")
uv run --with twine python -m twine check "${ARTIFACTS[@]}"

if [[ "$DRY_RUN" == true ]]; then
    echo "Dry run complete; no tag was created and nothing was uploaded."
    exit 0
fi

if [[ "$TAG_PUBLIC" != true ]]; then
    git push origin "$TAG"
    TAG_PUBLIC=true
fi

UPLOAD_ARTIFACTS=("${ARTIFACTS[@]}")
if [[ "$RETRY_ARTIFACT" == "wheel" ]]; then
    UPLOAD_ARTIFACTS=("${WHEELS[@]}")
elif [[ "$RETRY_ARTIFACT" == "sdist" ]]; then
    UPLOAD_ARTIFACTS=("${SDISTS[@]}")
fi

echo "Uploading openshell-agent-runner $VERSION with .pypirc repository '$PYPIRC_REPOSITORY'..."
if ! uv run --with twine python -m twine upload \
    --repository "$PYPIRC_REPOSITORY" \
    "${UPLOAD_ARTIFACTS[@]}"; then
    echo "publish: upload failed; identify the missing artifact or artifacts before retrying" >&2
    exit 1
fi

if [[ "$RETRY_ARTIFACT" == "wheel" || "$RETRY_ARTIFACT" == "sdist" ]]; then
    echo "Uploaded the missing $RETRY_ARTIFACT artifact for openshell-agent-runner $VERSION."
else
    echo "Published openshell-agent-runner $VERSION from $TAG."
fi
