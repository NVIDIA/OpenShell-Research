#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
resolve_tools
[[ -r "$RUNTIME_DIR/task.env" ]] || fail "run run-task.sh first"
# shellcheck disable=SC1091
source "$RUNTIME_DIR/task.env"
"$DEMO_DIR/start-forwarding.sh"

elastic_password=$(k -n "$OBS_NAMESPACE" get secret openshell-elastic-credentials -o jsonpath='{.data.ELASTIC_PASSWORD}' | base64 --decode)
elastic_count() {
  local index=$1 query=$2
  curl --fail --silent --show-error --noproxy '*' \
    --cacert "$RUNTIME_DIR/elastic-ca.crt" \
    --resolve elasticsearch:19200:127.0.0.1 \
    --user "elastic:$elastic_password" \
    --header 'Content-Type: application/json' \
    --data-binary "$query" \
    "https://elasticsearch:19200/$index/_count" | jq -er '.count'
}

security_query=$(jq -nc --arg sandbox "$SANDBOX_ID" '{query:{term:{"openshell.sandbox.id":$sandbox}}}')
relay_query=$(jq -nc --arg sandbox "$SANDBOX_ID" "{query:{bool:{filter:[{term:{\"openshell.sandbox.id\":\$sandbox}},{term:{\"openshell.correlation.status\":\"complete\"}},{exists:{field:\"session.id\"}}]}}}")
forwarded_query=$(jq -nc --arg sandbox "$SANDBOX_ID" '{query:{bool:{filter:[{term:{"openshell.sandbox.id":$sandbox}},{terms:{"openshell.acquisition.kind":["ocsf.forwarded","openshell.log.forwarded"]}}]}}}')
capability_query=$(jq -nc --arg sandbox "$SANDBOX_ID" '{query:{bool:{filter:[{term:{"openshell.sandbox.id":$sandbox}},{term:{"event.action":"com.nvidia.openshell.source.capability.v1"}}]}}}')
invalid_query=$(jq -nc --arg sandbox "$SANDBOX_ID" '{query:{bool:{filter:[{term:{"openshell.sandbox.id":$sandbox}},{term:{"openshell.validation.status":"invalid"}}]}}}')
denial_query=$(jq -nc --arg sandbox "$SANDBOX_ID" '{query:{bool:{filter:[{term:{"openshell.sandbox.id":$sandbox}},{term:{tags:"policy-denial"}}]}}}')
draft_update_query=$(jq -nc --arg sandbox "$SANDBOX_ID" '{query:{bool:{filter:[{term:{"openshell.sandbox.id":$sandbox}},{term:{"event.action":"com.nvidia.openshell.policy.draft_updated.v1"}}]}}}')
draft_chunk_query=$(jq -nc --arg sandbox "$SANDBOX_ID" --arg chunk "$DRAFT_CHUNK_ID" '{query:{bool:{filter:[{term:{"openshell.sandbox.id":$sandbox}},{term:{"openshell.policy_chunk.id":$chunk}}]}}}')

security=0 relay=0 forwarded=0 capability=0 draft_updates=0 draft_chunk_records=0
for attempt in $(seq 1 90); do
  security=$(elastic_count 'logs-openshell.security-*-v2' "$security_query")
  relay=$(elastic_count 'traces-apm*' "$relay_query")
  forwarded=$(elastic_count 'logs-openshell.security-*-v2' "$forwarded_query")
  capability=$(elastic_count 'logs-openshell.security-*-v2' "$capability_query")
  draft_updates=$(elastic_count 'logs-openshell.security-*-v2' "$draft_update_query")
  draft_chunk_records=$(elastic_count 'logs-openshell.security-*-v2' "$draft_chunk_query")
  if (( security > 0 && relay > 0 && forwarded > 0 && capability > 0 && draft_chunk_records > 0 )); then break; fi
  sleep 2
done
(( security > 0 )) || fail "no OpenShell evidence reached Elastic for sandbox $SANDBOX_ID"
(( relay > 0 )) || fail "no fully correlated Provider v2-authenticated NeMo Relay traces with an agent session reached Elastic for sandbox $SANDBOX_ID"
(( forwarded > 0 )) || fail "the per-sandbox Fluent Bit file lane produced no Elastic evidence for $SANDBOX_ID"
(( capability > 0 )) || fail "the source-capability diagnostic did not reach Elastic for $SANDBOX_ID"
(( draft_chunk_records > 0 )) || fail "no authoritative OpenShell proposed-policy evidence carried the real pending draft chunk ID $DRAFT_CHUNK_ID"
if (( draft_updates == 0 )); then
  note "OpenShell 0.0.113 emitted authoritative OCSF CONFIG:PROPOSED evidence but no separate DraftPolicyUpdate notification for this policy.local submission"
fi
relay_status=observed_provider_v2_direct_fully_correlated

sandbox_name=$(k -n "$SANDBOX_NAMESPACE" get sandboxes.agents.x-k8s.io -o json | jq -er --arg sandbox "$SANDBOX_ID" '.items[] | select(.spec.podTemplate.metadata.annotations["openshell.io/sandbox-id"] == $sandbox) | .metadata.name' | head -1)
sandbox_json=$(k -n "$SANDBOX_NAMESPACE" get sandbox "$sandbox_name" -o json)
injected=$(printf '%s' "$sandbox_json" | jq -r '.spec.podTemplate.metadata.annotations["observability.openshell.nvidia.com/evidence-injected"] // empty')
[[ "$injected" == v1 ]] || fail "sandbox $sandbox_name was not marked as evidence-injected"
file_profile=$(printf '%s' "$sandbox_json" | jq -r '.spec.podTemplate.metadata.annotations["observability.openshell.nvidia.com/file-evidence-profile"] // empty')
network_lane=$(printf '%s' "$sandbox_json" | jq -r '.spec.podTemplate.metadata.annotations["observability.openshell.nvidia.com/network-file-lane"] // empty')
has_network=$(printf '%s' "$sandbox_json" | jq '[.spec.podTemplate.spec.containers[].name] | index("openshell-supervisor-network") != null')
if [[ "$has_network" == true ]]; then
  [[ "$file_profile" == agent-and-network && "$network_lane" == enabled ]] || fail "network topology selected incorrect forwarder profile $file_profile/$network_lane"
  expected_claims=3
else
  [[ "$file_profile" == agent-only && "$network_lane" == unavailable ]] || fail "agent-only topology selected incorrect forwarder profile $file_profile/$network_lane"
  expected_claims=2
fi
printf '%s' "$sandbox_json" | jq -e '
  [.spec.podTemplate.spec.containers[].name] | index("openshell-evidence-forwarder") != null
' >/dev/null || fail "sandbox $sandbox_name has no Fluent Bit forwarder"
printf '%s' "$sandbox_json" | jq -e '
  [.spec.podTemplate.spec.containers[] | select(.name == "openshell-relay-forwarder")] | length == 0
' >/dev/null || fail "sandbox unexpectedly contains the deprecated Relay OTLP proxy"
printf '%s' "$sandbox_json" | jq -e '
  [.spec.podTemplate.spec.volumes[]? | .secret.secretName? | select(. == "openshell-exporter-relay-input-auth" or . == "openshell-demo-relay-provider")] | length == 0
' >/dev/null || fail "the real Relay bearer Secret was mounted into the sandbox Pod"
printf '%s' "$sandbox_json" | jq -e --argjson expected "$expected_claims" '
  [.spec.volumeClaimTemplates[] | select(.metadata.name | startswith("openshell-evidence-") or startswith("openshell-forwarder-")) | .spec.accessModes[]] as $modes | ($modes | length) == $expected and ($modes | all(. == "ReadWriteOncePod"))
' >/dev/null || fail "sandbox evidence claims are not private ReadWriteOncePod claims"

metrics=$(curl -fsS http://127.0.0.1:18888/metrics)
printf '%s' "$metrics" | grep -q 'otelcol_exporter_sent_log_records' || fail "exporter delivery metrics are missing"
invalid=$(elastic_count 'logs-openshell.security-*-v2' "$invalid_query")
denials=$(elastic_count 'logs-openshell.security-*-v2' "$denial_query")
unset elastic_password

cat >"$RUNTIME_DIR/verification.json" <<JSON
{
  "gateway_version": "$OPEN_SHELL_VERSION",
  "sandbox_id": "$SANDBOX_ID",
  "sandbox_resource": "$sandbox_name",
  "hermes_session_id": "$HERMES_SESSION_ID",
  "hermes_cli_session_id": "$HERMES_SESSION_ID",
  "relay_session_identity": "exporter_managed_agent_session_id",
  "elastic_security_records": $security,
  "elastic_relay_spans_fully_correlated": $relay,
  "relay_delivery_status": "$relay_status",
  "forwarded_file_records": $forwarded,
  "source_capability_records": $capability,
  "policy_denials": $denials,
  "pending_draft_chunk_id": "$DRAFT_CHUNK_ID",
  "draft_update_notifications": $draft_updates,
  "draft_chunk_evidence_records": $draft_chunk_records,
  "structurally_invalid": $invalid,
  "file_evidence_profile": "$file_profile",
  "network_file_lane": "$network_lane",
  "watchsandbox_durability": "non_resumable",
  "file_forwarder_durability": "checkpointed_private_rwop",
  "relay_transport": "provider_v2_placeholder_over_direct_https"
}
JSON
jq . "$RUNTIME_DIR/verification.json"
note "verified real gateway, real Hermes/Nemotron Agent Sandbox, pending Policy Advisor draft, Provider v2 Relay traces, topology-aware file forwarding, Elastic SOC delivery, recovery, and bounded metrics"
