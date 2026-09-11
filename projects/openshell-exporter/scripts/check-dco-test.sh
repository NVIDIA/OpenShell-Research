#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
fixture=$(mktemp -d)
cleanup() { rm -rf "$fixture"; }
trap cleanup EXIT

git -C "$fixture" init -q
git -C "$fixture" config user.name "DCO Test"
git -C "$fixture" config user.email "dco-test@example.com"

git -C "$fixture" commit -q --allow-empty --signoff -m "test: establish base"
base_commit=$(git -C "$fixture" rev-parse HEAD)

git -C "$fixture" commit -q --allow-empty --signoff -m "test: accept matching signoff"
signed_head=$(git -C "$fixture" rev-parse HEAD)
(
  cd "$fixture"
  "$repo_root/scripts/check-dco.sh" "$base_commit" "$signed_head" >/dev/null
)

git -C "$fixture" commit -q --allow-empty -m "test: reject missing signoff"
unsigned_head=$(git -C "$fixture" rev-parse HEAD)
if (
  cd "$fixture"
  "$repo_root/scripts/check-dco.sh" "$signed_head" "$unsigned_head" >/dev/null 2>&1
); then
  echo "DCO test: unsigned commit was accepted" >&2
  exit 1
fi

git -C "$fixture" commit -q --allow-empty \
  -m "test: reject mismatched signoff" \
  -m "Signed-off-by: Another Person <another@example.com>"
mismatched_head=$(git -C "$fixture" rev-parse HEAD)
if (
  cd "$fixture"
  "$repo_root/scripts/check-dco.sh" "$unsigned_head" "$mismatched_head" >/dev/null 2>&1
); then
  echo "DCO test: mismatched signoff was accepted" >&2
  exit 1
fi

echo "DCO tests passed"
