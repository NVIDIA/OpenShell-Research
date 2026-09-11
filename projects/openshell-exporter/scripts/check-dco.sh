#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 <base-commit> <head-commit>" >&2
  exit 2
fi

base_commit=$1
head_commit=$2

git cat-file -e "${base_commit}^{commit}"
git cat-file -e "${head_commit}^{commit}"

mapfile -t commits < <(git rev-list --reverse "${base_commit}..${head_commit}")
if [[ ${#commits[@]} -eq 0 ]]; then
  echo "DCO check: no commits found in ${base_commit}..${head_commit}" >&2
  exit 1
fi

failed=0
for commit in "${commits[@]}"; do
  author_email=$(git show -s --format=%ae "$commit")
  subject=$(git show -s --format=%s "$commit")
  trailers=$(git show -s --format=%B "$commit" | git interpret-trailers --parse)

  if ! awk -v email="$author_email" '
    BEGIN { expected = "<" tolower(email) ">" }
    tolower($0) ~ /^signed-off-by:[[:space:]]/ && index(tolower($0), expected) > 0 {
      found = 1
    }
    END { exit(found ? 0 : 1) }
  ' <<<"$trailers"; then
    printf 'DCO check: commit %s (%s) lacks a Signed-off-by trailer for <%s>\n' \
      "$commit" "$subject" "$author_email" >&2
    failed=1
  fi
done

if [[ $failed -ne 0 ]]; then
  echo "Add the trailer with: git commit --amend --signoff" >&2
  echo "Then update the pull-request branch without discarding other contributors' work." >&2
  exit 1
fi

printf 'DCO check: %d commit(s) have author-matching Signed-off-by trailers\n' "${#commits[@]}"
