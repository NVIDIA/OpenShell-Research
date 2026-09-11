#!/bin/sh
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH= cd -- "$script_dir/.." && pwd)
version=$(tr -d '\r\n' < "$repo_root/VERSION")

release_url="https://github.com/NVIDIA/OpenShell-Research/releases"
for path in "$repo_root/README.md" "$repo_root/docs/compatibility.md"; do
  grep -Fiq 'experimental research example' "$path" || {
    printf '%s\n' "$path: missing experimental research status" >&2
    exit 1
  }
  grep -Fq "$release_url" "$path" || {
    printf '%s\n' "$path: missing v$version release link" >&2
    exit 1
  }
done

for event_type in \
  com.nvidia.openshell.policy.draft_updated.v1 \
  com.nvidia.openshell.policy.draft.snapshot.v1 \
  com.nvidia.openshell.policy.draft.chunk.v1 \
  com.nvidia.openshell.policy.draft.history.v1 \
  com.nvidia.openshell.policy.status.v1 \
  com.nvidia.openshell.policy.revision.v1 \
  com.nvidia.openshell.policy.reconciliation.warning.v1
do
  grep -Fq "$event_type" "$repo_root/docs/event-model.md" || {
    printf '%s\n' "docs/event-model.md: missing $event_type" >&2
    exit 1
  }
done

grep -Fq 'v0.0.113' "$repo_root/docs/compatibility.md"
grep -Fq '1.8.0' "$repo_root/docs/compatibility.md"

printf '%s\n' 'documentation consistency: release, compatibility, and policy contract are aligned'
