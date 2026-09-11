#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -Eeuo pipefail
umask 077

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
mode=${1:-plan}
environment=${OPENSHELL_ELASTIC_ENVIRONMENT:-production}
kibana_space=${OPENSHELL_KIBANA_SPACE:-openshell-production}
agent_traces_enabled=${OPENSHELL_AGENT_TRACES_ENABLED:-false}
max_age_seconds=${OPENSHELL_MAX_EVIDENCE_AGE_SECONDS:-900}
connector_id=${OPENSHELL_SOC_WEBHOOK_CONNECTOR_ID:-}
connector_type=${OPENSHELL_SOC_CONNECTOR_TYPE:-.webhook}
allow_local_index_connector=${OPENSHELL_ALLOW_LOCAL_INDEX_CONNECTOR:-false}
body_template="$script_dir/assets/soc-webhook-body.json"
manifest="$script_dir/manifest.json"
rule_ids=(
  openshell-policy-denial-v1
  openshell-validation-failure-v1
  openshell-source-gap-v1
  openshell-policy-draft-change-v1
  openshell-policy-denial-burst-v1
  openshell-agent-trace-error-v1
  openshell-agent-correlation-gap-v1
  openshell-agent-policy-denial-sequence-v1
  openshell-policy-denial-destination-probe-v1
  openshell-policy-change-denial-sequence-v1
  openshell-denial-source-gap-sequence-v1
)
escalation_rule_ids=(
  openshell-validation-failure-v1
  openshell-source-gap-v1
  openshell-policy-denial-burst-v1
  openshell-agent-policy-denial-sequence-v1
  openshell-policy-denial-destination-probe-v1
  openshell-denial-source-gap-sequence-v1
)

fail() {
  printf 'elastic-soc shift check: %s\n' "$*" >&2
  exit 1
}

case "$mode" in
  plan)
    printf 'OpenShell Elastic SOC shift-check plan\n'
    printf '  environment: %s\n  Kibana space: %s\n' "$environment" "$kibana_space"
    printf '  security evidence: source and destination acceptance required and fresh within %s seconds\n' "$max_age_seconds"
    printf '  agent traces: %s\n' "$agent_traces_enabled"
    printf '  detections: eleven enabled and healthy rules\n'
    printf '  escalation routing: six exact rules through one healthy %s connector\n' "$connector_type"
    printf '  cases: aggregate open, in-progress, and closed workload only\n'
    printf 'No alert, case, rule, connector, evidence, or OpenShell state is changed.\n'
    exit 0
    ;;
  run) ;;
  *) fail "usage: $0 {plan|run}" ;;
esac

require_https_url() {
  local name=$1 value=$2
  [[ "$value" == https://* ]] || fail "$name must use https"
  [[ "$value" != */ ]] || fail "$name must not end with a slash"
}

require_secret_header() {
  local name=$1 file=$2 mode_value line_count
  [[ -r "$file" && -f "$file" ]] || fail "$name is not a readable regular file"
  line_count=$(awk 'END { print NR }' "$file")
  [[ "$line_count" == 1 ]] || fail "$name must contain exactly one header line"
  grep -Eq '^Authorization: (ApiKey|Basic|Bearer) [^[:space:]]+$' "$file" ||
    fail "$name must contain one ApiKey, Basic, or Bearer Authorization header"
  mode_value=$(stat -c '%a' "$file" 2>/dev/null || stat -f '%Lp' "$file")
  (( (8#$mode_value & 077) == 0 )) || fail "$name must not be group/world accessible"
}

command -v curl >/dev/null || fail "curl is required"
command -v jq >/dev/null || fail "jq is required"
command -v sha256sum >/dev/null || command -v shasum >/dev/null ||
  fail "sha256sum or shasum is required"
[[ "$environment" =~ ^[a-z0-9][a-z0-9-]{0,31}$ ]] || fail "invalid Elastic environment"
[[ "$kibana_space" =~ ^[a-z0-9][a-z0-9_-]{0,63}$ ]] || fail "invalid Kibana space"
[[ "$agent_traces_enabled" == true || "$agent_traces_enabled" == false ]] ||
  fail "OPENSHELL_AGENT_TRACES_ENABLED must be true or false"
[[ "$max_age_seconds" =~ ^[1-9][0-9]{1,6}$ ]] ||
  fail "OPENSHELL_MAX_EVIDENCE_AGE_SECONDS must be between 10 and 9999999 seconds"
[[ "$connector_id" =~ ^[a-z0-9][a-z0-9-]{0,35}$ ]] || fail "invalid SOC connector identifier"
[[ "$connector_type" == .webhook || "$connector_type" == .index ]] ||
  fail "OPENSHELL_SOC_CONNECTOR_TYPE must be .webhook or .index"

elasticsearch_url=${ELASTICSEARCH_URL:-}
kibana_url=${KIBANA_URL:-}
ca_file=${ELASTIC_CA_FILE:-}
es_header=${ELASTICSEARCH_ANALYST_AUTH_HEADER_FILE:-}
kibana_header=${KIBANA_ANALYST_AUTH_HEADER_FILE:-}
report_output=${OPENSHELL_SHIFT_REPORT_OUTPUT:-}
require_https_url ELASTICSEARCH_URL "$elasticsearch_url"
require_https_url KIBANA_URL "$kibana_url"
[[ -r "$ca_file" && -f "$ca_file" ]] || fail "ELASTIC_CA_FILE must be a readable CA file"
require_secret_header ELASTICSEARCH_ANALYST_AUTH_HEADER_FILE "$es_header"
require_secret_header KIBANA_ANALYST_AUTH_HEADER_FILE "$kibana_header"
[[ "$report_output" == /* ]] || fail "OPENSHELL_SHIFT_REPORT_OUTPUT must be an absolute path"
[[ ! -e "$report_output" ]] || fail "refusing to replace existing shift report: $report_output"
report_dir=$(dirname -- "$report_output")
[[ -d "$report_dir" && -w "$report_dir" ]] || fail "shift report directory is not writable"
if [[ "$connector_type" == .index ]]; then
  [[ "$allow_local_index_connector" == true && "$environment" == default &&
    "$elasticsearch_url" == https://127.0.0.1:* && "$kibana_url" == https://127.0.0.1:* ]] ||
    fail ".index connector is restricted to the explicit loopback local fixture"
fi

client_cert=${ELASTIC_CLIENT_CERT_FILE:-}
client_key=${ELASTIC_CLIENT_KEY_FILE:-}
if [[ -n "$client_cert" || -n "$client_key" ]]; then
  [[ -r "$client_cert" && -r "$client_key" ]] ||
    fail "ELASTIC_CLIENT_CERT_FILE and ELASTIC_CLIENT_KEY_FILE are required together"
fi
curl_args=(--fail-with-body --silent --show-error --cacert "$ca_file" --max-time 30)
if [[ -n "$client_cert" ]]; then
  curl_args+=(--cert "$client_cert" --key "$client_key")
fi
kibana_prefix=
[[ "$kibana_space" == default ]] || kibana_prefix="/s/$kibana_space"
security_index="logs-openshell.security-${environment}-v1"
trace_index="traces-openshell.agent-${environment}-v1"
checked_at=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
pack_version=$(jq -er '.pack_version' "$manifest")
work_dir=$(mktemp -d "$report_dir/.openshell-shift.XXXXXX")
trap 'rm -rf "$work_dir"' EXIT
failures=()

es_read() { curl "${curl_args[@]}" --header "@$es_header" "$@"; }
kibana_read() { curl "${curl_args[@]}" --header "@$kibana_header" "$@"; }
add_failure() { failures+=("$1"); }

check_index() {
  local lane=$1 index=$2 enabled=$3 output=$4 response total source_recent delivery_recent latest_source latest_ingested status query
  if [[ "$enabled" == false ]]; then
    jq -n --arg index "$index" --argjson age "$max_age_seconds" '{enabled:false,status:"disabled",index:$index,document_count:0,source_recent_document_count:0,delivery_recent_document_count:0,latest_source_timestamp:null,latest_ingested_timestamp:null,max_age_seconds:$age}' >"$output"
    return
  fi
  query=$(jq -cn --argjson age "$max_age_seconds" '{size:0,track_total_hits:true,aggs:{latest_source:{max:{field:"@timestamp"}},latest_ingested:{max:{field:"event.ingested"}},source_recent:{filter:{range:{"@timestamp":{gte:("now-" + ($age|tostring) + "s")}}}},delivery_recent:{filter:{range:{"event.ingested":{gte:("now-" + ($age|tostring) + "s")}}}}}}')
  if ! response=$(es_read --request GET --header 'Content-Type: application/json' --data "$query" "$elasticsearch_url/$index/_search"); then
    add_failure "$lane evidence query failed"
    jq -n --arg index "$index" --argjson age "$max_age_seconds" '{enabled:true,status:"unreachable",index:$index,document_count:0,source_recent_document_count:0,delivery_recent_document_count:0,latest_source_timestamp:null,latest_ingested_timestamp:null,max_age_seconds:$age}' >"$output"
    return
  fi
  if ! jq -e '.hits.total.value >= 0 and .aggregations.source_recent.doc_count >= 0 and .aggregations.delivery_recent.doc_count >= 0 and ((.aggregations.latest_source.value_as_string // null) | type == "string" or . == null) and ((.aggregations.latest_ingested.value_as_string // null) | type == "string" or . == null)' <<<"$response" >/dev/null; then
    add_failure "$lane evidence response is invalid"
    jq -n --arg index "$index" --argjson age "$max_age_seconds" '{enabled:true,status:"invalid_response",index:$index,document_count:0,source_recent_document_count:0,delivery_recent_document_count:0,latest_source_timestamp:null,latest_ingested_timestamp:null,max_age_seconds:$age}' >"$output"
    return
  fi
  total=$(jq -r '.hits.total.value' <<<"$response")
  source_recent=$(jq -r '.aggregations.source_recent.doc_count' <<<"$response")
  delivery_recent=$(jq -r '.aggregations.delivery_recent.doc_count' <<<"$response")
  latest_source=$(jq -c '.aggregations.latest_source.value_as_string // null' <<<"$response")
  latest_ingested=$(jq -c '.aggregations.latest_ingested.value_as_string // null' <<<"$response")
  status=healthy
  if (( total == 0 )); then
    status=empty
    add_failure "$lane evidence is empty"
  elif (( source_recent == 0 && delivery_recent == 0 )); then
    status=stale
    add_failure "$lane source and destination acceptance are stale"
  elif (( source_recent == 0 )); then
    status=source_stale
    add_failure "$lane source activity is stale while destination acceptance is active"
  elif (( delivery_recent == 0 )); then
    status=delivery_stale
    add_failure "$lane destination acceptance is stale while source timestamps are recent"
  fi
  jq -n --arg index "$index" --arg status "$status" --argjson total "$total" --argjson source_recent "$source_recent" --argjson delivery_recent "$delivery_recent" --argjson latest_source "$latest_source" --argjson latest_ingested "$latest_ingested" --argjson age "$max_age_seconds" '{enabled:true,status:$status,index:$index,document_count:$total,source_recent_document_count:$source_recent,delivery_recent_document_count:$delivery_recent,latest_source_timestamp:$latest_source,latest_ingested_timestamp:$latest_ingested,max_age_seconds:$age}' >"$output"
}

check_index security "$security_index" true "$work_dir/security.json"
check_index agent "$trace_index" "$agent_traces_enabled" "$work_dir/agent.json"
body=$(jq -c --arg environment "$environment" '.environment = $environment' "$body_template")
healthy_rules=0
verified_actions=0
for rule_id in "${rule_ids[@]}"; do
  if ! rule=$(kibana_read "$kibana_url$kibana_prefix/api/detection_engine/rules?rule_id=$rule_id"); then
    add_failure "detection rule unavailable: $rule_id"
    continue
  fi
  arrival_safe=true
  case "$rule_id" in
    openshell-policy-denial-burst-v1|openshell-agent-policy-denial-sequence-v1|openshell-policy-denial-destination-probe-v1|openshell-policy-change-denial-sequence-v1|openshell-denial-source-gap-sequence-v1) arrival_safe=false ;;
  esac
  if jq -e --argjson arrival_safe "$arrival_safe" '.enabled == true and .execution_summary.last_execution.status == "succeeded" and (if $arrival_safe then .timestamp_override == "event.ingested" and .timestamp_override_fallback_disabled == true else (.timestamp_override // null) == null and (.timestamp_override_fallback_disabled // null) == null end)' <<<"$rule" >/dev/null; then
    healthy_rules=$((healthy_rules + 1))
  else
    add_failure "detection rule disabled or unhealthy: $rule_id"
  fi
  if printf '%s\n' "${escalation_rule_ids[@]}" | grep -Fxq "$rule_id"; then
    if jq -e --arg id "$connector_id" --arg type "$connector_type" --arg body "$body" --argjson body_object "$body" '
      .actions | length == 1 and .[0].id == $id and .[0].action_type_id == $type and
      .[0].group == "default" and .[0].frequency.summary == false and
      .[0].frequency.notifyWhen == "onActiveAlert" and .[0].frequency.throttle == null and
      (if $type == ".webhook" then .[0].params.body == $body
       else (.[0].params.documents | length == 1 and .[0] == $body_object) end)
    ' <<<"$rule" >/dev/null; then
      verified_actions=$((verified_actions + 1))
    else
      add_failure "escalation action drift: $rule_id"
    fi
  fi
done

connector_status=degraded
if connector=$(kibana_read "$kibana_url$kibana_prefix/api/actions/connector/$connector_id") &&
  jq -e --arg id "$connector_id" --arg type "$connector_type" '.id == $id and .connector_type_id == $type and (.is_deprecated // false) == false and (.is_connector_type_deprecated // false) == false and (.is_missing_secrets // false) == false' <<<"$connector" >/dev/null; then
  connector_status=healthy
else
  add_failure "SOC connector is unavailable, deprecated, or missing secrets"
fi

if cases=$(kibana_read "$kibana_url$kibana_prefix/api/cases/_find?owner=securitySolution&perPage=1") &&
  jq -e '.total >= 0 and .count_open_cases >= 0 and .count_in_progress_cases >= 0 and .count_closed_cases >= 0' <<<"$cases" >/dev/null; then
  jq '{status:"observed",total,open:.count_open_cases,in_progress:.count_in_progress_cases,closed:.count_closed_cases}' <<<"$cases" >"$work_dir/cases.json"
else
  add_failure "Elastic Security case workload is unavailable"
  jq -n '{status:"unreachable",total:0,open:0,in_progress:0,closed:0}' >"$work_dir/cases.json"
fi

overall_status=passed
(( ${#failures[@]} == 0 )) || overall_status=failed
failures_json=$(printf '%s\n' "${failures[@]-}" | jq -Rsc 'split("\n") | map(select(length > 0))')
detection_status=healthy
(( healthy_rules == ${#rule_ids[@]} )) || detection_status=degraded
routing_status=healthy
(( verified_actions == ${#escalation_rule_ids[@]} )) || routing_status=degraded
jq -n --arg status "$overall_status" --arg checked_at "$checked_at" --arg pack_version "$pack_version" --arg environment "$environment" --arg space "$kibana_space" --slurpfile security "$work_dir/security.json" --slurpfile agent "$work_dir/agent.json" --arg detection_status "$detection_status" --argjson expected_rules "${#rule_ids[@]}" --argjson healthy_rules "$healthy_rules" --arg routing_status "$routing_status" --arg connector_id "$connector_id" --arg connector_type "$connector_type" --arg connector_status "$connector_status" --argjson expected_actions "${#escalation_rule_ids[@]}" --argjson verified_actions "$verified_actions" --slurpfile cases "$work_dir/cases.json" --argjson failures "$failures_json" '{schema_version:"1.1",status:$status,checked_at:$checked_at,pack_version:$pack_version,target:{environment:$environment,kibana_space:$space},checks:{security_evidence:$security[0],agent_traces:$agent[0],detections:{status:$detection_status,expected_rules:$expected_rules,healthy_rules:$healthy_rules},escalation_routing:{status:$routing_status,expected_actions:$expected_actions,verified_actions:$verified_actions,connector_id:$connector_id,connector_type:$connector_type,connector_status:$connector_status},cases:$cases[0]},failures:$failures}' >"$work_dir/report.json"
ln "$work_dir/report.json" "$report_output" || fail "could not publish append-only shift report"
report_sha=$(if command -v sha256sum >/dev/null 2>&1; then sha256sum "$report_output" | awk '{print $1}'; else shasum -a 256 "$report_output" | awk '{print $1}'; fi)
printf 'OpenShell Elastic SOC shift check: %s\n' "$overall_status"
printf '  evidence: security=%s, agent=%s\n' "$(jq -r '.status' "$work_dir/security.json")" "$(jq -r '.status' "$work_dir/agent.json")"
printf '  detections: %s/%s healthy\n' "$healthy_rules" "${#rule_ids[@]}"
printf '  escalation routes: %s/%s verified; connector=%s\n' "$verified_actions" "${#escalation_rule_ids[@]}" "$connector_status"
printf '  report: %s\n  sha256: %s\n' "$report_output" "$report_sha"
[[ "$overall_status" == passed ]]
