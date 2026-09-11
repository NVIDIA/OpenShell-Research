#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

# Shared deployment policy: local images never trigger registry access; remote
# images must be pinned to the digest from the user's own publication.
fail() { printf 'image: %s\n' "$*" >&2; exit 1; }
[[ $# == 3 ]] || fail 'usage: prepare-image.sh docker|podman IMAGE never|missing|always'
engine=$1 ref=$2 policy=$3
case "$engine" in docker|podman) ;; *) fail 'unsupported engine' ;; esac
[[ "$ref" != -* && "$ref" != *[[:space:]]* ]] || fail 'invalid image reference'
case "$policy" in
  never)
    [[ "$ref" =~ ^sha256:[0-9a-f]{64}$ || "$ref" =~ ^.+@sha256:[0-9a-f]{64}$ || "$ref" =~ ^[^@]+:[A-Za-z0-9_][A-Za-z0-9_.-]*$ ]] ||
      fail 'local images need an explicit tag or sha256 identity'
    "$engine" image inspect "$ref" >/dev/null 2>&1 ||
      fail "local image $ref is missing; build it with scripts/build-images.sh --engine $engine (no pull attempted)"
    ;;
  missing|always)
    [[ "$ref" =~ ^.+@sha256:[0-9a-f]{64}$ ]] || fail 'registry images require an immutable sha256 digest with 64 lowercase hex characters'
    if [[ "$policy" == always ]] || ! "$engine" image inspect "$ref" >/dev/null 2>&1; then
      "$engine" pull "$ref" >/dev/null || fail 'unable to pull your image; check registry authentication and digest'
    fi
    ;;
  *) fail 'pull policy must be never, missing, or always' ;;
esac
