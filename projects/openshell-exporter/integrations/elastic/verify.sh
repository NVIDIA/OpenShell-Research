#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -Eeuo pipefail
umask 077

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
assets_dir="$script_dir/assets"
environment=${OPENSHELL_ELASTIC_ENVIRONMENT:-production}
kibana_space=${OPENSHELL_KIBANA_SPACE:-openshell-production}
security_index="logs-openshell.security-${environment}-v1"
trace_index="traces-openshell.agent-${environment}-v1"
role_suffix=${environment//-/_}
ingest_role="openshell_ingest_${role_suffix}"
viewer_role="openshell_soc_viewer_${role_suffix}"
analyst_role="openshell_soc_analyst_${role_suffix}"
detection_role="openshell_soc_detection_engineer_${role_suffix}"
minimum_nodes=${OPENSHELL_MIN_ELASTIC_NODES:-3}
minimum_data_nodes=${OPENSHELL_MIN_ELASTIC_DATA_NODES:-2}
require_rule_execution=${OPENSHELL_REQUIRE_RULE_EXECUTION:-true}
evidence_output=${OPENSHELL_EVIDENCE_OUTPUT:-}
evidence_sha256=

fail() {
  printf 'elastic-soc verify: %s\n' "$*" >&2
  exit 1
}

require_tool() {
  command -v "$1" >/dev/null 2>&1 || fail "required tool is missing: $1"
}

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
  grep -Eq '^Authorization: (ApiKey|Bearer) [^[:space:]]+$' "$file" ||
    fail "$name must contain one ApiKey or Bearer Authorization header"
  mode_value=$(stat -c '%a' "$file" 2>/dev/null || stat -f '%Lp' "$file")
  [[ "$mode_value" =~ ^[0-7]{3,4}$ ]] || fail "cannot determine permissions for $name"
  (( (8#$mode_value & 077) == 0 )) || fail "$name must not be group/world accessible"
}

verify_asset_hashes() {
  if command -v sha256sum >/dev/null 2>&1; then
    (cd "$script_dir" && sha256sum --check --quiet assets.sha256) ||
      fail "reviewed Elastic asset checksum mismatch"
  elif command -v shasum >/dev/null 2>&1; then
    (cd "$script_dir" && shasum -a 256 --check assets.sha256 >/dev/null) ||
      fail "reviewed Elastic asset checksum mismatch"
  else
    fail "sha256sum or shasum is required"
  fi
}
sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{ print $1 }'
  else
    shasum -a 256 "$1" | awk '{ print $1 }'
  fi
}
verify_asset_hashes

[[ "$environment" =~ ^[a-z0-9][a-z0-9-]{0,31}$ ]] ||
  fail "OPENSHELL_ELASTIC_ENVIRONMENT has an invalid identifier"
[[ "$kibana_space" =~ ^[a-z0-9][a-z0-9_-]{0,63}$ ]] ||
  fail "OPENSHELL_KIBANA_SPACE has an invalid identifier"
[[ "$minimum_nodes" =~ ^[1-9][0-9]*$ && "$minimum_data_nodes" =~ ^[1-9][0-9]*$ ]] ||
  fail "minimum node counts must be positive integers"
[[ "$require_rule_execution" == true || "$require_rule_execution" == false ]] ||
  fail "OPENSHELL_REQUIRE_RULE_EXECUTION must be true or false"
if [[ -n "$evidence_output" ]] && (( minimum_nodes < 3 || minimum_data_nodes < 2 )); then
  fail "conformance evidence requires at least three nodes and two data nodes"
fi

require_tool curl
require_tool cmp
require_tool jq
elasticsearch_url=${ELASTICSEARCH_URL:-}
kibana_url=${KIBANA_URL:-}
elastic_ca=${ELASTIC_CA_FILE:-}
snapshot_repository=${OPENSHELL_SNAPSHOT_REPOSITORY:-}
retention_approval=${OPENSHELL_RETENTION_APPROVAL:-}
security_lifecycle_policy=${OPENSHELL_SECURITY_LIFECYCLE_POLICY:-}
agent_lifecycle_policy=${OPENSHELL_AGENT_LIFECYCLE_POLICY:-}
detection_principal=${OPENSHELL_DETECTION_PRINCIPAL:-}
require_https_url ELASTICSEARCH_URL "$elasticsearch_url"
require_https_url KIBANA_URL "$kibana_url"
[[ -r "$elastic_ca" && -f "$elastic_ca" ]] ||
  fail "ELASTIC_CA_FILE must be a readable CA file"
[[ "$snapshot_repository" =~ ^[A-Za-z0-9._-]+$ ]] ||
  fail "OPENSHELL_SNAPSHOT_REPOSITORY is required and has an invalid name"
[[ "$retention_approval" =~ ^[A-Za-z0-9._:/-]+$ ]] ||
  fail "OPENSHELL_RETENTION_APPROVAL is required and has an invalid reference"
[[ "$security_lifecycle_policy" =~ ^[A-Za-z0-9._-]+$ ]] ||
  fail "OPENSHELL_SECURITY_LIFECYCLE_POLICY is required and has an invalid name"
[[ "$agent_lifecycle_policy" =~ ^[A-Za-z0-9._-]+$ ]] ||
  fail "OPENSHELL_AGENT_LIFECYCLE_POLICY is required and has an invalid name"
[[ "$security_lifecycle_policy" != "$agent_lifecycle_policy" ]] ||
  fail "security evidence and agent traces require distinct lifecycle policies"
[[ "$detection_principal" =~ ^[A-Za-z0-9._@-]+$ ]] ||
  fail "OPENSHELL_DETECTION_PRINCIPAL is required and has an invalid identifier"

es_read_header=${ELASTICSEARCH_READ_AUTH_HEADER_FILE:-}
kibana_read_header=${KIBANA_READ_AUTH_HEADER_FILE:-}
es_ingest_header=${ELASTICSEARCH_INGEST_AUTH_HEADER_FILE:-}
require_secret_header ELASTICSEARCH_READ_AUTH_HEADER_FILE "$es_read_header"
require_secret_header KIBANA_READ_AUTH_HEADER_FILE "$kibana_read_header"
require_secret_header ELASTICSEARCH_INGEST_AUTH_HEADER_FILE "$es_ingest_header"
cmp -s "$es_read_header" "$es_ingest_header" &&
  fail "read and runtime ingest identities must be distinct"

client_cert=${ELASTIC_CLIENT_CERT_FILE:-}
client_key=${ELASTIC_CLIENT_KEY_FILE:-}
if [[ -n "$client_cert" || -n "$client_key" ]]; then
  [[ -r "$client_cert" && -r "$client_key" ]] ||
    fail "ELASTIC_CLIENT_CERT_FILE and ELASTIC_CLIENT_KEY_FILE are required together"
fi
curl_common=(--silent --show-error --fail-with-body --cacert "$elastic_ca")
if [[ -n "$client_cert" ]]; then
  curl_common+=(--cert "$client_cert" --key "$client_key")
fi

es_read() {
  curl "${curl_common[@]}" --header "@$es_read_header" "$@"
}

es_ingest() {
  curl "${curl_common[@]}" --header "@$es_ingest_header" "$@"
}

kibana_read() {
  curl "${curl_common[@]}" --header "@$kibana_read_header" "$@"
}

kibana_prefix=
if [[ "$kibana_space" != default ]]; then
  kibana_prefix="/s/$kibana_space"
fi

manifest=$(jq -c . "$script_dir/manifest.json")
expected_es_version=$(jq -r .validated_elasticsearch_version <<<"$manifest")
expected_kibana_version=$(jq -r .validated_kibana_version <<<"$manifest")
expected_rules=$(jq -r .rule_count <<<"$manifest")
expected_saved_objects=$(jq -r .saved_object_count <<<"$manifest")

cluster_root=$(es_read "$elasticsearch_url/")
cluster_health=$(es_read "$elasticsearch_url/_cluster/health")
[[ $(jq -r .version.number <<<"$cluster_root") == "$expected_es_version" ]] ||
  fail "Elasticsearch version does not match validated $expected_es_version"
jq -e --argjson nodes "$minimum_nodes" --argjson data_nodes "$minimum_data_nodes" '
  .status == "green" and
  .number_of_nodes >= $nodes and
  .number_of_data_nodes >= $data_nodes and
  .timed_out == false
' <<<"$cluster_health" >/dev/null ||
  fail "cluster is not green or lacks the required node topology"

snapshot=$(es_read "$elasticsearch_url/_snapshot/$snapshot_repository")
jq -e --arg repository "$snapshot_repository" 'has($repository)' <<<"$snapshot" >/dev/null ||
  fail "approved snapshot repository is unavailable"

ilm_status=$(es_read "$elasticsearch_url/_ilm/status")
jq -e '.operation_mode == "RUNNING"' <<<"$ilm_status" >/dev/null ||
  fail "index lifecycle management is not running"

for lifecycle_policy in "$security_lifecycle_policy" "$agent_lifecycle_policy"; do
  lifecycle=$(es_read "$elasticsearch_url/_ilm/policy/$lifecycle_policy")
  jq -e --arg policy "$lifecycle_policy" '
    has($policy) and .[$policy].policy.phases != null
  ' <<<"$lifecycle" >/dev/null || fail "approved lifecycle policy is unavailable: $lifecycle_policy"
done

for template in   "component:openshell-security-ecs-v1"   "component:openshell-agent-trace-ecs-v1"   "index:openshell-security-v1"   "index:openshell-agent-trace-v1"
do
  template_type=${template%%:*}
  template_name=${template#*:}
  if [[ "$template_type" == component ]]; then
    payload=$(es_read "$elasticsearch_url/_component_template/$template_name")
    jq -e --arg name "$template_name" '
      any(.component_templates[];
        .name == $name and
        .component_template.version == 1 and
        .component_template._meta.managed_by == "openshell-event-exporter-reference")
    ' <<<"$payload" >/dev/null || fail "component template drift: $template_name"
  else
    payload=$(es_read "$elasticsearch_url/_index_template/$template_name")
    jq -e --arg name "$template_name" '
      any(.index_templates[];
        .name == $name and
        .index_template.version == 1 and
        .index_template._meta.managed_by == "openshell-event-exporter-reference")
    ' <<<"$payload" >/dev/null || fail "index template drift: $template_name"
  fi
done

for index_spec in "$security_index:$security_lifecycle_policy" "$trace_index:$agent_lifecycle_policy"; do
  index=${index_spec%%:*}
  lifecycle_policy=${index_spec#*:}
  index_health=$(es_read "$elasticsearch_url/_cluster/health/$index")
  jq -e '.status == "green" and .active_primary_shards > 0 and .unassigned_shards == 0'     <<<"$index_health" >/dev/null || fail "index is not green: $index"
  index_settings=$(es_read "$elasticsearch_url/$index/_settings?flat_settings=true")
  jq -e --arg index "$index" --arg policy "$lifecycle_policy" '
    (.[$index].settings["index.number_of_replicas"] | tonumber) >= 1 and
    .[$index].settings["index.lifecycle.name"] == $policy
  ' <<<"$index_settings" >/dev/null ||
    fail "index replica or lifecycle settings drift: $index"
  lifecycle_explain=$(es_read "$elasticsearch_url/$index/_ilm/explain")
  jq -e --arg index "$index" --arg policy "$lifecycle_policy" '
    .indices[$index].managed == true and
    .indices[$index].policy == $policy and
    (.indices[$index].failed_step // null) == null and
    (.indices[$index].step_info // null) == null
  ' <<<"$lifecycle_explain" >/dev/null ||
    fail "index lifecycle policy is not healthy: $index"
done

role_payload=$(es_read "$elasticsearch_url/_security/role/$ingest_role")
jq -e --arg role "$ingest_role" --arg security "$security_index" --arg trace "$trace_index" '
  .[$role].cluster == ["monitor"] and
  .[$role].indices[0].names == [$security, $trace] and
  .[$role].indices[0].privileges == ["create_doc", "view_index_metadata"] and
  .[$role].applications == [] and
  .[$role].run_as == []
' <<<"$role_payload" >/dev/null || fail "runtime ingest role drift"

runtime_auth=$(es_ingest "$elasticsearch_url/_security/_authenticate")
jq -e '.username != "elastic" and ((.roles // []) | index("superuser") | not)'   <<<"$runtime_auth" >/dev/null || fail "runtime ingest identity is a superuser"

privilege_request=$(jq -cn --arg security "$security_index" --arg trace "$trace_index" '{
  cluster:["monitor","manage_security","manage_index_templates"],
  index:[{names:[$security,$trace],privileges:[
    "create_doc","view_index_metadata","read","write","delete","delete_index","manage"
  ]}]
}')
privileges=$(es_ingest --request POST --header 'Content-Type: application/json'   --data "$privilege_request" "$elasticsearch_url/_security/user/_has_privileges")
jq -e --arg security "$security_index" --arg trace "$trace_index" '
  . as $response |
  .has_all_requested == false and
  .cluster.monitor == true and
  .cluster.manage_security == false and
  .cluster.manage_index_templates == false and
  all([$security,$trace][]; . as $index |
    $response.index[$index].create_doc == true and
    $response.index[$index].view_index_metadata == true and
    $response.index[$index].read == false and
    $response.index[$index].write == false and
    $response.index[$index].delete == false and
    $response.index[$index].delete_index == false and
    $response.index[$index].manage == false)
' <<<"$privileges" >/dev/null || fail "runtime ingest privilege boundary is incorrect"

kibana_status=$(kibana_read "$kibana_url/api/status")
[[ $(jq -r .version.number <<<"$kibana_status") == "$expected_kibana_version" ]] ||
  fail "Kibana version does not match validated $expected_kibana_version"
jq -e '.status.overall.level == "available"' <<<"$kibana_status" >/dev/null ||
  fail "Kibana is not available"

for role_spec in "$viewer_role:viewer" "$analyst_role:analyst" "$detection_role:detection"; do
  role_name=${role_spec%%:*}
  role_profile=${role_spec#*:}
  role=$(kibana_read "$kibana_url/api/security/role/$role_name")
  jq -e --arg security "$security_index" --arg trace "$trace_index"     --arg space "$kibana_space" --arg profile "$role_profile" '
    .elasticsearch.cluster == [] and
    .elasticsearch.indices[0].names == [$security,$trace] and
    .elasticsearch.indices[0].privileges == ["read","view_index_metadata"] and
    .elasticsearch.run_as == [] and .kibana[0].base == [] and
    .kibana[0].spaces == [$space] and
    .kibana[0].feature.discover_v2 == ["read"] and
    .kibana[0].feature.dashboard_v2 == ["read"] and
    .kibana[0].feature.siemV5 == ["read"] and
    (if $profile == "viewer" then
      .kibana[0].feature.securitySolutionTimeline == ["read"] and
      .kibana[0].feature.securitySolutionNotes == ["read"] and
      .kibana[0].feature.securitySolutionAlertsV1 == ["read"] and
      .kibana[0].feature.securitySolutionCasesV3 == ["read"] and
      .kibana[0].feature.securitySolutionRulesV4 == ["read"] and
      (.kibana[0].feature.actions // []) == []
    elif $profile == "analyst" then
      .kibana[0].feature.securitySolutionTimeline == ["all"] and
      .kibana[0].feature.securitySolutionNotes == ["all"] and
      .kibana[0].feature.securitySolutionAlertsV1 == ["all"] and
      .kibana[0].feature.securitySolutionCasesV3 == ["all"] and
      .kibana[0].feature.securitySolutionRulesV4 == ["read"] and
      .kibana[0].feature.actions == ["read"]
    else
      .kibana[0].feature.securitySolutionTimeline == ["all"] and
      .kibana[0].feature.securitySolutionNotes == ["all"] and
      .kibana[0].feature.securitySolutionAlertsV1 == ["all"] and
      .kibana[0].feature.securitySolutionCasesV3 == ["all"] and
      .kibana[0].feature.securitySolutionRulesV4 == ["all"] and
      .kibana[0].feature.actions == ["read"]
    end)
  ' <<<"$role" >/dev/null || fail "Kibana role separation drift: $role_name"
done

for view_spec in   "openshell-security:$security_index"   "openshell-agent-traces:$trace_index"
do
  view_id=${view_spec%%:*}
  view_title=${view_spec#*:}
  view=$(kibana_read "$kibana_url$kibana_prefix/api/data_views/data_view/$view_id")
  jq -e --arg id "$view_id" --arg title "$view_title" '
    .data_view.id == $id and .data_view.title == $title and
    .data_view.timeFieldName == "@timestamp"
  ' <<<"$view" >/dev/null || fail "data view drift: $view_id"
done

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
  dashboard=$(kibana_read "$kibana_url$kibana_prefix/api/saved_objects/dashboard/$dashboard_id")
  jq -e --arg id "$dashboard_id" --arg title "$dashboard_title" --argjson panels "$dashboard_panels" --argjson references "$dashboard_references" '
    .id == $id and .attributes.title == $title and
    (.attributes.panelsJSON | fromjson | length) == $panels and
    (.references | length) == $references
  ' <<<"$dashboard" >/dev/null || fail "OpenShell SOC dashboard drift: $dashboard_id"
done

rule_count=0
while IFS= read -r expected_rule; do
  [[ -n "$expected_rule" ]] || continue
  rule_id=$(jq -r .rule_id <<<"$expected_rule")
  expected_version=$(jq -r .version <<<"$expected_rule")
  rule=$(kibana_read "$kibana_url$kibana_prefix/api/detection_engine/rules?rule_id=$rule_id")
  jq -e --argjson version "$expected_version" --arg environment "$environment" --arg security "$security_index" --arg trace "$trace_index" --argjson expected "$expected_rule" '
    .enabled == true and .version == $version and
    (.tags | index("Environment: " + $environment)) != null and
    all(.index[]; . == $security or . == $trace) and
    ((.timestamp_override // null) == ($expected.timestamp_override // null)) and
    ((.timestamp_override_fallback_disabled // null) == ($expected.timestamp_override_fallback_disabled // null))
  ' <<<"$rule" >/dev/null || fail "detection rule drift: $rule_id"
  if [[ "$require_rule_execution" == true ]]; then
    jq -e '.execution_summary.last_execution.status == "succeeded"' <<<"$rule" >/dev/null ||
      fail "detection rule has not completed successfully: $rule_id"
  fi
  rule_object_id=$(jq -r '.id // empty' <<<"$rule")
  [[ -n "$rule_object_id" ]] || fail "detection rule lacks an object ID: $rule_id"
  alerting_rule=$(kibana_read "$kibana_url$kibana_prefix/api/alerting/rule/$rule_object_id")
  jq -e --arg owner "$detection_principal" '
    .api_key_owner == $owner and .api_key_created_by_user == false
  ' <<<"$alerting_rule" >/dev/null ||
    fail "detection execution key has an unexpected owner: $rule_id"
  rule_count=$((rule_count + 1))
done < "$assets_dir/soc-rules.ndjson"
[[ "$rule_count" == "$expected_rules" ]] ||
  fail "verified $rule_count rules, expected $expected_rules"

saved_object_lines=$(awk 'NF { count++ } END { print count+0 }' "$assets_dir/soc-saved-objects.ndjson" "$assets_dir/soc-investigation-saved-objects.ndjson" "$assets_dir/policy-engine-saved-objects.ndjson")
[[ "$saved_object_lines" == "$expected_saved_objects" ]] ||
  fail "local saved-object pack count drift"

write_conformance_evidence() {
  [[ -n "$evidence_output" ]] || return 0

  local output_dir temporary_output verified_at asset_set_sha256 manifest_sha256 verifier_sha256 action_binder_sha256 schema_sha256
  local node_count data_node_count
  local shift_checker_sha256 shift_report_schema_sha256
  output_dir=$(dirname -- "$evidence_output")
  [[ -d "$output_dir" && -w "$output_dir" ]] ||
    fail "OPENSHELL_EVIDENCE_OUTPUT parent must be an existing writable directory"
  [[ ! -e "$evidence_output" && ! -L "$evidence_output" ]] ||
    fail "OPENSHELL_EVIDENCE_OUTPUT already exists; evidence is append-only"

  temporary_output=$(mktemp "$output_dir/.openshell-elastic-soc-conformance.XXXXXX")
  verified_at=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
  asset_set_sha256=$(sha256_file "$script_dir/assets.sha256")
  manifest_sha256=$(sha256_file "$script_dir/manifest.json")
  verifier_sha256=$(sha256_file "$script_dir/verify.sh")
  action_binder_sha256=$(sha256_file "$script_dir/bind-actions.sh")
  schema_sha256=$(sha256_file "$script_dir/conformance.schema.json")
  node_count=$(jq -r .number_of_nodes <<<"$cluster_health")
  shift_checker_sha256=$(sha256_file "$script_dir/shift-check.sh")
  shift_report_schema_sha256=$(sha256_file "$script_dir/shift-report.schema.json")
  data_node_count=$(jq -r .number_of_data_nodes <<<"$cluster_health")

  if ! jq -n \
    --arg verified_at "$verified_at" \
    --arg pack_version "$(jq -r .pack_version <<<"$manifest")" \
    --arg asset_set_sha256 "$asset_set_sha256" \
    --arg manifest_sha256 "$manifest_sha256" \
    --arg verifier_sha256 "$verifier_sha256" \
    --arg action_binder_sha256 "$action_binder_sha256" \
    --arg schema_sha256 "$schema_sha256" \
    --arg environment "$environment" \
    --arg kibana_space "$kibana_space" \
    --arg shift_checker_sha256 "$shift_checker_sha256" \
    --arg shift_report_schema_sha256 "$shift_report_schema_sha256" \
    --arg elasticsearch_version "$expected_es_version" \
    --arg kibana_version "$expected_kibana_version" \
    --arg cluster_status "$(jq -r .status <<<"$cluster_health")" \
    --arg security_index "$security_index" \
    --arg trace_index "$trace_index" \
    --arg detection_principal "$detection_principal" \
    --arg snapshot_repository "$snapshot_repository" \
    --arg retention_approval "$retention_approval" \
    --arg security_lifecycle_policy "$security_lifecycle_policy" \
    --arg agent_lifecycle_policy "$agent_lifecycle_policy" \
    --argjson node_count "$node_count" \
    --argjson data_node_count "$data_node_count" \
    --argjson rule_count "$rule_count" \
    '{
      schema_version: "1.0",
      status: "passed",
      verified_at: $verified_at,
      integration: {
        name: "openshell-elastic-soc",
        pack_version: $pack_version,
        asset_set_sha256: $asset_set_sha256,
        manifest_sha256: $manifest_sha256,
        verifier_sha256: $verifier_sha256,
        action_binder_sha256: $action_binder_sha256,
        schema_sha256: $schema_sha256,
        shift_checker_sha256: $shift_checker_sha256,
        shift_report_schema_sha256: $shift_report_schema_sha256
      },
      target: {
        environment: $environment,
        kibana_space: $kibana_space,
        elasticsearch: {
          version: $elasticsearch_version,
          cluster_status: $cluster_status,
          node_count: $node_count,
          data_node_count: $data_node_count
        },
        kibana: {
          version: $kibana_version,
          status: "available"
        },
        indices: {
          security: $security_index,
          agent_traces: $trace_index
        }
      },
      controls: {
        templates_and_roles_match: true,
        soc_role_separation_verified: true,
        indices_green_with_replicas: true,
        runtime_identity_create_only: true,
        runtime_identity_admin_read_delete_denied: true,
        detections_enabled_and_healthy: true,
        rule_count: $rule_count,
        detection_principal: $detection_principal,
        snapshot_repository: $snapshot_repository,
        retention_approval: $retention_approval,
        lifecycle_management_running: true,
        indices_lifecycle_managed: true,
        security_lifecycle_policy: $security_lifecycle_policy,
        agent_lifecycle_policy: $agent_lifecycle_policy
      },
      external_gates: {
        status: "unexecuted",
        required: [
          "sso_and_analyst_access",
          "mtls_and_credential_rotation",
          "snapshot_restore",
          "retention_and_deletion",
          "outage_drain_and_capacity",
          "connector_delivery",
          "soc_shift_acceptance",
          "canary_and_multi_gateway_pilot"
        ]
      }
    }' >"$temporary_output"; then
    rm -f -- "$temporary_output"
    fail "could not serialize conformance evidence"
  fi

  chmod 0600 "$temporary_output"
  if ! ln "$temporary_output" "$evidence_output"; then
    rm -f -- "$temporary_output"
    fail "could not publish append-only conformance evidence"
  fi
  rm -f -- "$temporary_output"
  evidence_sha256=$(sha256_file "$evidence_output")
}

write_conformance_evidence
printf 'OpenShell Elastic SOC conformance passed\n'
printf '  environment: %s\n' "$environment"
printf '  Kibana space: %s\n' "$kibana_space"
printf '  Elasticsearch/Kibana: %s/%s\n' "$expected_es_version" "$expected_kibana_version"
printf '  topology: %s nodes, %s data nodes, green\n'   "$(jq -r .number_of_nodes <<<"$cluster_health")"   "$(jq -r .number_of_data_nodes <<<"$cluster_health")"
printf '  indices: %s, %s\n' "$security_index" "$trace_index"
printf '  detections: %s enabled rules owned by %s\n' "$rule_count" "$detection_principal"
printf '  SOC roles: viewer read-only; analyst triage; detection engineer rule management\n'
printf '  snapshot repository: %s\n' "$snapshot_repository"
printf '  retention approval: %s\n' "$retention_approval"
printf '  lifecycle policies: security=%s, agent=%s (ILM running and healthy)\n' \
  "$security_lifecycle_policy" "$agent_lifecycle_policy"
printf '  runtime ingest: positive create-only and negative admin/read/delete proof passed\n'
if [[ -n "$evidence_output" ]]; then
  printf '  evidence: %s\n' "$evidence_output"
  printf '  evidence sha256: %s\n' "$evidence_sha256"
fi
