#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

if (( $# < 3 || $# > 4 )); then
  echo "usage: $0 URL SHA256 OUTPUT [CA_BUNDLE]" >&2
  exit 2
fi

url=$1
expected_sha256=$2
output=$3
ca_bundle=${4:-}

[[ "$url" == https://* ]] || { echo "artifact URL must use HTTPS" >&2; exit 2; }
[[ "$expected_sha256" =~ ^[0-9a-f]{64}$ ]] \
  || { echo "artifact SHA-256 must be 64 lowercase hexadecimal characters" >&2; exit 2; }
if [[ -n "$ca_bundle" ]]; then
  [[ -r "$ca_bundle" ]] || { echo "CA bundle is not readable: $ca_bundle" >&2; exit 2; }
  grep -q -- "-----BEGIN CERTIFICATE-----" "$ca_bundle" \
    || { echo "CA bundle contains no PEM certificate: $ca_bundle" >&2; exit 2; }
fi

mkdir -p "$(dirname -- "$output")"

sha256_matches() {
  local file=$1 actual
  [[ -f "$file" ]] || return 1
  actual=$(sha256sum "$file" | awk '{print $1}')
  [[ "$actual" == "$expected_sha256" ]]
}

if sha256_matches "$output"; then
  printf 'using cached pinned artifact %s\n' "$output" >&2
  exit 0
fi

temporary=$(mktemp "${output}.download.XXXXXX")
cleanup() {
  rm -f -- "$temporary"
}
trap cleanup EXIT

curl_args=(
  --fail
  --location
  --proto '=https'
  --tlsv1.2
  --retry 5
  --retry-delay 2
  --retry-all-errors
  --silent
  --show-error
)
if [[ -n "$ca_bundle" ]]; then
  curl_args+=(--cacert "$ca_bundle")
fi

printf 'downloading pinned artifact %s\n' "$url" >&2
curl "${curl_args[@]}" --output "$temporary" "$url"
sha256_matches "$temporary" || {
  actual=$(sha256sum "$temporary" | awk '{print $1}')
  printf 'checksum mismatch for %s: expected %s, got %s\n' \
    "$url" "$expected_sha256" "$actual" >&2
  exit 1
}
chmod 0644 "$temporary"
mv -f -- "$temporary" "$output"
trap - EXIT
