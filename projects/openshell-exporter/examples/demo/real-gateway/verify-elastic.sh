#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail
umask 077

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ELASTIC_ENV="$SCRIPT_DIR/runtime/elastic.env"
[ -f "$ELASTIC_ENV" ] || { echo "run ./run-elastic.sh first" >&2; exit 1; }
# shellcheck disable=SC1090
source "$ELASTIC_ENV"
[ -n "${ELASTIC_DEMO_STARTED_AT:-}" ] || { echo "Elastic demo start time is missing" >&2; exit 1; }
[ -n "${DEMO_TASK_STARTED_AT:-}" ] || { echo "real Hermes task start time is missing" >&2; exit 1; }
[ -n "${DEMO_TASK_FINISHED_AT:-}" ] || { echo "real Hermes task finish time is missing" >&2; exit 1; }
DETECTION_PASSWORD_FILE="$SCRIPT_DIR/runtime/tls/elastic-detection-password"
[ -s "$DETECTION_PASSWORD_FILE" ] || { echo "OpenShell detection-service password is missing" >&2; exit 1; }
DETECTION_PASSWORD=$(cat "$DETECTION_PASSWORD_FILE")
TLS_CA="$SCRIPT_DIR/runtime/tls/ca.crt"
[ -s "$TLS_CA" ] || { echo "Elastic demo CA is missing" >&2; exit 1; }

secure_curl() {
  curl --cacert "$TLS_CA" "$@"
}
[[ "$DETECTION_PASSWORD" =~ ^[0-9a-f]{64}$ ]] || { echo "OpenShell detection-service password has an invalid format" >&2; exit 1; }
[ "$DETECTION_PASSWORD" != "$ELASTIC_PASSWORD" ] || { echo "detection and administrator credentials must be distinct" >&2; exit 1; }
[ "$DETECTION_PASSWORD" != "$ELASTIC_INGEST_PASSWORD" ] || { echo "detection and ingest credentials must be distinct" >&2; exit 1; }

for endpoint in http://127.0.0.1:9200 http://127.0.0.1:5601; do
  if curl --fail --silent --max-time 2 "$endpoint" >/dev/null 2>&1; then
    echo "Elastic endpoint accepted plaintext HTTP: $endpoint" >&2
    exit 1
  fi
done
for certificate in elasticsearch-server.crt kibana-server.crt logstash-server.crt logstash-client.crt apm-server.crt; do
  openssl verify -CAfile "$TLS_CA" "$SCRIPT_DIR/runtime/tls/$certificate" >/dev/null || {
    echo "Elastic endpoint certificate does not chain to the demo CA: $certificate" >&2
    exit 1
  }
done

es() {
  secure_curl --fail --silent --show-error --user "elastic:$ELASTIC_PASSWORD" "$@"
}


[ "$ELASTIC_INGEST_PASSWORD" != "$ELASTIC_OTLP_PASSWORD" ] || { echo "CloudEvents and OTLP credentials must be distinct" >&2; exit 1; }
[ "$ELASTIC_APM_SECRET_TOKEN" != "$ELASTIC_INGEST_PASSWORD" ] || { echo "APM intake and Logstash output credentials must be distinct" >&2; exit 1; }
[ "$ELASTIC_INGEST_PASSWORD" != "$ELASTIC_PASSWORD" ] || {
  echo "runtime ingest and Elastic administrator credentials must be distinct" >&2
  exit 1
}

privileges=$(secure_curl --fail --silent --show-error \
  --user "openshell_ingest:$ELASTIC_INGEST_PASSWORD" \
  --request POST \
  --header 'Content-Type: application/json' \
  --data '{"cluster":["monitor","manage_index_templates"],"index":[{"names":["logs-openshell.security-default-v2","traces-apm*"],"privileges":["create_doc","view_index_metadata","delete_index"]}]}' \
  'https://127.0.0.1:9200/_security/user/_has_privileges')
printf '%s' "$privileges" | jq -e '
  .has_all_requested == false and
  .cluster.monitor == true and
  .cluster.manage_index_templates == false and
  .index["logs-openshell.security-default-v2"].create_doc == true and
  .index["logs-openshell.security-default-v2"].view_index_metadata == true and
  .index["logs-openshell.security-default-v2"].delete_index == false and
  .index["traces-apm*"].create_doc == false and
  .index["traces-apm*"].view_index_metadata == false and
  .index["traces-apm*"].delete_index == false
' >/dev/null || { echo "OpenShell ingest privilege boundary is incorrect" >&2; exit 1; }

otlp_privileges=$(secure_curl --fail --silent --show-error \
  --user "openshell_otlp:$ELASTIC_OTLP_PASSWORD" \
  --request POST \
  --header 'Content-Type: application/json' \
  --data '{"cluster":["monitor","manage_index_templates"],"index":[{"names":["traces-apm*","logs-openshell.security-default-v2",".apm-agent-configuration"],"privileges":["auto_configure","create_doc","view_index_metadata","read"]}]}' \
  'https://127.0.0.1:9200/_security/user/_has_privileges')
printf '%s' "$otlp_privileges" | jq -e '
  .has_all_requested == false and
  .cluster.monitor == true and
  .cluster.manage_index_templates == false and
  .index["traces-apm*"].auto_configure == true and
  .index["traces-apm*"].create_doc == true and
  .index["logs-openshell.security-default-v2"].create_doc == false and
  .index[".apm-agent-configuration"].read == true
' >/dev/null || { echo "APM output privilege boundary is incorrect" >&2; exit 1; }
detection_privileges=$(secure_curl --fail --silent --show-error \
  --user "openshell_detection_service:$DETECTION_PASSWORD" \
  --request POST \
  --header "Content-Type: application/json" \
  --data '{"cluster":["monitor","manage_index_templates"],"index":[{"names":["logs-openshell.security-default-v2","traces-apm*"],"privileges":["read","view_index_metadata","create_doc","delete_index"]}]}' \
  "https://127.0.0.1:9200/_security/user/_has_privileges")
printf '%s' "$detection_privileges" | jq -e '
  .has_all_requested == false and
  .cluster.monitor == false and
  .cluster.manage_index_templates == false and
  .index["logs-openshell.security-default-v2"].read == true and
  .index["logs-openshell.security-default-v2"].view_index_metadata == true and
  .index["logs-openshell.security-default-v2"].create_doc == false and
  .index["logs-openshell.security-default-v2"].delete_index == false and
  .index["traces-apm*"].read == true and
  .index["traces-apm*"].view_index_metadata == true and
  .index["traces-apm*"].create_doc == false and
  .index["traces-apm*"].delete_index == false
' >/dev/null || { echo "OpenShell detection-service privilege boundary is incorrect" >&2; exit 1; }

detection_forbidden_status=$(secure_curl --silent --output /tmp/openshell-detection-forbidden-template.json --write-out '%{http_code}' \
  --user "openshell_detection_service:$DETECTION_PASSWORD" \
  --request PUT \
  --header "Content-Type: application/json" \
  --data '{"index_patterns":["forbidden-openshell-detection-*"]}' \
  "https://127.0.0.1:9200/_index_template/openshell-detection-forbidden")
[ "$detection_forbidden_status" -eq 403 ] || {
  echo "OpenShell detection service can administer index templates (HTTP $detection_forbidden_status)" >&2
  exit 1
}

forbidden_status=$(secure_curl --silent --output /tmp/openshell-forbidden-template.json --write-out '%{http_code}' \
  --user "openshell_ingest:$ELASTIC_INGEST_PASSWORD" \
  --request PUT \
  --header 'Content-Type: application/json' \
  --data '{"index_patterns":["forbidden-openshell-*"]}' \
  'https://127.0.0.1:9200/_index_template/openshell-forbidden')
[ "$forbidden_status" -eq 403 ] || {
  echo "OpenShell ingest identity can administer index templates (HTTP $forbidden_status)" >&2
  exit 1
}

component=$(es 'https://127.0.0.1:9200/_component_template/openshell-security-ecs-v2')
printf '%s' "$component" | jq -e '
  .component_templates[0].component_template.template.mappings._meta.openshell_mapping_version == 2 and
  .component_templates[0].component_template.template.mappings.properties.tags.type == "keyword" and
  .component_templates[0].component_template.template.mappings.properties.event.properties.original.index == false and
  .component_templates[0].component_template.template.mappings.properties.openshell.properties.ocsf.type == "flattened"
' >/dev/null || { echo "OpenShell ECS component mapping is missing" >&2; exit 1; }

index_template=$(es 'https://127.0.0.1:9200/_index_template/openshell-security-v2')
printf '%s' "$index_template" | jq -e '
  .index_templates[0].index_template.composed_of == ["openshell-security-ecs-v2"] and
  .index_templates[0].index_template.index_patterns == ["logs-openshell.security-*-v2"] and
  .index_templates[0].index_template.data_stream == null
' >/dev/null || { echo "OpenShell canonical index template is missing" >&2; exit 1; }


canonical_index="logs-openshell.security-default-v2"
canonical_trace_index="traces-apm*"

COMPOSE=(docker compose --env-file "$ELASTIC_ENV" -f "$SCRIPT_DIR/compose.yaml" -f "$SCRIPT_DIR/compose.elastic.yaml")
for service in logstash apm-server elasticsearch kibana exporter; do
  service_id=$("${COMPOSE[@]}" ps -q "$service")
  [ -n "$service_id" ] || { echo "Elastic two-lane service is missing: $service" >&2; exit 1; }
done

logstash_stats=$(curl --fail --silent http://127.0.0.1:19600/_node/stats/pipelines)
printf '%s' "$logstash_stats" | jq -e '
  .pipelines["openshell-cloudevents"].queue.type == "persisted" and
  .pipelines["openshell-cloudevents"].queue.capacity.max_queue_size_in_bytes >= 536870912 and
  any(.pipelines["openshell-cloudevents"].plugins.inputs[]; .id == "openshell_cloudevents_https") and
  any(.pipelines["openshell-cloudevents"].plugins.outputs[]; .id == "openshell_security_v2")
' >/dev/null || { echo "Logstash CloudEvents pipeline or persistent queue is not ready" >&2; exit 1; }

if secure_curl --fail --silent --max-time 3 \
  --resolve logstash.demo.internal:18443:127.0.0.1 \
  https://logstash.demo.internal:18443/v1/events >/dev/null 2>&1; then
  echo "Logstash accepted a client without the required mTLS identity" >&2
  exit 1
fi
logstash_diagnostic_before=$("${COMPOSE[@]}" exec -T logstash sh -c "if [ -f /usr/share/logstash/data/recovery/cloudevents-intake-diagnostics.jsonl ]; then wc -l < /usr/share/logstash/data/recovery/cloudevents-intake-diagnostics.jsonl; else echo 0; fi")
wrong_media_status=$(secure_curl --silent --output /tmp/openshell-wrong-logstash-media.txt --write-out "%{http_code}" --resolve logstash.demo.internal:18443:127.0.0.1 --cert "$SCRIPT_DIR/runtime/tls/logstash-client.crt" --key "$SCRIPT_DIR/runtime/tls/logstash-client.key" --header "Content-Type: application/json; charset=utf-8" --request POST --data-binary "[]" https://logstash.demo.internal:18443/v1/events)
[ "$wrong_media_status" -eq 204 ] || { echo "Logstash did not acknowledge the durably accepted diagnostic fixture (HTTP $wrong_media_status)" >&2; exit 1; }
for attempt in $(seq 1 20); do logstash_diagnostic_after=$("${COMPOSE[@]}" exec -T logstash sh -c "wc -l < /usr/share/logstash/data/recovery/cloudevents-intake-diagnostics.jsonl 2>/dev/null || echo 0"); [ "$logstash_diagnostic_after" -gt "$logstash_diagnostic_before" ] && break; sleep 1; done
[ "$logstash_diagnostic_after" -gt "$logstash_diagnostic_before" ] || { echo "Logstash dropped the wrong-media-type intake diagnostic" >&2; exit 1; }
"${COMPOSE[@]}" exec -T logstash sh -c "tail -n 1 /usr/share/logstash/data/recovery/cloudevents-intake-diagnostics.jsonl" | jq -e ".event.reason == \"Content-Type must be application/cloudevents-batch+json\" and (.tags | index(\"intake-diagnostic\") != null)" >/dev/null || { echo "Logstash recovery diagnostic is incomplete" >&2; exit 1; }
wrong_apm_status=$(secure_curl --silent --output /tmp/openshell-wrong-apm-token.txt --write-out '%{http_code}' \
  --header 'Authorization: Bearer definitely-not-the-demo-token' \
  --header 'Content-Type: application/x-protobuf' \
  --request POST --data-binary '' \
  https://127.0.0.1:18200/v1/traces)
[ "$wrong_apm_status" -eq 401 ] || { echo "APM Server accepted the wrong bearer token (HTTP $wrong_apm_status)" >&2; exit 1; }

apm_pipeline=$(es 'https://127.0.0.1:9200/_ingest/pipeline/traces-apm@custom')
printf '%s' "$apm_pipeline" | jq -e '
  .["traces-apm@custom"].description | contains("privacy-filtered NeMo Relay")
' >/dev/null || { echo "Elastic APM OpenShell customization hook is missing" >&2; exit 1; }
count_query() {
  local index=$1
  local query=$2
  es --header 'Content-Type: application/json' \
    --request POST \
    --data "$query" \
    "https://127.0.0.1:9200/$index/_count" | jq -r '.count'
}

task_time_filter=$(jq -cn --arg started "$DEMO_TASK_STARTED_AT" --arg finished "$DEMO_TASK_FINISHED_AT" '{query:{range:{"@timestamp":{gte:$started,lte:$finished}}}}')
time_filter=$(jq -cn --arg started "$ELASTIC_DEMO_STARTED_AT" '{query:{range:{"@timestamp":{gte:$started}}}}')
security_events=$(count_query "$canonical_index" "$time_filter")
denials=$(count_query "$canonical_index" "$(jq -cn --arg started "$ELASTIC_DEMO_STARTED_AT" '{query:{bool:{filter:[{range:{"@timestamp":{gte:$started}}},{term:{"tags":"policy-denial"}},{term:{"event.dataset":"openshell.security"}}]}}}')")
sandbox_events=$(count_query "$canonical_index" "$(jq -cn --arg started "$ELASTIC_DEMO_STARTED_AT" --arg sandbox "$OPENSHELL_DEMO_SANDBOX_ID" '{query:{bool:{filter:[{range:{"@timestamp":{gte:$started}}},{term:{"openshell.sandbox.id":$sandbox}}]}}}')")
agent_traces=$(count_query "$canonical_trace_index" "$task_time_filter")
agent_sandbox_traces=$(count_query "$canonical_trace_index" "$(jq -cn --arg started "$DEMO_TASK_STARTED_AT" --arg finished "$DEMO_TASK_FINISHED_AT" --arg sandbox "$OPENSHELL_DEMO_SANDBOX_ID" '{query:{bool:{filter:[{range:{"@timestamp":{gte:$started,lte:$finished}}},{term:{"openshell.sandbox.id":$sandbox}}]}}}')")
agent_complete_traces=$(count_query "$canonical_trace_index" "$(jq -cn --arg started "$DEMO_TASK_STARTED_AT" --arg finished "$DEMO_TASK_FINISHED_AT" --arg sandbox "$OPENSHELL_DEMO_SANDBOX_ID" '{query:{bool:{filter:[{range:{"@timestamp":{gte:$started,lte:$finished}}},{term:{"openshell.sandbox.id":$sandbox}},{term:{"openshell.correlation.status":"complete"}}]}}}')")

[ "$security_events" -gt 0 ] || { echo "no real OpenShell evidence reached Elasticsearch" >&2; exit 1; }
[ "$denials" -ge 3 ] || { echo "fewer than three real OpenShell policy denials reached Elasticsearch" >&2; exit 1; }
[ "$sandbox_events" -gt 0 ] || { echo "Elastic evidence does not carry the real sandbox ID" >&2; exit 1; }
[ "$agent_traces" -gt 0 ] || { echo "no privacy-filtered Relay spans reached Elasticsearch" >&2; exit 1; }
[ "$agent_sandbox_traces" -gt 0 ] || { echo "Elastic agent traces do not carry the real sandbox ID" >&2; exit 1; }
[ "$agent_complete_traces" -gt 0 ] || { echo "Elastic has no Relay trace carrying both sandbox and session identity" >&2; exit 1; }

trace_sample=$(es --header 'Content-Type: application/json' \
  --request POST \
  --data "$(jq -cn --arg started "$DEMO_TASK_STARTED_AT" --arg finished "$DEMO_TASK_FINISHED_AT" --arg sandbox "$OPENSHELL_DEMO_SANDBOX_ID" '{size:1,query:{bool:{filter:[{range:{"@timestamp":{gte:$started,lte:$finished}}},{term:{"openshell.sandbox.id":$sandbox}},{term:{"openshell.correlation.status":"complete"}}]}},sort:[{"@timestamp":"desc"}],_source:["event.id","event.dataset","event.action","message","trace.id","span.id","session.id","openshell.sandbox.id","openshell.gateway.id","openshell.workspace","openshell.correlation.status","nemo_relay.privacy","labels.openshell_sandbox_id","labels.openshell_gateway_id","labels.openshell_workspace","labels.openshell_correlation_status","labels.agent_session_id"]}')" \
  "https://127.0.0.1:9200/$canonical_trace_index/_search")
printf '%s' "$trace_sample" | jq -e --arg sandbox "$OPENSHELL_DEMO_SANDBOX_ID" '
  .hits.hits[0]._source as $trace |
  ($trace.event.id | test("^[0-9a-f]{32}:[0-9a-f]{16}$")) and
  $trace.event.dataset == "openshell.agent" and
  ($trace.event.action | test("^(agent|chain|llm|tool|span)$")) and
  ($trace.message | test("^relay\\.(agent|chain|model|tool|span)$")) and
  ($trace.trace.id | test("^[0-9a-f]{32}$")) and
  ($trace.span.id | test("^[0-9a-f]{16}$")) and
  ($trace.session.id | length > 0) and
  $trace.openshell.sandbox.id == $sandbox and
  $trace.openshell.correlation.status == "complete" and
  $trace.nemo_relay.privacy == {"applied":true,"mode":"allow","profile":"relay-default-allowlist-v1"} and
  $trace.labels.openshell_sandbox_id == $sandbox and
  $trace.labels.openshell_gateway_id == $trace.openshell.gateway.id and
  $trace.labels.openshell_workspace == $trace.openshell.workspace and
  $trace.labels.openshell_correlation_status == $trace.openshell.correlation.status and
  $trace.labels.agent_session_id == $trace.session.id
' >/dev/null || { echo "Elastic trace lost native APM fields, privacy metadata, or additive OpenShell correlation" >&2; exit 1; }

trace_documents=$(es --header 'Content-Type: application/json' \
  --request POST \
  --data "$(jq -cn --arg started "$DEMO_TASK_STARTED_AT" --arg finished "$DEMO_TASK_FINISHED_AT" '{size:1000,query:{bool:{filter:[{range:{"@timestamp":{gte:$started,lte:$finished}}},{term:{"event.dataset":"openshell.agent"}}]}}}')" \
  "https://127.0.0.1:9200/$canonical_trace_index/_search")
privacy_leaks=$(printf '%s' "$trace_documents" | jq '[
  .hits.hits[]._source |
  paths(scalars) as $path |
  ($path | map(tostring) | join(".")) as $key |
  select($key | test("(^|[._-])(authorization|credential|password|prompt|secret)([._-]|$)|(^|[._-])(input|output)[._-](value|content|message|messages)([._-]|$)|response.*content|tool.*(argument|parameter|result)|nemo_relay\\.mark\\.data|error\\.(message|stack)"; "i")) |
  select(($key | test("(usage|token_count|token_usage|cost)"; "i")) | not) |
  $key
] | unique | length')
[ "$privacy_leaks" -eq 0 ] || { echo "privacy-denied Relay attribute keys reached Elasticsearch" >&2; exit 1; }

sample=$(es --header 'Content-Type: application/json' \
  --request POST \
  --data "$(jq -cn --arg started "$ELASTIC_DEMO_STARTED_AT" '{size:1,query:{bool:{filter:[{range:{"@timestamp":{gte:$started}}},{term:{"tags":"policy-denial"}},{term:{"openshell.validation.status":"valid"}}]}},sort:[{"@timestamp":"desc"}],_source:["event.id","event.original","openshell.cloud_event.source","openshell.validation.status","openshell.redaction"]}')" \
  'https://127.0.0.1:9200/logs-openshell.security-default-v2/_search')

event_id=$(printf '%s' "$sample" | jq -r '.hits.hits[0]._source.event.id // empty')
event_source=$(printf '%s' "$sample" | jq -r '.hits.hits[0]._source.openshell.cloud_event.source // empty')
original=$(printf '%s' "$sample" | jq -r '.hits.hits[0]._source.event.original // empty')
validation=$(printf '%s' "$sample" | jq -r '.hits.hits[0]._source.openshell.validation.status // empty')
[[ "$event_id" =~ ^sha256:[0-9a-f]{64}$ ]] || { echo "Elastic document lacks a stable CloudEvent ID" >&2; exit 1; }
[[ "$event_source" == openshell://* ]] || { echo "Elastic document lacks the OpenShell CloudEvent source" >&2; exit 1; }
[ "$validation" = valid ] || { echo "Elastic denial is not structurally valid OCSF evidence" >&2; exit 1; }
[ -n "$original" ] || { echo "Elastic document did not retain the complete redacted CloudEvent" >&2; exit 1; }

# Re-deliver the exact redacted envelope through Logstash and prove that the
# deterministic SHA-256(source + NUL + id) document key suppresses duplicates.
printf '%s' "$original" | jq -e '
  .specversion == "1.0" and .dataschema == "urn:openshell:event-envelope:1" and
  (.data.original != null) and (.data.acquisition.kind | length > 0) and
  (.data.security.validation.status | length > 0) and
  (.data.security.redaction.profile_id | length > 0)
' >/dev/null || { echo "retained CloudEvent lost envelope or original-source fields" >&2; exit 1; }
duplicate_query=$(jq -cn --arg id "$event_id" --arg source "$event_source" '{query:{bool:{filter:[{term:{"event.id":$id}},{term:{"openshell.cloud_event.source":$source}}]}}}')
before=$(count_query "$canonical_index" "$duplicate_query")
[ "$before" -eq 1 ] || { echo "expected one canonical source+ID document before replay, got $before" >&2; exit 1; }
batch=$(printf '%s' "$original" | jq -sc '.')
secure_curl --fail --silent --show-error \
  --resolve logstash.demo.internal:18443:127.0.0.1 \
  --cert "$SCRIPT_DIR/runtime/tls/logstash-client.crt" \
  --key "$SCRIPT_DIR/runtime/tls/logstash-client.key" \
  --header 'Content-Type: application/cloudevents-batch+json; charset=utf-8' \
  --request POST --data "$batch" \
  https://logstash.demo.internal:18443/v1/events >/dev/null
sleep 2
after=$(count_query "$canonical_index" "$duplicate_query")
[ "$after" -eq 1 ] || { echo "source+ID replay created a duplicate Elasticsearch document" >&2; exit 1; }
secure_curl --fail --silent --show-error --user "elastic:$ELASTIC_PASSWORD" \
  --header 'kbn-xsrf: openshell-demo' \
  'https://127.0.0.1:5601/api/data_views/data_view/openshell-security' >/dev/null
secure_curl --fail --silent --show-error --user "elastic:$ELASTIC_PASSWORD" \
  --header 'kbn-xsrf: openshell-demo' \
  'https://127.0.0.1:5601/api/data_views/data_view/openshell-agent-traces' >/dev/null

for dashboard_spec in \
  "openshell-soc-overview|OpenShell SOC - Agent Security Command Center|7|6" \
  "openshell-soc-investigation|OpenShell SOC - Agent Activity and Enforcement Timeline|4|3" \
  "openshell-soc-incident-investigation|OpenShell SOC - Alert-to-Sandbox Investigation|6|5" \
  "openshell-soc-analyst-triage|OpenShell SOC - Analyst Triage and Investigation|11|10" \
  "openshell-soc-trust|OpenShell SOC - Evidence Coverage and Trust|4|3"
do
  IFS='|' read -r dashboard_id dashboard_title dashboard_panels dashboard_references <<EOF
$dashboard_spec
EOF
  dashboard=$(secure_curl --fail --silent --show-error --user "elastic:$ELASTIC_PASSWORD" \
    "https://127.0.0.1:5601/api/saved_objects/dashboard/$dashboard_id")
  printf '%s' "$dashboard" | jq -e --arg title "$dashboard_title" --argjson panels "$dashboard_panels" --argjson references "$dashboard_references" '
    .attributes.title == $title and
    (.attributes.panelsJSON | fromjson | length) == $panels and
    (.references | length) == $references
  ' >/dev/null || { echo "OpenShell SOC dashboard is incomplete: $dashboard_id" >&2; exit 1; }
done

for visualization_id in \
  openshell-soc-evidence-flow \
  openshell-soc-attention-now \
  openshell-soc-evidence-mix \
  openshell-soc-denials-by-sandbox \
  openshell-soc-agent-stages \
  openshell-soc-correlated-timeline \
  openshell-soc-latest-sandbox-state \
  openshell-soc-incident-timeline \
  openshell-soc-trust-controls \
  openshell-soc-delivery-latency
do
  visualization=$(secure_curl --fail --silent --show-error --user "elastic:$ELASTIC_PASSWORD" \
    "https://127.0.0.1:5601/api/saved_objects/visualization/$visualization_id")
  printf '%s' "$visualization" | jq -e --arg visualization_id "$visualization_id" '
    (.attributes.visState | fromjson) as $state |
    ($state.params.spec | fromjson) as $spec |
    $state.type == "vega" and
    ($spec.data.url.index == "logs-openshell.security-*-v2" or
     $spec.data.url.index == "traces-apm*" or
     $spec.data.url.index == "logs-openshell.security-*-v2,traces-apm*") and
    (
      (($spec.data.url.body | has("query")) | not) or
      (
        (($spec.data.url["%context%"] // false) != true) and
        (($spec.data.url["%timefield%"] // "") == "") and
        (($spec | tojson | contains("%dashboard_context-must_clause%"))) and
        (($spec | tojson | contains("%dashboard_context-filter_clause%"))) and
        (($spec | tojson | contains("%dashboard_context-must_not_clause%"))) and
        (($spec | tojson | contains("%timefilter%")))
      )
    ) and
    (
      $visualization_id != "openshell-soc-delivery-latency" or
      (($spec.data.url.body.script_fields.delivery_delay_seconds.script.source // "") | length) > 0
    )
  ' >/dev/null || { echo "OpenShell SOC visualization is incomplete: $visualization_id" >&2; exit 1; }
done

for saved_search in \
  openshell-soc-evidence-health \
  openshell-soc-policy-denials \
  openshell-soc-sandbox-provenance \
  openshell-soc-recent-evidence \
  openshell-soc-agent-traces
do
  search_payload=$(secure_curl --fail --silent --show-error --user "elastic:$ELASTIC_PASSWORD" \
    "https://127.0.0.1:5601/api/saved_objects/search/$saved_search")
  expected_data_view=openshell-security
  [ "$saved_search" != openshell-soc-agent-traces ] || expected_data_view=openshell-agent-traces
  printf '%s' "$search_payload" | jq -e --arg data_view "$expected_data_view" '
    .attributes.title != "" and
    .references == [{
      "id":$data_view,
      "name":"kibanaSavedObjectMeta.searchSourceJSON.index",
      "type":"index-pattern"
    }]
  ' >/dev/null || { echo "OpenShell SOC saved search is incomplete: $saved_search" >&2; exit 1; }
done

for role in openshell_soc_viewer openshell_soc_analyst openshell_soc_detection_engineer; do
  role_payload=$(secure_curl --fail --silent --show-error --user "elastic:$ELASTIC_PASSWORD" \
    "https://127.0.0.1:5601/api/security/role/$role")
  printf '%s' "$role_payload" | jq -e '
    .elasticsearch.cluster == [] and
    .elasticsearch.indices[0].names == ["logs-openshell.security-*","traces-apm*"] and
    .elasticsearch.indices[0].privileges == ["read","view_index_metadata"] and
    .kibana[0].feature.discover_v2 == ["read"] and
    .kibana[0].feature.siemV5 == ["read"]
  ' >/dev/null || { echo "OpenShell SOC role is not least privilege: $role" >&2; exit 1; }
done
viewer_role=$(secure_curl --fail --silent --show-error --user "elastic:$ELASTIC_PASSWORD" \
  'https://127.0.0.1:5601/api/security/role/openshell_soc_viewer')
printf '%s' "$viewer_role" | jq -e '
  .kibana[0].feature.securitySolutionTimeline == ["read"] and
  .kibana[0].feature.securitySolutionNotes == ["read"] and
  .kibana[0].feature.securitySolutionAlertsV1 == ["read"] and
  .kibana[0].feature.securitySolutionCasesV3 == ["read"] and
  .kibana[0].feature.securitySolutionRulesV4 == ["read"] and
  (.kibana[0].feature.actions // []) == []
' >/dev/null || { echo "OpenShell viewer privileges are not read-only" >&2; exit 1; }

analyst_role=$(secure_curl --fail --silent --show-error --user "elastic:$ELASTIC_PASSWORD" \
  'https://127.0.0.1:5601/api/security/role/openshell_soc_analyst')
printf '%s' "$analyst_role" | jq -e '
  .kibana[0].feature.securitySolutionTimeline == ["all"] and
  .kibana[0].feature.securitySolutionNotes == ["all"] and
  .kibana[0].feature.securitySolutionAlertsV1 == ["all"] and
  .kibana[0].feature.securitySolutionCasesV3 == ["all"] and
  .kibana[0].feature.securitySolutionRulesV4 == ["read"] and
  .kibana[0].feature.actions == ["read"]
' >/dev/null || { echo "OpenShell analyst cannot perform triage without rule administration" >&2; exit 1; }

detection_role=$(secure_curl --fail --silent --show-error --user "elastic:$ELASTIC_PASSWORD" \
  'https://127.0.0.1:5601/api/security/role/openshell_soc_detection_engineer')
printf '%s' "$detection_role" | jq -e '
  .kibana[0].feature.securitySolutionTimeline == ["all"] and
  .kibana[0].feature.securitySolutionNotes == ["all"] and
  .kibana[0].feature.securitySolutionAlertsV1 == ["all"] and
  .kibana[0].feature.securitySolutionCasesV3 == ["all"] and
  .kibana[0].feature.securitySolutionRulesV4 == ["all"] and
  .kibana[0].feature.actions == ["read"]
' >/dev/null || { echo "OpenShell detection-engineer privileges are incomplete" >&2; exit 1; }

connector=$(secure_curl --fail --silent --show-error --user "elastic:$ELASTIC_PASSWORD" \
  'https://127.0.0.1:5601/api/actions/connector/openshell-soc-case-index-v1')
printf '%s' "$connector" | jq -e '
  .connector_type_id == ".index" and
  .is_missing_secrets == false and
  .config.index == "openshell-soc-notifications-default-v1" and
  .config.refresh == true and .config.executionTimeField == "indexed_at"
' >/dev/null || { echo "local SOC case connector is incomplete" >&2; exit 1; }

rules_file="$SCRIPT_DIR/../../../integrations/elastic/assets/soc-rules.ndjson"
[ -s "$rules_file" ] || { echo "OpenShell SOC rule pack is missing" >&2; exit 1; }
rule_count=0
while IFS= read -r rule; do
  rule_id=$(printf '%s\n' "$rule" | jq -r '.rule_id')
  rule_payload=$(secure_curl --fail --silent --show-error --user "elastic:$ELASTIC_PASSWORD" \
    "https://127.0.0.1:5601/api/detection_engine/rules?rule_id=$rule_id")
  rule_object_id=$(printf '%s' "$rule_payload" | jq -r '.id // empty')
  printf '%s' "$rule_payload" | jq -e --arg owner openshell_detection_service --argjson expected "$rule" '
    .enabled == true and
    .created_by == $owner and
    .updated_by == $owner and
    .execution_summary.last_execution.status == "succeeded" and
    .version == $expected.version and
    ((.timestamp_override // null) == ($expected.timestamp_override // null)) and
    ((.timestamp_override_fallback_disabled // null) == ($expected.timestamp_override_fallback_disabled // null))
  ' >/dev/null || { echo "OpenShell SOC rule is not healthy or least privilege: $rule_id" >&2; exit 1; }
  case "$rule_id" in
    openshell-validation-failure-v1|openshell-source-gap-v1|openshell-policy-denial-burst-v1|openshell-agent-policy-denial-sequence-v1|openshell-policy-denial-destination-probe-v1|openshell-denial-source-gap-sequence-v1)
      printf '%s' "$rule_payload" | jq -e '
        .actions | length == 1 and
        .[0].action_type_id == ".index" and
        .[0].id == "openshell-soc-case-index-v1" and
        .[0].group == "default" and
        .[0].frequency == {"summary":false,"notifyWhen":"onActiveAlert","throttle":null} and
        (.[0].params.documents | length == 1) and
        (.[0].params.documents[0] |
          .schema_version == "1.0" and .producer == "elastic-security" and
          .environment == "default" and .prompt == null and .response == null and
          .tool_arguments == null and .credentials == null)
      ' >/dev/null || { echo "SOC rule lacks the reviewed privacy-safe action: $rule_id" >&2; exit 1; }
      ;;
    *)
      printf '%s' "$rule_payload" | jq -e '.actions == []' >/dev/null ||
        { echo "non-escalation rule unexpectedly has a connector action: $rule_id" >&2; exit 1; }
      ;;
  esac
  if [ "$rule_id" = openshell-agent-policy-denial-sequence-v1 ]; then
    printf '%s' "$rule_payload" | jq -e '
      .version == 3 and
      (.alert_suppression == null) and
      (.query | contains("event.action == \"agent\""))
    ' >/dev/null || { echo "cross-lane SOC rule does not anchor to the root agent stage" >&2; exit 1; }
  fi
  alerting_rule=$(secure_curl --fail --silent --show-error --user "elastic:$ELASTIC_PASSWORD" \
    "https://127.0.0.1:5601/api/alerting/rule/$rule_object_id")
  printf '%s' "$alerting_rule" | jq -e --arg owner openshell_detection_service '
    .api_key_owner == $owner and .api_key_created_by_user == false
  ' >/dev/null || { echo "OpenShell SOC rule execution key has an unsafe owner: $rule_id" >&2; exit 1; }
  rule_count=$((rule_count + 1))
done < "$rules_file"
[ "$rule_count" -eq 11 ] || { echo "expected 11 OpenShell SOC rules, got $rule_count" >&2; exit 1; }

alert_query=$(jq -cn \
  --arg event_id "$event_id" \
  '{size:10,_source:["kibana.alert.rule.rule_id","kibana.alert.original_event.id","kibana.alert.status","kibana.alert.workflow_status","openshell.sandbox.id"],query:{bool:{filter:[
    {term:{"kibana.alert.rule.rule_id":"openshell-policy-denial-v1"}},
    {term:{"kibana.alert.original_event.id":$event_id}},
    {term:{"kibana.alert.status":"active"}}
  ]}}}')
alert_response=$(es --header 'Content-Type: application/json' \
  --request POST \
  --data "$alert_query" \
  'https://127.0.0.1:9200/.alerts-security.alerts-default/_search')
alert_count=$(printf '%s' "$alert_response" | jq -r '.hits.total.value')
[ "$alert_count" -gt 0 ] || { echo "real OpenShell policy denial did not produce an active SOC alert" >&2; exit 1; }
printf '%s' "$alert_response" | jq -e --arg sandbox "$OPENSHELL_DEMO_SANDBOX_ID" '
  any(.hits.hits[]; ._source.openshell.sandbox.id == $sandbox and
    ._source["kibana.alert.workflow_status"] == "open")
' >/dev/null || { echo "policy-denial alert does not preserve the real sandbox identity and open workflow state" >&2; exit 1; }

burst_query=$(jq -cn \
  '{size:10,_source:["kibana.alert.rule.rule_id","kibana.alert.status","kibana.alert.workflow_status","kibana.alert.threshold_result"],query:{bool:{filter:[
    {term:{"kibana.alert.rule.rule_id":"openshell-policy-denial-burst-v1"}},
    {term:{"kibana.alert.status":"active"}}
  ]}}}')
burst_response=$(es --header 'Content-Type: application/json' \
  --request POST \
  --data "$burst_query" \
  'https://127.0.0.1:9200/.alerts-security.alerts-default/_search')
burst_count=$(printf '%s' "$burst_response" | jq -r '.hits.total.value')
[ "$burst_count" -gt 0 ] || { echo "three real policy denials did not produce a high-severity burst alert" >&2; exit 1; }
printf '%s' "$burst_response" | jq -e --arg sandbox "$OPENSHELL_DEMO_SANDBOX_ID" '
  any(.hits.hits[];
    ._source["kibana.alert.workflow_status"] == "open" and
    ._source["kibana.alert.threshold_result"].count >= 3 and
    any(._source["kibana.alert.threshold_result"].terms[]?;
      .field == "openshell.sandbox.id" and .value == $sandbox))
' >/dev/null || { echo "denial-burst alert does not preserve the grouped sandbox identity" >&2; exit 1; }

cross_lane_query=$(jq -cn \
  --arg started "$DEMO_TASK_STARTED_AT" \
  '{size:10,_source:["@timestamp","kibana.alert.rule.rule_id","kibana.alert.status","kibana.alert.workflow_status","kibana.alert.original_time","openshell.sandbox.id"],query:{bool:{filter:[
    {term:{"kibana.alert.rule.rule_id":"openshell-agent-policy-denial-sequence-v1"}},
    {term:{"kibana.alert.rule.version":3}},
    {term:{"kibana.alert.status":"active"}},
    {range:{"@timestamp":{gte:$started}}}
  ],must_not:[{exists:{field:"kibana.alert.building_block_type"}}]}}}')
cross_lane_response=$(es --header 'Content-Type: application/json' \
  --request POST \
  --data "$cross_lane_query" \
  'https://127.0.0.1:9200/.alerts-security.alerts-default/_search')
cross_lane_count=$(printf '%s' "$cross_lane_response" | jq -r '.hits.total.value')
[ "$cross_lane_count" -eq 1 ] || { echo "expected one analyst-facing root-agent denial sequence, got $cross_lane_count" >&2; exit 1; }
printf '%s' "$cross_lane_response" | jq -e --arg sandbox "$OPENSHELL_DEMO_SANDBOX_ID" '
  any(.hits.hits[];
    ._source.openshell.sandbox.id == $sandbox and
    ._source["kibana.alert.workflow_status"] == "open")
' >/dev/null || { echo "cross-lane alert does not preserve the real sandbox identity and open workflow state" >&2; exit 1; }

soc_notifications=$(es --header 'Content-Type: application/json' --request POST \
  --data '{"size":1000,"query":{"match_all":{}}}' \
  'https://127.0.0.1:9200/openshell-soc-notifications-default-v1/_search')
soc_notification_count=$(printf '%s' "$soc_notifications" | jq '.hits.total.value')
[ "$soc_notification_count" -gt 0 ] || { echo "Elastic detection did not reach the local SOC notification audit index" >&2; exit 1; }
printf '%s' "$soc_notifications" | jq -e '
  [.hits.hits[]._source] | all(.[];
    .producer == "elastic-security" and .schema_version == "1.0" and
    .environment == "default" and .rule.severity == "high" and
    .alert.id != "" and .indexed_at != "" and
    .prompt == null and .response == null and .original == null and
    .tool_arguments == null and .credentials == null)
' >/dev/null || { echo "SOC notification audit retained unreviewed or privacy-sensitive fields" >&2; exit 1; }

printf 'Verified Elastic Security: events=%d, denials=%d, sandbox-correlated=%d, agent-traces=%d, complete-agent-traces=%d, privacy-leaks=%d, duplicate-count=%d, single-alerts=%d, burst-alerts=%d, cross-lane-alerts=%d, soc-notifications=%d.\n' \
  "$security_events" "$denials" "$sandbox_events" "$agent_traces" "$agent_complete_traces" "$privacy_leaks" "$after" "$alert_count" "$burst_count" "$cross_lane_count" "$soc_notification_count"
