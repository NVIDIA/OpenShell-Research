#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail
umask 077

model_id=""
arguments=("$@")
for ((index = 0; index < ${#arguments[@]}; index++)); do
  if [[ "${arguments[$index]}" == "--model" && $((index + 1)) -lt ${#arguments[@]} ]]; then
    model_id="${arguments[$((index + 1))]}"
    break
  fi
done
if [[ ! "$model_id" =~ ^[A-Za-z0-9._:/-]{1,256}$ ]]; then
  echo "Pi harness: --model is missing or invalid" >&2
  exit 2
fi

payload=${OAR_RUNTIME_ROOT:-/sandbox/oar-runtime}
for required in "$payload/prompt.md" "$payload/models.json" "$payload/settings.json"; do
  if [[ ! -f "$required" ]]; then
    echo "Pi harness: missing required file: $required" >&2
    exit 2
  fi
done

pi_home=/sandbox/pi-home
mkdir -p "$pi_home/.pi/agent" /sandbox/artifacts /sandbox/tmp
install -m 0600 "$payload/models.json" "$pi_home/.pi/agent/models.json"
install -m 0600 "$payload/settings.json" "$pi_home/.pi/agent/settings.json"

export HOME="$pi_home"
export TMPDIR=/sandbox/tmp
export PI_OFFLINE=1
export PI_SKIP_VERSION_CHECK=1
export PI_TELEMETRY=0
export OAR_MODEL_ID="$model_id"

agent_workdir=${REPOSITORY_ROOT:-/sandbox}
if [[ ! -d "$agent_workdir" ]]; then
  echo "Pi harness: REPOSITORY_ROOT is not a directory: $agent_workdir" >&2
  exit 2
fi
cd "$agent_workdir"

stdout_path=/sandbox/artifacts/result.stdout
result_path=/sandbox/artifacts/result
pi \
  --print \
  --no-session \
  --no-extensions \
  --no-skills \
  --no-prompt-templates \
  --no-themes \
  --no-context-files \
  --no-approve \
  --offline \
  "$@" \
  <"$payload/prompt.md" \
  >"$stdout_path"

if [[ -s "$result_path" ]]; then
  rm -f "$stdout_path"
else
  mv "$stdout_path" "$result_path"
fi
