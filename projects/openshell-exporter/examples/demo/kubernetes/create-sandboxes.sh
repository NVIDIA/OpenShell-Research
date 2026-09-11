#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/sandbox-control-lib.sh"

usage() {
  cat <<'EOF'
Usage: ./create-sandboxes.sh [COUNT] [PREFIX]

Create 1-20 additional real OpenShell sandboxes. Every sandbox receives the
demo's Hermes/NeMo Relay image and the injected Fluent Bit evidence sidecar.

COUNT   Number of sandboxes to create (default: 3)
PREFIX  Lowercase DNS-label prefix (default: soc-agent)

Names include a compact UTC time and sequence number, so this command never
replaces an existing sandbox.
EOF
}

if [[ ${1:-} == -h || ${1:-} == --help ]]; then
  usage
  exit 0
fi

count=${1:-3}
prefix=${2:-soc-agent}
[[ "$count" =~ ^[0-9]+$ ]] || fail "COUNT must be an integer"
((count >= 1 && count <= 20)) || fail "COUNT must be between 1 and 20"
validate_sandbox_name "$prefix"
((${#prefix} <= 9)) || fail "PREFIX must contain at most 9 characters"

stamp=$(date -u '+%H%M%S')
longest_name="$prefix-$stamp-$(printf '%02d' "$count")"
validate_sandbox_name "$longest_name"

trap stop_sandbox_control EXIT INT TERM
start_sandbox_control

printf 'NAME\tSANDBOX_ID\tPHASE\n'
for ((index = 1; index <= count; index++)); do
  printf -v sequence '%02d' "$index"
  name="$prefix-$stamp-$sequence"
  sandbox_json=$(create_demo_sandbox "$name" kubernetes-hermes-relay-soc-multi)
  sandbox_id=$(jq -er '.id' <<<"$sandbox_json")
  phase=$(jq -er '.phase' <<<"$sandbox_json")
  printf '%s\t%s\t%s\n' "$name" "$sandbox_id" "$phase"
done

note "created $count sandbox(es); use ./hermes-terminal.sh <name> to enter one"
