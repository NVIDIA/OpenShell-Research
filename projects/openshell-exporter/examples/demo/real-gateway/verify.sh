#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail
umask 077

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ENV_FILE="$SCRIPT_DIR/runtime/demo.env"
[ -f "$ENV_FILE" ] || { echo "run ./run.sh first" >&2; exit 1; }
REQUESTED_DEMO_AGENT_SESSION_ID=${DEMO_AGENT_SESSION_ID:-}
# shellcheck disable=SC1090
source "$ENV_FILE"
RECORDED_DEMO_AGENT_SESSION_ID=${DEMO_AGENT_SESSION_ID:-}
export DEMO_REPOSITORY_COMMIT=${DEMO_REPOSITORY_COMMIT:-unknown}
export DEMO_EXPORTER_VERSION=${DEMO_EXPORTER_VERSION:-unknown}
export DEMO_BUILD_CREATED=${DEMO_BUILD_CREATED:-unknown}
DEMO_TASK_STARTED_AT=${DEMO_TASK_STARTED_AT:-}
DEMO_TASK_FINISHED_AT=${DEMO_TASK_FINISHED_AT:-}
DEMO_TASK_FILE_SHA256=${DEMO_TASK_FILE_SHA256:-}
DEMO_AGENT_SESSION_ID=${REQUESTED_DEMO_AGENT_SESSION_ID:-$RECORDED_DEMO_AGENT_SESSION_ID}
DEMO_RELAY_SESSION_INSTANCE_ID=${DEMO_RELAY_SESSION_INSTANCE_ID:-}
[ "$DEMO_AGENT_SESSION_ID" = "$RECORDED_DEMO_AGENT_SESSION_ID" ] \
  || { echo "requested Hermes session does not match the ATIF-proven run" >&2; exit 1; }
OPENSHELL_DEMO_POLICY_VERSION=${OPENSHELL_DEMO_POLICY_VERSION:-}
[ -n "$DEMO_TASK_STARTED_AT" ] || { echo "demo task start time is missing; rerun ./run.sh" >&2; exit 1; }
[ -n "$DEMO_TASK_FINISHED_AT" ] || { echo "demo task finish time is missing; rerun ./run.sh" >&2; exit 1; }
[[ "$DEMO_TASK_FILE_SHA256" =~ ^[0-9a-f]{64}$ ]] || { echo "demo task file identity is missing; rerun ./run.sh" >&2; exit 1; }
DEMO_EXTERNAL_DESTINATIONS=${DEMO_EXTERNAL_DESTINATIONS:-false}
COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$SCRIPT_DIR/compose.yaml")
if [ "$DEMO_EXTERNAL_DESTINATIONS" = true ]; then
  COMPOSE+=(-f "$SCRIPT_DIR/compose.external.yaml")
fi
sandbox_ssh_exec() {
  "${COMPOSE[@]}" run --rm \
    --entrypoint /demo/control/sandbox-ssh-exec.sh \
    control "$@"
}

curl --fail --silent --show-error http://127.0.0.1:8081/healthz >/dev/null
curl --fail --silent --show-error "http://127.0.0.1:${DEMO_HEALTH_PORT:-13133}/" >/dev/null
stats=$(curl --fail --silent --show-error --cacert "$SCRIPT_DIR/runtime/tls/ca.crt" \
  https://127.0.0.1:8088/api/stats)
evidence=$(curl --fail --silent --show-error --cacert "$SCRIPT_DIR/runtime/tls/ca.crt" \
  'https://127.0.0.1:8088/api/events?limit=2000')
window_evidence=$(printf '%s' "$evidence" | jq --arg start "$DEMO_TASK_STARTED_AT" --arg finish "$DEMO_TASK_FINISHED_AT" -f "$SCRIPT_DIR/task-window.jq")
[[ "$DEMO_AGENT_SESSION_ID" =~ ^[A-Za-z0-9._:-]{1,256}$ ]] || { echo "Hermes session ID is invalid" >&2; exit 1; }
[[ "$DEMO_RELAY_SESSION_INSTANCE_ID" =~ ^[A-Za-z0-9._:-]{1,256}$ ]] \
  || { echo "ATIF-proven Relay session instance ID is invalid; rerun ./run.sh" >&2; exit 1; }
atif_documents=$(sandbox_ssh_exec hermes-demo \
  /bin/cat '/sandbox/telemetry/atif/hermes-atif-*.json')
atif_bridge_matches=$(printf '%s\n' "$atif_documents" | jq -sr \
  --arg session "$DEMO_AGENT_SESSION_ID" \
  --arg relay "$DEMO_RELAY_SESSION_INSTANCE_ID" '
    [
      .[]
      | select(
          [.extra.observed_events[]? | .data.session_id?, .metadata.session_id?]
          | index($session) != null
        )
      | select((.extra.nemo_relay.session_instance_id // "") == $relay)
    ]
    | length
  ')
unset atif_documents
[ "$atif_bridge_matches" -eq 1 ] \
  || { echo "ATIF does not prove exactly one Hermes-to-Relay session bridge" >&2; exit 1; }
task_evidence=$(printf '%s' "$window_evidence" | jq --arg sandbox "$OPENSHELL_DEMO_SANDBOX_ID" --arg session "$DEMO_RELAY_SESSION_INSTANCE_ID" '[.[] | select((.kind == "openshell_event" and .correlation["openshell.sandbox.id"] == $sandbox) or (.kind == "nemo_relay_trace" and (((.correlation["agent.session.id"] // "") == $session) or ((.correlation["agent.session.id"] // "") == ""))))]')
traces=$(printf '%s' "$task_evidence" | jq '[.[] | select(.kind == "nemo_relay_trace")] | length')
logs=$(printf '%s' "$task_evidence" | jq '[.[] | select(.kind == "openshell_event")] | length')
correlated_logs=$(printf '%s' "$task_evidence" | jq --arg sandbox "$OPENSHELL_DEMO_SANDBOX_ID" '[.[] | select(.kind == "openshell_event" and .correlation["openshell.sandbox.id"] == $sandbox)] | length')
relay_sandbox_traces=$(printf '%s' "$task_evidence" | jq --arg sandbox "$OPENSHELL_DEMO_SANDBOX_ID" '[.[] | select(.kind == "nemo_relay_trace" and .correlation["openshell.sandbox.id"] == $sandbox)] | length')
relay_session_traces=$(printf '%s' "$task_evidence" | jq '[.[] | select(.kind == "nemo_relay_trace" and (.correlation["agent.session.id"] // "") != "")] | length')
correlated_traces=$(printf '%s' "$task_evidence" | jq --arg sandbox "$OPENSHELL_DEMO_SANDBOX_ID" '[.[] | select(.kind == "nemo_relay_trace" and .correlation["openshell.sandbox.id"] == $sandbox and (.correlation["agent.session.id"] // "") != "")] | length')
privacy_leak_keys=$(printf '%s' "$task_evidence" | jq '[
  .[]
  | select(.kind == "nemo_relay_trace")
  | .payload
  | ..
  | objects
  | to_entries[]?
  | . as $entry
  | (.key | ascii_downcase) as $key
  | select(
      $key
      | test("(^|[._-])(authorization|credential|password|prompt|secret)([._-]|$)|(^|[._-])(input|output)[._-](value|content|message|messages)([._-]|$)|response.*content|tool.*(argument|parameter|result)|nemo_relay\\.mark\\.data|error\\.(message|stack)")
    )
  | select(
      (
        (($entry.value | type) == "number")
        and (
          ($key == "tokens_in")
          or ($key == "tokens_out")
          or ($key | startswith("gen_ai.usage."))
          or ($key | startswith("llm.token_count."))
          or ($key | startswith("nemo_relay.llm.token_count."))
          or ($key | startswith("llm.cost."))
          or ($key | startswith("nemo_relay.llm.cost."))
        )
      )
      or (($key == "token_type") and (($entry.value | type) == "string"))
      | not
    )
  | .key
] | unique')
privacy_leaks=$(printf '%s' "$privacy_leak_keys" | jq 'length')
invalid=$(printf '%s' "$task_evidence" | jq '[.[] | select(.valid == false)] | length')
relay_partial_session_spans=$(printf '%s' "$task_evidence" | jq --arg sandbox "$OPENSHELL_DEMO_SANDBOX_ID" '[.[] | select(.kind == "nemo_relay_trace" and .correlation["openshell.sandbox.id"] == $sandbox and (.correlation["agent.session.id"] // "") == "")] | length')
relay_diagnosed_partial_spans=$(printf '%s' "$task_evidence" | jq --arg sandbox "$OPENSHELL_DEMO_SANDBOX_ID" '[.[] | select(
  .kind == "nemo_relay_trace"
  and .correlation["openshell.sandbox.id"] == $sandbox
  and (.correlation["agent.session.id"] // "") == ""
  and .payload.attributes["openshell.correlation.status"] == "partial"
  and (((.payload.attributes["openshell.correlation.missing"] // "") | split(",")) | index("agent.session.id") != null)
)] | length')

relay_sessions=$(printf '%s' "$task_evidence" | jq '[.[] | select(.kind == "nemo_relay_trace") | .correlation["agent.session.id"] // empty] | unique | length')
relay_prompt_spans=$(printf '%s' "$task_evidence" | jq '[.[] | select(.kind == "nemo_relay_trace" and .stage == "prompt")] | length')
relay_model_spans=$(printf '%s' "$task_evidence" | jq '[.[] | select(.kind == "nemo_relay_trace" and .stage == "model")] | length')
relay_tool_spans=$(printf '%s' "$task_evidence" | jq '[.[] | select(.kind == "nemo_relay_trace" and .stage == "tool")] | length')
relay_complete_prompt_spans=$(printf '%s' "$task_evidence" | jq --arg sandbox "$OPENSHELL_DEMO_SANDBOX_ID" '[.[] | select(.kind == "nemo_relay_trace" and .stage == "prompt" and .correlation["openshell.sandbox.id"] == $sandbox and (.correlation["agent.session.id"] // "") != "")] | length')
relay_complete_model_spans=$(printf '%s' "$task_evidence" | jq --arg sandbox "$OPENSHELL_DEMO_SANDBOX_ID" '[.[] | select(.kind == "nemo_relay_trace" and .stage == "model" and .correlation["openshell.sandbox.id"] == $sandbox and (.correlation["agent.session.id"] // "") != "")] | length')
relay_complete_tool_spans=$(printf '%s' "$task_evidence" | jq --arg sandbox "$OPENSHELL_DEMO_SANDBOX_ID" '[.[] | select(.kind == "nemo_relay_trace" and .stage == "tool" and .correlation["openshell.sandbox.id"] == $sandbox and (.correlation["agent.session.id"] // "") != "")] | length')
relay_diagnosed_partial_prompt_spans=$(printf '%s' "$task_evidence" | jq --arg sandbox "$OPENSHELL_DEMO_SANDBOX_ID" '[.[] | select(.kind == "nemo_relay_trace" and .stage == "prompt" and .correlation["openshell.sandbox.id"] == $sandbox and (.correlation["agent.session.id"] // "") == "" and .payload.attributes["openshell.correlation.status"] == "partial" and (((.payload.attributes["openshell.correlation.missing"] // "") | split(",")) | index("agent.session.id") != null))] | length')
relay_diagnosed_partial_model_spans=$(printf '%s' "$task_evidence" | jq --arg sandbox "$OPENSHELL_DEMO_SANDBOX_ID" '[.[] | select(.kind == "nemo_relay_trace" and .stage == "model" and .correlation["openshell.sandbox.id"] == $sandbox and (.correlation["agent.session.id"] // "") == "" and .payload.attributes["openshell.correlation.status"] == "partial" and (((.payload.attributes["openshell.correlation.missing"] // "") | split(",")) | index("agent.session.id") != null))] | length')
relay_diagnosed_partial_tool_spans=$(printf '%s' "$task_evidence" | jq --arg sandbox "$OPENSHELL_DEMO_SANDBOX_ID" '[.[] | select(.kind == "nemo_relay_trace" and .stage == "tool" and .correlation["openshell.sandbox.id"] == $sandbox and (.correlation["agent.session.id"] // "") == "" and .payload.attributes["openshell.correlation.status"] == "partial" and (((.payload.attributes["openshell.correlation.missing"] // "") | split(",")) | index("agent.session.id") != null))] | length')
ocsf_records=$(printf '%s' "$task_evidence" | jq '[.[] | select(.kind == "openshell_event" and (.type | startswith("com.nvidia.openshell.ocsf.")))] | length')
ocsf_process_records=$(printf '%s' "$task_evidence" | jq '[.[] | select(.type == "com.nvidia.openshell.ocsf.1007.v1")] | length')
ocsf_network_records=$(printf '%s' "$task_evidence" | jq '[.[] | select(.type == "com.nvidia.openshell.ocsf.4001.v1")] | length')
ocsf_file_records=$(printf '%s' "$task_evidence" | jq '[.[] | select(.type == "com.nvidia.openshell.ocsf.1001.v1")] | length')
watchsandbox_records=$(printf '%s' "$task_evidence" | jq '[.[] | select(.kind == "openshell_event" and (.type | startswith("com.nvidia.openshell.ocsf.") | not))] | length')
request_identity_records=$(printf '%s' "$task_evidence" | jq '[.[] | select((.correlation.request_id // "") != "")] | length')
tool_identity_records=$(printf '%s' "$task_evidence" | jq '[.[] | select((.correlation.tool_call_id // "") != "")] | length')
policy_identity_records=$(printf '%s' "$task_evidence" | jq '[.[] | select((.correlation["openshell.policy.version"] // "") != "")] | length')
relay_policy_traces=$(printf '%s' "$task_evidence" | jq --arg version "$OPENSHELL_DEMO_POLICY_VERSION" '[.[] | select(.kind == "nemo_relay_trace" and ((.correlation["openshell.policy.version"] // "") | tostring) == $version)] | length')
relay_latency_spans=$(printf '%s' "$task_evidence" | jq '[.[] | select(.kind == "nemo_relay_trace" and (.duration_ms // 0) > 0)] | length')
relay_token_spans=$(printf '%s' "$task_evidence" | jq '[.[] | select(.kind == "nemo_relay_trace" and .token_usage_status == "observed")] | length')
relay_token_unavailable_spans=$(printf '%s' "$task_evidence" | jq '[.[] | select(.kind == "nemo_relay_trace" and .token_usage_status == "not_emitted")] | length')
relay_token_status_spans=$((relay_token_spans + relay_token_unavailable_spans))
policy_denials=$(printf '%s' "$task_evidence" | jq '[.[] | select(.kind == "openshell_event" and .outcome == "denied")] | length')
relay_failures=$(printf '%s' "$task_evidence" | jq '[.[] | select(.kind == "nemo_relay_trace" and .outcome == "error")] | length')

[ "$logs" -gt 0 ] || { echo "no real OpenShell CloudEvents reached the conformance receiver" >&2; exit 1; }
[ "$traces" -gt 0 ] || { echo "no real Hermes/NeMo Relay spans reached the conformance receiver" >&2; exit 1; }
[ "$correlated_logs" -eq "$logs" ] || { echo "task evidence included OpenShell records without the authoritative sandbox identity" >&2; exit 1; }
[ "$correlated_logs" -gt 0 ] || { echo "no OpenShell evidence carried the real sandbox ID" >&2; exit 1; }
[ "$relay_sandbox_traces" -gt 0 ] || { echo "no Relay trace carried the real sandbox ID" >&2; exit 1; }
[ "$relay_session_traces" -gt 0 ] || { echo "no Relay trace carried a normalized Relay session-instance ID" >&2; exit 1; }
[ "$correlated_traces" -gt 0 ] || { echo "no Relay trace carried both the real sandbox ID and Relay session-instance ID" >&2; exit 1; }
[ "$relay_sandbox_traces" -eq "$traces" ] || { echo "not every Relay span carried the authoritative sandbox identity" >&2; exit 1; }
[ $((correlated_traces + relay_partial_session_spans)) -eq "$traces" ] || { echo "Relay correlation accounting is incomplete" >&2; exit 1; }
[ "$relay_diagnosed_partial_spans" -eq "$relay_partial_session_spans" ] || { echo "a Relay session-identity gap was not explicitly diagnosed" >&2; exit 1; }
if [ -n "$OPENSHELL_DEMO_POLICY_VERSION" ]; then
  [ "$relay_policy_traces" -eq "$traces" ] || {
    echo "not every Relay span carried the gateway-reported current policy version" >&2
    exit 1
  }
fi
[ "$relay_sessions" -eq 1 ] || { echo "expected exactly one correlated Relay session instance, got $relay_sessions" >&2; exit 1; }
[ "$relay_prompt_spans" -gt 0 ] || { echo "no Relay prompt/agent stage reached the conformance receiver" >&2; exit 1; }
[ $((relay_complete_prompt_spans + relay_diagnosed_partial_prompt_spans)) -eq "$relay_prompt_spans" ] || { echo "a Relay prompt/agent span was neither fully correlated nor explicitly diagnosed" >&2; exit 1; }
[ "$relay_model_spans" -gt 0 ] || { echo "no Relay model stage reached the conformance receiver" >&2; exit 1; }
[ $((relay_complete_model_spans + relay_diagnosed_partial_model_spans)) -eq "$relay_model_spans" ] || { echo "a Relay model span was neither fully correlated nor explicitly diagnosed" >&2; exit 1; }
[ "$relay_tool_spans" -gt 0 ] || { echo "no Relay tool stage reached the conformance receiver" >&2; exit 1; }
[ $((relay_complete_tool_spans + relay_diagnosed_partial_tool_spans)) -eq "$relay_tool_spans" ] || { echo "a Relay tool span was neither fully correlated nor explicitly diagnosed" >&2; exit 1; }
[ "$relay_latency_spans" -gt 0 ] || { echo "no Relay span carried measurable latency" >&2; exit 1; }
[ "$relay_token_status_spans" -eq "$traces" ] || { echo "Relay token capability was not classified on every span" >&2; exit 1; }
[ "$policy_denials" -gt 0 ] || { echo "no OpenShell evidence was classified as an expected policy denial" >&2; exit 1; }
[ "$watchsandbox_records" -gt 0 ] || { echo "no WatchSandbox evidence reached the conformance receiver" >&2; exit 1; }
[ "$ocsf_records" -gt 0 ] || { echo "no OCSF file evidence reached the conformance receiver" >&2; exit 1; }
process_observation_status=observed
if [ "$ocsf_process_records" -eq 0 ]; then
  process_observation_status=declared_unobserved
fi
[ "$ocsf_network_records" -gt 0 ] || { echo "no OCSF network activity reached the conformance receiver" >&2; exit 1; }
[ "$invalid" -eq 0 ] || { echo "structurally invalid OCSF evidence reached the conformance receiver: $invalid" >&2; exit 1; }
[ "$privacy_leaks" -eq 0 ] || {
  privacy_leak_names=$(printf '%s' "$privacy_leak_keys" | jq -r 'join(", ")')
  echo "privacy-denied Relay attribute keys reached the conformance receiver: $privacy_leak_names" >&2
  exit 1
}
[ -s "$SCRIPT_DIR/runtime/recovery/openshell-events.json" ] || { echo "event recovery archive is empty" >&2; exit 1; }
[ -s "$SCRIPT_DIR/runtime/recovery/nemo-relay-traces.json" ] || { echo "trace recovery archive is empty" >&2; exit 1; }

sha256_file() {
  openssl dgst -sha256 "$1" | awk '{print $NF}'
}

repo_root=$(cd -- "$SCRIPT_DIR/../../.." && pwd)
repository_commit=$(git -C "$repo_root" rev-parse HEAD)
repository_branch=$(git -C "$repo_root" symbolic-ref --quiet --short HEAD || printf 'detached')
worktree_clean=true
if [ -n "$(git -C "$repo_root" status --porcelain --untracked-files=normal)" ]; then
  worktree_clean=false
fi

exporter_container_id=$(docker compose --env-file "$ENV_FILE" -f "$SCRIPT_DIR/compose.yaml" ps -q exporter)
gateway_container_id=$(docker compose --env-file "$ENV_FILE" -f "$SCRIPT_DIR/compose.yaml" ps -q gateway)
conformance_receiver_container_id=$(docker compose --env-file "$ENV_FILE" -f "$SCRIPT_DIR/compose.yaml" ps -q conformance-receiver)
[ -n "$exporter_container_id" ] || { echo "exporter container is missing" >&2; exit 1; }
[ -n "$gateway_container_id" ] || { echo "gateway container is missing" >&2; exit 1; }
[ -n "$conformance_receiver_container_id" ] || { echo "conformance receiver container is missing" >&2; exit 1; }

exporter_image_id=$(docker inspect --format '{{.Image}}' "$exporter_container_id")
exporter_image_revision=$(docker inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$exporter_container_id")
exporter_image_version=$(docker inspect --format '{{index .Config.Labels "org.opencontainers.image.version"}}' "$exporter_container_id")
exporter_image_created=$(docker inspect --format '{{index .Config.Labels "org.opencontainers.image.created"}}' "$exporter_container_id")
gateway_image_id=$(docker inspect --format '{{.Image}}' "$gateway_container_id")
gateway_image_ref=$(docker inspect --format '{{.Config.Image}}' "$gateway_container_id")
conformance_receiver_image_id=$(docker inspect --format '{{.Image}}' "$conformance_receiver_container_id")
hermes_image_id=$(docker image inspect --format '{{.Id}}' openshell-exporter-demo-hermes:0.20.2)
hermes_image_revision=${hermes_image_id#sha256:}
hermes_image_revision=${hermes_image_revision:0:24}
sandbox_image_revision=$(jq -r '.labels.demoimage // empty' "$SCRIPT_DIR/runtime/sandbox.json")

candidate_bound=false
if [ "$worktree_clean" = true ] \
  && [[ "$repository_commit" =~ ^[0-9a-f]{40}$ ]] \
  && [[ "$exporter_image_id" =~ ^sha256:[0-9a-f]{64}$ ]] \
  && [ "$exporter_image_revision" = "$repository_commit" ] \
  && [ -n "$exporter_image_version" ] \
  && [ -n "$exporter_image_created" ] \
  && [[ "$gateway_image_id" =~ ^sha256:[0-9a-f]{64}$ ]] \
  && [[ "$gateway_image_ref" =~ @sha256:[0-9a-f]{64}$ ]] \
  && [[ "$conformance_receiver_image_id" =~ ^sha256:[0-9a-f]{64}$ ]] \
  && [ "$sandbox_image_revision" = "$hermes_image_revision" ] \
  && [[ "$hermes_image_id" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  candidate_bound=true
fi

relay_session_id=$(printf '%s' "$task_evidence" | jq -r '[.[] | select(.kind == "nemo_relay_trace") | .correlation["agent.session.id"] // empty] | unique | if length == 1 then .[0] else empty end')
[ "$relay_session_id" = "$DEMO_RELAY_SESSION_INSTANCE_ID" ] || { echo "qualified Relay session does not match the ATIF-proven Relay session instance" >&2; exit 1; }
relay_trace_ids=$(printf '%s' "$task_evidence" | jq -c '[.[] | select(.kind == "nemo_relay_trace") | .correlation.trace_id // empty] | unique')
request_ids=$(printf '%s' "$task_evidence" | jq -c '[.[] | .correlation.request_id // empty] | unique')
tool_call_ids=$(printf '%s' "$task_evidence" | jq -c '[.[] | .correlation.tool_call_id // empty] | unique')
policy_versions=$(printf '%s' "$task_evidence" | jq -c '[.[] | .correlation["openshell.policy.version"] // empty | tostring] | unique')
gateway_id=$(printf '%s' "$task_evidence" | jq -r '[.[] | .correlation["openshell.gateway.id"] // empty] | unique | if length == 1 then .[0] else empty end')
workspace=$(printf '%s' "$task_evidence" | jq -r '[.[] | .correlation["openshell.workspace"] // empty] | unique | if length == 1 then .[0] else empty end')
[ -n "$gateway_id" ] || { echo "task evidence does not have one stable gateway identity" >&2; exit 1; }
[ -n "$workspace" ] || { echo "task evidence does not have one stable workspace identity" >&2; exit 1; }
cloud_event_identities=$(printf '%s' "$task_evidence" | jq -c '
  [.[] | select(.kind == "openshell_event") | {source, id, type,
    correlation: {
      "openshell.gateway.id": (.correlation["openshell.gateway.id"] // ""),
      "openshell.workspace": (.correlation["openshell.workspace"] // ""),
      "openshell.sandbox.id": (.correlation["openshell.sandbox.id"] // ""),
      "agent.session.id": (.correlation["agent.session.id"] // ""),
      "trace_id": (.correlation.trace_id // ""),
      "request_id": (.correlation.request_id // ""),
      "tool_call_id": (.correlation.tool_call_id // ""),
      "openshell.policy.version": ((.correlation["openshell.policy.version"] // "") | tostring)
    }
  }]
  | sort_by([.source, .id])
  | unique_by([.source, .id])')
relay_span_identities=$(printf '%s' "$task_evidence" | jq -c '
  [.[] | select(.kind == "nemo_relay_trace") | {
    trace_id: .correlation.trace_id,
    span_id: .payload.span_id,
    stage,
    request_id: (.correlation.request_id // ""),
    tool_call_id: (.correlation.tool_call_id // ""),
    policy_version: ((.correlation["openshell.policy.version"] // "") | tostring),
    correlation_status: (.payload.attributes["openshell.correlation.status"] // ""),
    correlation_missing: (.payload.attributes["openshell.correlation.missing"] // ""),
    correlation: {
      "openshell.gateway.id": (.correlation["openshell.gateway.id"] // ""),
      "openshell.workspace": (.correlation["openshell.workspace"] // ""),
      "openshell.sandbox.id": (.correlation["openshell.sandbox.id"] // ""),
      "agent.session.id": (.correlation["agent.session.id"] // ""),
      "trace_id": (.correlation.trace_id // ""),
      "request_id": (.correlation.request_id // ""),
      "tool_call_id": (.correlation.tool_call_id // ""),
      "openshell.policy.version": ((.correlation["openshell.policy.version"] // "") | tostring)
    }
  }]
  | sort_by([.trace_id, .span_id])
  | unique_by([.trace_id, .span_id])')
unique_cloud_events=$(printf '%s' "$cloud_event_identities" | jq 'length')
unique_relay_spans=$(printf '%s' "$relay_span_identities" | jq 'length')
[ "$unique_cloud_events" -gt 0 ] || { echo "no stable CloudEvent identities were captured" >&2; exit 1; }
[ "$unique_relay_spans" -gt 0 ] || { echo "no stable Relay span identities were captured" >&2; exit 1; }
task_evidence_sha256=$(printf '%s' "$task_evidence" | openssl dgst -sha256 | awk '{print $NF}')
event_recovery_sha256=$(sha256_file "$SCRIPT_DIR/runtime/recovery/openshell-events.json")
trace_recovery_sha256=$(sha256_file "$SCRIPT_DIR/runtime/recovery/nemo-relay-traces.json")
sandbox_response_sha256=$(sha256_file "$SCRIPT_DIR/runtime/sandbox.json")
verified_at=$(date -u '+%Y-%m-%dT%H:%M:%SZ')

report_inputs_dir="$SCRIPT_DIR/runtime/state"
mkdir -p "$report_inputs_dir"
chmod 0700 "$report_inputs_dir"
cloud_event_identities_path=$(mktemp "$report_inputs_dir/cloud-event-identities.XXXXXX")
relay_span_identities_path=$(mktemp "$report_inputs_dir/relay-span-identities.XXXXXX")
cleanup_report_inputs() {
  rm -f "$cloud_event_identities_path" "$relay_span_identities_path"
}
trap cleanup_report_inputs EXIT HUP INT TERM
printf '%s\n' "$cloud_event_identities" >"$cloud_event_identities_path"
printf '%s\n' "$relay_span_identities" >"$relay_span_identities_path"
chmod 0600 "$cloud_event_identities_path" "$relay_span_identities_path"

report=$(jq -n \
  --arg verified_at "$verified_at" \
  --arg repository_commit "$repository_commit" \
  --arg repository_branch "$repository_branch" \
  --argjson worktree_clean "$worktree_clean" \
  --argjson candidate_bound "$candidate_bound" \
  --argjson external_routing_enabled "$DEMO_EXTERNAL_DESTINATIONS" \
  --arg exporter_image_id "$exporter_image_id" \
  --arg exporter_image_revision "$exporter_image_revision" \
  --arg exporter_image_version "$exporter_image_version" \
  --arg exporter_image_created "$exporter_image_created" \
  --arg gateway_image_id "$gateway_image_id" \
  --arg gateway_image_ref "$gateway_image_ref" \
  --arg conformance_receiver_image_id "$conformance_receiver_image_id" \
  --arg hermes_image_id "$hermes_image_id" \
  --arg sandbox_image_revision "$sandbox_image_revision" \
  --arg started_at "$DEMO_TASK_STARTED_AT" \
  --arg finished_at "$DEMO_TASK_FINISHED_AT" \
  --arg gateway_id "$gateway_id" \
  --arg workspace "$workspace" \
  --arg sandbox_id "$OPENSHELL_DEMO_SANDBOX_ID" \
  --arg agent_session_id "$DEMO_AGENT_SESSION_ID" \
  --arg relay_session_instance_id "$relay_session_id" \
  --arg created_file_sha256 "$DEMO_TASK_FILE_SHA256" \
  --argjson relay_trace_ids "$relay_trace_ids" \
  --argjson request_ids "$request_ids" \
  --argjson tool_call_ids "$tool_call_ids" \
  --argjson policy_versions "$policy_versions" \
  --slurpfile cloud_event_identities "$cloud_event_identities_path" \
  --slurpfile relay_span_identities "$relay_span_identities_path" \
  --arg task_evidence_sha256 "$task_evidence_sha256" \
  --arg sandbox_response_sha256 "$sandbox_response_sha256" \
  --arg event_recovery_sha256 "$event_recovery_sha256" \
  --arg trace_recovery_sha256 "$trace_recovery_sha256" \
  --argjson openshell_events "$logs" \
  --argjson relay_spans "$traces" \
  --argjson correlated_openshell "$correlated_logs" \
  --argjson correlated_relay "$correlated_traces" \
  --argjson relay_sessions "$relay_sessions" \
  --argjson unique_cloud_events "$unique_cloud_events" \
  --argjson unique_relay_spans "$unique_relay_spans" \
  --argjson prompt_spans "$relay_prompt_spans" \
  --argjson model_spans "$relay_model_spans" \
  --argjson tool_spans "$relay_tool_spans" \
  --argjson complete_prompt_spans "$relay_complete_prompt_spans" \
  --argjson complete_model_spans "$relay_complete_model_spans" \
  --argjson diagnosed_partial_prompt_spans "$relay_diagnosed_partial_prompt_spans" \
  --argjson diagnosed_partial_model_spans "$relay_diagnosed_partial_model_spans" \
  --argjson diagnosed_partial_tool_spans "$relay_diagnosed_partial_tool_spans" \
  --argjson complete_tool_spans "$relay_complete_tool_spans" \
  --argjson partial_relay_spans "$relay_partial_session_spans" \
  --argjson diagnosed_partial_relay_spans "$relay_diagnosed_partial_spans" \
  --argjson watchsandbox_records "$watchsandbox_records" \
  --argjson ocsf_records "$ocsf_records" \
  --argjson process_records "$ocsf_process_records" \
  --arg process_observation_status "$process_observation_status" \
  --argjson network_records "$ocsf_network_records" \
  --argjson file_records "$ocsf_file_records" \
  --argjson invalid_records "$invalid" \
  --argjson privacy_leaks "$privacy_leaks" \
  '{
    schema_version: "1.0",
    result: "passed_real_correlation",
    candidate_bound: $candidate_bound,
    external_routing_enabled: $external_routing_enabled,
    verified_at: $verified_at,
    candidate: {
      repository_commit: $repository_commit,
      repository_branch: $repository_branch,
      worktree_clean: $worktree_clean,
      images: {
        exporter: {id: $exporter_image_id, revision: $exporter_image_revision, version: $exporter_image_version, created: $exporter_image_created},
        gateway: {reference: $gateway_image_ref, id: $gateway_image_id},
        conformance_receiver: {id: $conformance_receiver_image_id},
        hermes: {id: $hermes_image_id}
      }
    },
    task: {
      started_at: $started_at,
      finished_at: $finished_at,
      gateway_id: $gateway_id,
      workspace: $workspace,
      openshell_sandbox_id: $sandbox_id,
      sandbox_image_revision: $sandbox_image_revision,
      agent_session_id: $agent_session_id,
      relay_session_instance_id: $relay_session_instance_id,
      session_bridge: {
        source: "hermes_atif",
        agent_session_id: $agent_session_id,
        relay_session_instance_id: $relay_session_instance_id
      },
      created_file: {path: "/sandbox/showcase-agent-evidence.txt", sha256: $created_file_sha256},
      relay_trace_ids: $relay_trace_ids,
      request_ids: $request_ids,
      tool_call_ids: $tool_call_ids,
      policy_versions: $policy_versions,
      cloud_events: $cloud_event_identities[0],
      relay_spans: $relay_span_identities[0],
      task_evidence_sha256: $task_evidence_sha256,
      sandbox_response_sha256: $sandbox_response_sha256
    },
    counts: {
      openshell_events: $openshell_events,
      relay_spans: $relay_spans,
      correlated_openshell: $correlated_openshell,
      correlated_relay: $correlated_relay,
      relay_sessions: $relay_sessions,
      unique_cloud_events: $unique_cloud_events,
      unique_relay_spans: $unique_relay_spans,
      relay_stages: {
        observed: {prompt: $prompt_spans, model: $model_spans, tool: $tool_spans},
        fully_correlated: {prompt: $complete_prompt_spans, model: $complete_model_spans, tool: $complete_tool_spans},
        diagnosed_partial: {prompt: $diagnosed_partial_prompt_spans, model: $diagnosed_partial_model_spans, tool: $diagnosed_partial_tool_spans}
      },
      correlation_gaps: {partial_relay: $partial_relay_spans, diagnosed_partial_relay: $diagnosed_partial_relay_spans},
      sources: {
        watchsandbox: $watchsandbox_records,
        ocsf: $ocsf_records,
        process: $process_records,
        process_status: $process_observation_status,
        network: $network_records,
        file: $file_records
      },
      structurally_invalid: $invalid_records,
      privacy_denied_attribute_leaks: $privacy_leaks
    },
    recovery: {
      openshell_events: {path: "runtime/recovery/openshell-events.json", sha256: $event_recovery_sha256},
      nemo_relay_traces: {path: "runtime/recovery/nemo-relay-traces.json", sha256: $trace_recovery_sha256}
    },
    limitations: [
      "local presentation receiver; not external destination certification",
      "created-file hash proves the sandbox postcondition; per-run OCSF process/file class availability is reported separately and is not implied by the postcondition",
      "OpenShell joins without direct session or trace identity are temporal",
      "Relay spans without source session identity remain explicitly partial and are never inferred",
      "does not prove rotation, node restart, capacity, signing, canary, or pilot gates"
    ]
  }')
cleanup_report_inputs
trap - EXIT HUP INT TERM

evidence_dir="$SCRIPT_DIR/runtime/evidence"
evidence_path="$evidence_dir/real-gateway-correlation.json"
mkdir -p "$evidence_dir"
chmod 0700 "$evidence_dir"
printf '%s\n' "$report" >"$evidence_path.tmp"
chmod 0600 "$evidence_path.tmp"
mv "$evidence_path.tmp" "$evidence_path"
if ! jq -e '
  . as $report |
  .schema_version == "1.0" and
  .result == "passed_real_correlation" and
  (.external_routing_enabled | type == "boolean") and
  (.candidate.repository_commit | test("^[0-9a-f]{40}$")) and
  (.candidate.images.exporter.id | test("^sha256:[0-9a-f]{64}$")) and
  ((.candidate_bound == false) or ((.candidate.worktree_clean == true) and (.candidate.images.exporter.revision == .candidate.repository_commit))) and
  (.candidate.images.gateway.reference | test("@sha256:[0-9a-f]{64}$")) and
  (.candidate.images.gateway.id | test("^sha256:[0-9a-f]{64}$")) and
  (.candidate.images.conformance_receiver.id | test("^sha256:[0-9a-f]{64}$")) and
  (.candidate.images.hermes.id | test("^sha256:[0-9a-f]{64}$")) and
  (.task.gateway_id | length > 0) and
  (.task.workspace | length > 0) and
  (.task.agent_session_id | length > 0) and
  (.task.relay_session_instance_id | length > 0) and
  (.task.session_bridge.source == "hermes_atif") and
  (.task.session_bridge.agent_session_id == .task.agent_session_id) and
  (.task.session_bridge.relay_session_instance_id == .task.relay_session_instance_id) and
  (.task.created_file.path == "/sandbox/showcase-agent-evidence.txt") and
  (.task.created_file.sha256 | test("^[0-9a-f]{64}$")) and
  (.task.relay_trace_ids | length > 0) and
  (($report.task.cloud_events | length) == $report.counts.unique_cloud_events) and
  (($report.task.relay_spans | length) == $report.counts.unique_relay_spans) and
  (all(.task.cloud_events[];
    (.source | startswith("openshell://")) and
    (.id | test("^sha256:[0-9a-f]{64}$")) and
    (.type | startswith("com.nvidia.openshell.")) and
    (.correlation["openshell.gateway.id"] == $report.task.gateway_id) and
    (.correlation["openshell.workspace"] == $report.task.workspace) and
    (.correlation["openshell.sandbox.id"] == $report.task.openshell_sandbox_id) and
    ((.correlation["agent.session.id"] == "") or (.correlation["agent.session.id"] == $report.task.relay_session_instance_id)) and
    ((.correlation.trace_id == "") or (.correlation.trace_id | test("^[0-9a-f]{32}$"))) and
    (.correlation.request_id | type == "string") and
    (.correlation.tool_call_id | type == "string") and
    (.correlation["openshell.policy.version"] | type == "string")
  )) and
  (all(.task.relay_spans[];
    (.trace_id | test("^[0-9a-f]{32}$")) and
    (.span_id | test("^[0-9a-f]{16}$")) and
    (.correlation["openshell.gateway.id"] == $report.task.gateway_id) and
    (.correlation["openshell.workspace"] == $report.task.workspace) and
    (.correlation["openshell.sandbox.id"] == $report.task.openshell_sandbox_id) and
    (
      ((.correlation["agent.session.id"] == $report.task.relay_session_instance_id) and (.correlation_status == "complete") and (.correlation_missing == "")) or
      ((.correlation["agent.session.id"] == "") and (.correlation_status == "partial") and
        ((.correlation_missing | split(",")) | index("agent.session.id") != null))
    ) and
    (.correlation.trace_id == .trace_id) and
    (.correlation.request_id == .request_id) and
    (.correlation.tool_call_id == .tool_call_id) and
    (.correlation["openshell.policy.version"] == .policy_version)
  )) and
  (.task.task_evidence_sha256 | test("^[0-9a-f]{64}$")) and
  (.recovery.openshell_events.sha256 | test("^[0-9a-f]{64}$")) and
  (.recovery.nemo_relay_traces.sha256 | test("^[0-9a-f]{64}$")) and
  (.counts.relay_spans == (.counts.correlated_relay + .counts.correlation_gaps.partial_relay)) and
  (.counts.correlation_gaps.partial_relay == .counts.correlation_gaps.diagnosed_partial_relay) and
  (.counts.relay_stages.observed.prompt > 0) and
  (.counts.relay_stages.observed.prompt == (.counts.relay_stages.fully_correlated.prompt + .counts.relay_stages.diagnosed_partial.prompt)) and
  (.counts.relay_stages.observed.model > 0) and
  (.counts.relay_stages.observed.model == (.counts.relay_stages.fully_correlated.model + .counts.relay_stages.diagnosed_partial.model)) and
  (.counts.relay_stages.observed.tool > 0) and
  (.counts.relay_stages.observed.tool == (.counts.relay_stages.fully_correlated.tool + .counts.relay_stages.diagnosed_partial.tool)) and
  (
    ((.counts.sources.process > 0) and (.counts.sources.process_status == "observed")) or
    ((.counts.sources.process == 0) and (.counts.sources.process_status == "declared_unobserved"))
  ) and
  .counts.relay_sessions == 1 and
  .counts.structurally_invalid == 0 and
  .counts.privacy_denied_attribute_leaks == 0
' "$evidence_path" >/dev/null; then
  echo "qualification evidence self-validation failed: $evidence_path" >&2
  jq '{candidate_bound, worktree_clean: .candidate.worktree_clean, repository_commit: .candidate.repository_commit, exporter_revision: .candidate.images.exporter.revision, relay_stages: .counts.relay_stages, correlation_gaps: .counts.correlation_gaps}' "$evidence_path" >&2
  exit 1
fi
external_destination_token=
if [ "$DEMO_EXTERNAL_DESTINATIONS" = true ]; then
  external_destination_token=$(<"$SCRIPT_DIR/runtime/tls/external-destination-token")
fi
for protected_value in "$NEMO_RELAY_OTLP_TOKEN" "$WEBAPP_INGEST_TOKEN" "$external_destination_token"; do
  [ -z "$protected_value" ] || ! grep -Fq -- "$protected_value" "$evidence_path" \
    || { echo "qualification evidence contains a credential value" >&2; exit 1; }
done
evidence_sha256=$(sha256_file "$evidence_path")

"${COMPOSE[@]}" ps
printf '\nDashboard evidence counts:\n'
printf '%s\n' "$stats" | jq '{total, invalid, by_kind, by_type}'
printf '\nVerified: OpenShell evidence=%s, Relay spans=%s, correlated OpenShell=%s, correlated Relay=%s, denied-attribute leaks=%s.\n' \
  "$logs" "$traces" "$correlated_logs" "$correlated_traces" "$privacy_leaks"
printf 'Correlation: ATIF-proven Hermes-to-Relay bridge; sessions=%s; complete prompt=%s, model=%s, tool=%s; diagnosed partial prompt=%s, model=%s, tool=%s; all partial=%s.\n' \
  "$relay_sessions" "$relay_complete_prompt_spans" "$relay_complete_model_spans" "$relay_complete_tool_spans" "$relay_diagnosed_partial_prompt_spans" "$relay_diagnosed_partial_model_spans" "$relay_diagnosed_partial_tool_spans" "$relay_diagnosed_partial_spans"
printf 'Sources: WatchSandbox=%s, OCSF=%s, process=%s (%s), network=%s, file=%s, invalid=%s.\n' \
  "$watchsandbox_records" "$ocsf_records" "$ocsf_process_records" "$process_observation_status" "$ocsf_network_records" "$ocsf_file_records" "$invalid"
printf 'Optional identities observed: request=%s, tool-call=%s, policy-version=%s.\n' \
  "$request_identity_records" "$tool_identity_records" "$policy_identity_records"
printf 'Showcase metrics: latency-spans=%s, token-spans=%s, Relay failures=%s, policy denials=%s.\n' \
  "$relay_latency_spans" "$relay_token_spans" "$relay_failures" "$policy_denials"
printf 'Qualification evidence: %s (sha256:%s, candidate-bound=%s).\n' \
  "$evidence_path" "$evidence_sha256" "$candidate_bound"
if [ "$candidate_bound" != true ]; then
  printf 'Warning: tracked or untracked repository changes prevent this run from qualifying an exact candidate.\n' >&2
fi
if [ "$relay_token_spans" -gt 0 ]; then
  printf 'Relay token telemetry: observed on %s span(s).\n' "$relay_token_spans"
else
  printf 'Relay token telemetry: unavailable; the observed Relay spans did not emit token-usage attributes.\n'
fi
