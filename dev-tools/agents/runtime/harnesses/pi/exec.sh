#!/usr/bin/env bash
set -euo pipefail
umask 077

if [[ "$#" -lt 2 ]]; then
  echo "usage: exec.sh PROMPT_FILE MODEL_ID [PI_RESOURCE_ARGS...]" >&2
  exit 2
fi
prompt_file="$1"
model_id="$2"
shift 2
if [[ ! "$model_id" =~ ^[A-Za-z0-9._:/-]{1,128}$ ]]; then
  echo "Pi harness: model ID is invalid" >&2
  exit 2
fi

payload=/etc/openshell/agent-payload
for required in "$prompt_file" "$payload/models.json" "$payload/settings.json"; do
  if [[ ! -f "$required" ]]; then
    echo "Pi harness: missing required file: $required" >&2
    exit 2
  fi
done
pi_home=/tmp/repository-agent-home
mkdir -p "$pi_home/.pi/agent"
install -m 0600 "$payload/models.json" "$pi_home/.pi/agent/models.json"
install -m 0600 "$payload/settings.json" "$pi_home/.pi/agent/settings.json"

export HOME="$pi_home"
export PI_OFFLINE=1
export PI_SKIP_VERSION_CHECK=1
export PI_TELEMETRY=0
exec pi \
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
  --provider repository-agent \
  --model "$model_id" \
  <"$prompt_file"
