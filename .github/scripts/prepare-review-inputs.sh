#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Prepare data from a disposable checkout without running any PR code.
set -euo pipefail

if [[ $# != 4 ]]; then
  echo "Usage: prepare-review-inputs.sh CHECKOUT NEW_OUTPUT_ROOT BASE_SHA HEAD_SHA" >&2
  exit 2
fi
checkout=$1
output_root=$2
base_sha=$3
head_sha=$4
if [[ ! -d "$checkout/.git" || -L "$checkout/.git" ]]; then
  echo "Expected a disposable checkout with its own .git directory." >&2
  exit 2
fi
if [[ ! "$base_sha" =~ ^[a-f0-9]{40}$ || ! "$head_sha" =~ ^[a-f0-9]{40}$ ]]; then
  echo "Expected exact base and head commit SHAs." >&2
  exit 2
fi
git -C "$checkout" cat-file -e "$base_sha^{commit}"
git -C "$checkout" cat-file -e "$head_sha^{commit}"

attributes="$checkout/.git/info/attributes"
if [[ -e "$attributes" || -L "$attributes" ]]; then
  echo "Refusing to replace existing checkout-local attribute overrides." >&2
  exit 2
fi
# Requiring a fresh destination ensures link removal touches only our snapshot.
mkdir -- "$output_root"
mkdir -- "$output_root/source" "$output_root/review-context"
mkdir -p -- "$checkout/.git/info"
printf '%s\n' '* -export-ignore -export-subst' > "$attributes"
trap 'rm -f -- "$attributes"' EXIT

# info/attributes has precedence over tracked attributes, including nested
# .gitattributes. Preserve every tracked file and its original committed bytes.
git -C "$checkout" -c core.attributesFile=/dev/null archive "$head_sha" \
  | tar -x --no-same-owner --no-same-permissions -C "$output_root/source"
find "$output_root/source" -type l -printf '%P -> %l\n' \
  > "$output_root/review-context/omitted-symlinks.txt"
find "$output_root/source" -type l -delete

# Gitlinks contain no source bytes in this commit; disclose that missing scope.
while IFS= read -r -d '' entry; do
  if [[ "$entry" == "160000 "* ]]; then
    printf '%s (git submodule; contents not included)\n' "${entry#*$'\t'}" \
      >> "$output_root/review-context/omitted-symlinks.txt"
  fi
done < <(git -C "$checkout" ls-tree -rz "$head_sha")
{
  printf 'Changed files:\n'
  git -C "$checkout" diff --no-ext-diff --no-textconv --name-status \
    --no-renames "$base_sha...$head_sha"
  printf '\nDiff statistics:\n'
  git -C "$checkout" diff --no-ext-diff --no-textconv --stat --summary \
    --no-renames "$base_sha...$head_sha"
} > "$output_root/review-context/changes-summary.txt"
