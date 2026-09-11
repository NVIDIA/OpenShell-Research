#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -Eeuo pipefail
umask 077

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
assets_dir="$script_dir/assets"
mode=${1:-plan}
environment=${OPENSHELL_ELASTIC_ENVIRONMENT:-production}
kibana_space=${OPENSHELL_KIBANA_SPACE:-openshell-production}
security_index="logs-openshell.security-${environment}-v1"
trace_index="traces-openshell.agent-${environment}-v1"
role_suffix=${environment//-/_}
ingest_role="openshell_ingest_${role_suffix}"
viewer_role="openshell_soc_viewer_${role_suffix}"
analyst_role="openshell_soc_analyst_${role_suffix}"
detection_role="openshell_soc_detection_engineer_${role_suffix}"
work_dir=$(mktemp -d)
trap 'rm -rf "$work_dir"' EXIT

fail() {
  printf 'elastic-soc install: %s\n' "$*" >&2
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
verify_asset_hashes

validate_namespace() {
  [[ "$environment" =~ ^[a-z0-9][a-z0-9-]{0,31}$ ]] ||
    fail "OPENSHELL_ELASTIC_ENVIRONMENT must match ^[a-z0-9][a-z0-9-]{0,31}$"
  [[ "$kibana_space" =~ ^[a-z0-9][a-z0-9_-]{0,63}$ ]] ||
    fail "OPENSHELL_KIBANA_SPACE has an invalid identifier"
}

print_plan() {
  cat <<PLAN
OpenShell Elastic SOC pack plan
  pack:            $(jq -r .pack_version "$script_dir/manifest.json")
  environment:     $environment
  Kibana space:    $kibana_space
  security index:  $security_index
  trace index:     $trace_index
  ingest role:     $ingest_role
  viewer role:     $viewer_role
  analyst role:    $analyst_role
  detection role:  $detection_role

platform phase:
  - install two versioned component templates and two index templates
  - create the two concrete v1 indices without deleting or replacing evidence
  - leave lifecycle-policy creation and assignment to the customer platform owner
  - require two distinct healthy policies before production conformance
  - install environment-scoped runtime, viewer, analyst, and detection roles
  - install two data views, eleven Vega visualizations, seven searches, and six dashboards

rules phase:
  - initialize the Elastic Security alert index if needed
  - create or update eleven stable-ID, environment-scoped detection rules

operations phase:
  - run shift-check.sh with the analyst read identity at every shift boundary
  - retain each non-overwritable aggregate health report and its SHA-256

No identity, credential, index, rule, or saved object is deleted.
PLAN
}

validate_namespace
require_tool jq
case "$mode" in
  plan)
    print_plan
    exit 0
    ;;
  platform|rules|apply) ;;
  *)
    fail "usage: $0 {plan|platform|rules|apply}"
    ;;
esac

[[ -n "${OPENSHELL_CHANGE_TICKET:-}" ]] ||
  fail "OPENSHELL_CHANGE_TICKET is required for every mutation"
elastic_ca=${ELASTIC_CA_FILE:-}
[[ -r "$elastic_ca" && -f "$elastic_ca" ]] ||
  fail "ELASTIC_CA_FILE must be a readable CA file"
client_cert=${ELASTIC_CLIENT_CERT_FILE:-}
client_key=${ELASTIC_CLIENT_KEY_FILE:-}
if [[ -n "$client_cert" || -n "$client_key" ]]; then
  [[ -r "$client_cert" && -r "$client_key" ]] ||
    fail "ELASTIC_CLIENT_CERT_FILE and ELASTIC_CLIENT_KEY_FILE are required together"
fi

require_tool curl
require_tool cmp
curl_transport=(--silent --show-error --cacert "$elastic_ca")
if [[ -n "$client_cert" ]]; then
  curl_transport+=(--cert "$client_cert" --key "$client_key")
fi
curl_common=("${curl_transport[@]}" --fail-with-body)

elasticsearch_url=${ELASTICSEARCH_URL:-}
kibana_url=${KIBANA_URL:-}
if [[ "$mode" == platform || "$mode" == apply ]]; then
  require_https_url ELASTICSEARCH_URL "$elasticsearch_url"
  require_https_url KIBANA_URL "$kibana_url"
  es_bootstrap_header=${ELASTICSEARCH_BOOTSTRAP_AUTH_HEADER_FILE:-}
  kibana_bootstrap_header=${KIBANA_BOOTSTRAP_AUTH_HEADER_FILE:-}
  require_secret_header ELASTICSEARCH_BOOTSTRAP_AUTH_HEADER_FILE "$es_bootstrap_header"
  require_secret_header KIBANA_BOOTSTRAP_AUTH_HEADER_FILE "$kibana_bootstrap_header"
  cmp -s "$es_bootstrap_header" "$kibana_bootstrap_header" &&
    fail "Elasticsearch and Kibana bootstrap identities must be distinct"
fi
if [[ "$mode" == rules || "$mode" == apply ]]; then
  require_https_url KIBANA_URL "$kibana_url"
  kibana_detection_header=${KIBANA_DETECTION_AUTH_HEADER_FILE:-}
  require_secret_header KIBANA_DETECTION_AUTH_HEADER_FILE "$kibana_detection_header"
  if [[ "$mode" == apply ]]; then
    cmp -s "$kibana_detection_header" "$es_bootstrap_header" &&
      fail "detection and Elasticsearch bootstrap identities must be distinct"
    cmp -s "$kibana_detection_header" "$kibana_bootstrap_header" &&
      fail "detection and Kibana bootstrap identities must be distinct"
  fi
fi

kibana_prefix=
if [[ "$kibana_space" != default ]]; then
  kibana_prefix="/s/$kibana_space"
fi

es_bootstrap_curl() {
  curl "${curl_common[@]}" --header "@$es_bootstrap_header" "$@"
}

kibana_bootstrap_curl() {
  curl "${curl_common[@]}" --header "@$kibana_bootstrap_header" "$@"
}

kibana_detection_curl() {
  curl "${curl_common[@]}" --header "@$kibana_detection_header" "$@"
}

put_es_json() {
  local path=$1 file=$2
  printf '[elastic-soc] PUT Elasticsearch %s\n' "$path"
  es_bootstrap_curl --request PUT --header 'Content-Type: application/json'     --data-binary "@$file" "$elasticsearch_url$path" >/dev/null
}

ensure_index() {
  local index=$1 response="$work_dir/index-response.json" status
  status=$(curl "${curl_transport[@]}" --header "@$es_bootstrap_header"     --silent --show-error --output "$response" --write-out '%{http_code}'     --request PUT "$elasticsearch_url/$index")
  case "$status" in
    200) printf '[elastic-soc] created index %s\n' "$index" ;;
    400)
      jq -e '.error.type == "resource_already_exists_exception"' "$response" >/dev/null ||
        fail "index creation failed for $index with HTTP $status"
      printf '[elastic-soc] retained existing index %s\n' "$index"
      ;;
    *) fail "index creation failed for $index with HTTP $status" ;;
  esac
}

install_platform() {
  jq empty "$assets_dir/component-template.json" "$assets_dir/index-template.json"     "$assets_dir/trace-component-template.json" "$assets_dir/trace-index-template.json"     "$assets_dir/ingest-role.json" "$assets_dir/soc-viewer-role.json"     "$assets_dir/soc-analyst-role.json" "$assets_dir/soc-detection-engineer-role.json"

  es_bootstrap_curl "$elasticsearch_url/_security/_authenticate" >/dev/null
  kibana_bootstrap_curl "$kibana_url/api/status" >/dev/null

  put_es_json /_component_template/openshell-security-ecs-v1 "$assets_dir/component-template.json"
  put_es_json /_index_template/openshell-security-v1 "$assets_dir/index-template.json"
  put_es_json /_component_template/openshell-agent-trace-ecs-v1 "$assets_dir/trace-component-template.json"
  put_es_json /_index_template/openshell-agent-trace-v1 "$assets_dir/trace-index-template.json"

  jq --arg security "$security_index" --arg trace "$trace_index"     '.indices[0].names = [$security, $trace]'     "$assets_dir/ingest-role.json" > "$work_dir/ingest-role.json"
  put_es_json "/_security/role/$ingest_role" "$work_dir/ingest-role.json"

  ensure_index "$security_index"
  ensure_index "$trace_index"

  for role_spec in     "$viewer_role:$assets_dir/soc-viewer-role.json"     "$analyst_role:$assets_dir/soc-analyst-role.json"     "$detection_role:$assets_dir/soc-detection-engineer-role.json"
  do
    role_name=${role_spec%%:*}
    role_file=${role_spec#*:}
    jq --arg security "$security_index" --arg trace "$trace_index" --arg space "$kibana_space"       '.elasticsearch.indices[0].names = [$security, $trace] | .kibana[].spaces = [$space]'       "$role_file" > "$work_dir/$role_name.json"
    printf '[elastic-soc] PUT Kibana role %s\n' "$role_name"
    kibana_bootstrap_curl --request PUT --header 'kbn-xsrf: openshell-soc'       --header 'Content-Type: application/json' --data-binary "@$work_dir/$role_name.json"       "$kibana_url/api/security/role/$role_name" >/dev/null
  done

  for view in     "openshell-security:OpenShell Security Evidence:$security_index"     "openshell-agent-traces:OpenShell Agent Traces (Privacy Filtered):$trace_index"
  do
    view_id=${view%%:*}
    view_rest=${view#*:}
    view_name=${view_rest%%:*}
    view_title=${view_rest#*:}
    jq -n --arg id "$view_id" --arg name "$view_name" --arg title "$view_title"       '{data_view:{id:$id,name:$name,title:$title,timeFieldName:"@timestamp",allowNoIndex:true},override:true}'       > "$work_dir/data-view.json"
    kibana_bootstrap_curl --request POST --header 'kbn-xsrf: openshell-soc'       --header 'Content-Type: application/json' --data-binary "@$work_dir/data-view.json"       "$kibana_url$kibana_prefix/api/data_views/data_view" >/dev/null
  done

  : > "$work_dir/saved-objects.ndjson"
  while IFS= read -r object; do
    [[ -n "$object" ]] || continue
    jq -c --arg security "$security_index" --arg trace "$trace_index" '
      if .type == "index-pattern" and .id == "openshell-security" then
        .attributes.title = $security
      elif .type == "index-pattern" and .id == "openshell-agent-traces" then
        .attributes.title = $trace
      else . end
    ' <<<"$object" >> "$work_dir/saved-objects.ndjson"
  done < <(cat "$assets_dir/soc-saved-objects.ndjson" "$assets_dir/soc-investigation-saved-objects.ndjson" "$assets_dir/policy-engine-saved-objects.ndjson")

  kibana_bootstrap_curl --request POST --header 'kbn-xsrf: openshell-soc'     --form "file=@$work_dir/saved-objects.ndjson"     "$kibana_url$kibana_prefix/api/saved_objects/_import?overwrite=true"     > "$work_dir/saved-object-result.json"
  jq -e '.success == true and .successCount == 26' "$work_dir/saved-object-result.json" >/dev/null ||
    fail "Kibana saved-object import did not return the reviewed count"

  printf '[elastic-soc] platform phase complete for change %s\n' "$OPENSHELL_CHANGE_TICKET"
  printf '[elastic-soc] assign role %s to the dedicated detection identity before the rules phase\n' "$detection_role"
}

install_rules() {
  local status rule rule_id existing_status rule_method rule_count=0
  kibana_detection_curl "$kibana_url/api/status" >/dev/null
  status=$(curl "${curl_transport[@]}" --header "@$kibana_detection_header"     --silent --show-error --output "$work_dir/detection-index.json" --write-out '%{http_code}'     --request POST --header 'kbn-xsrf: openshell-soc'     "$kibana_url$kibana_prefix/api/detection_engine/index")
  case "$status" in
    200|409) ;;
    *) fail "Elastic Security alert index initialization failed with HTTP $status" ;;
  esac

  while IFS= read -r rule; do
    [[ -n "$rule" ]] || continue
    rule=$(jq -c --arg security "$security_index" --arg trace "$trace_index" --arg environment "$environment" '
      .index |= map(
        if startswith("logs-openshell.security-") then $security
        elif startswith("traces-openshell.agent-") then $trace
        else . end
      )
      | .tags = ((.tags + ["Environment: " + $environment]) | unique)
    ' <<<"$rule")
    rule_id=$(jq -r .rule_id <<<"$rule")
    [[ "$rule_id" =~ ^[a-z0-9-]+$ ]] || fail "rule has an unsafe stable ID"

    existing_status=$(curl "${curl_transport[@]}" --header "@$kibana_detection_header"       --silent --show-error --output "$work_dir/rule-lookup.json" --write-out '%{http_code}'       "$kibana_url$kibana_prefix/api/detection_engine/rules?rule_id=$rule_id")
    case "$existing_status" in
      200) rule_method=PUT ;;
      404) rule_method=POST ;;
      *) fail "rule lookup failed for $rule_id with HTTP $existing_status" ;;
    esac
    kibana_detection_curl --request "$rule_method" --header 'kbn-xsrf: openshell-soc'       --header 'Content-Type: application/json' --data "$rule"       "$kibana_url$kibana_prefix/api/detection_engine/rules" >/dev/null
    rule_count=$((rule_count + 1))
  done < "$assets_dir/soc-rules.ndjson"

  [[ "$rule_count" == 11 ]] || fail "installed $rule_count rules, expected 11"
  printf '[elastic-soc] rules phase complete: %s environment-scoped rules for change %s\n'     "$rule_count" "$OPENSHELL_CHANGE_TICKET"
}

case "$mode" in
  platform) install_platform ;;
  rules) install_rules ;;
  apply)
    install_platform
    install_rules
    ;;
esac
