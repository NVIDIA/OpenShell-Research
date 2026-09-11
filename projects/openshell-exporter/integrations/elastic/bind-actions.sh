#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -Eeuo pipefail
umask 077

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
mode=${1:-plan}
environment=${OPENSHELL_ELASTIC_ENVIRONMENT:-production}
kibana_space=${OPENSHELL_KIBANA_SPACE:-openshell-production}
connector_id=${OPENSHELL_SOC_WEBHOOK_CONNECTOR_ID:-}
body_template="$script_dir/assets/soc-webhook-body.json"
rule_ids=(
  openshell-validation-failure-v1
  openshell-source-gap-v1
  openshell-policy-denial-burst-v1
  openshell-agent-policy-denial-sequence-v1
  openshell-policy-denial-destination-probe-v1
  openshell-denial-source-gap-sequence-v1
)
work_dir=$(mktemp -d)
trap 'rm -rf "$work_dir"' EXIT

fail() {
  printf 'elastic-soc actions: %s\n' "$*" >&2
  exit 1
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
  (( (8#$mode_value & 077) == 0 )) || fail "$name must not be group/world accessible"
}

if command -v sha256sum >/dev/null 2>&1; then
  (cd "$script_dir" && sha256sum --check --quiet assets.sha256) || fail "reviewed Elastic asset checksum mismatch"
elif command -v shasum >/dev/null 2>&1; then
  (cd "$script_dir" && shasum -a 256 --check assets.sha256 >/dev/null) || fail "reviewed Elastic asset checksum mismatch"
else
  fail "sha256sum or shasum is required"
fi

[[ "$environment" =~ ^[a-z0-9][a-z0-9-]{0,31}$ ]] ||
  fail "OPENSHELL_ELASTIC_ENVIRONMENT has an invalid identifier"
[[ "$kibana_space" =~ ^[a-z0-9][a-z0-9_-]{0,63}$ ]] ||
  fail "OPENSHELL_KIBANA_SPACE has an invalid identifier"

case "$mode" in
  plan)
    printf 'OpenShell Elastic SOC action-binding plan\n'
    printf '  environment: %s\n  Kibana space: %s\n' "$environment" "$kibana_space"
    printf '  connector: existing customer-managed .webhook connector %s\n' "${connector_id:-<required-for-apply>}"
    printf '  notification: one privacy-minimal notification for each new alert\n'
    printf '  rules:\n'
    printf '    - %s\n' "${rule_ids[@]}"
    printf 'No connector or credential is created, updated, exported, or deleted.\n'
    exit 0
    ;;
  apply|verify) ;;
  *) fail "usage: $0 {plan|apply|verify}" ;;
esac

[[ "${OPENSHELL_CHANGE_TICKET:-}" =~ ^[A-Za-z0-9._/-]{1,64}$ ]] || fail "OPENSHELL_CHANGE_TICKET has an invalid identifier"
[[ "$connector_id" =~ ^[a-z0-9][a-z0-9-]{0,35}$ ]] || fail "OPENSHELL_SOC_WEBHOOK_CONNECTOR_ID has an invalid identifier"
command -v curl >/dev/null || fail "curl is required"
command -v jq >/dev/null || fail "jq is required"
jq -e '
  .schema_version == "1.0" and .producer == "elastic-security" and
  .original == null and .prompt == null and .response == null and
  .tool_arguments == null and .credentials == null
' "$body_template" >/dev/null || fail "reviewed webhook body is invalid"

kibana_url=${KIBANA_URL:-}
ca_file=${ELASTIC_CA_FILE:-}
auth_header=${KIBANA_DETECTION_AUTH_HEADER_FILE:-}
require_https_url KIBANA_URL "$kibana_url"
[[ -r "$ca_file" && -f "$ca_file" ]] || fail "ELASTIC_CA_FILE must be a readable CA file"
require_secret_header KIBANA_DETECTION_AUTH_HEADER_FILE "$auth_header"
client_cert=${ELASTIC_CLIENT_CERT_FILE:-}
client_key=${ELASTIC_CLIENT_KEY_FILE:-}
if [[ -n "$client_cert" || -n "$client_key" ]]; then
  [[ -r "$client_cert" && -r "$client_key" ]] ||
    fail "ELASTIC_CLIENT_CERT_FILE and ELASTIC_CLIENT_KEY_FILE are required together"
fi

curl_args=(--fail-with-body --silent --show-error --cacert "$ca_file" --header "@$auth_header")
if [[ -n "$client_cert" ]]; then
  curl_args+=(--cert "$client_cert" --key "$client_key")
fi
kibana_prefix=
[[ "$kibana_space" == default ]] || kibana_prefix="/s/$kibana_space"

connector=$(curl "${curl_args[@]}" "$kibana_url$kibana_prefix/api/actions/connector/$connector_id")
jq -e --arg id "$connector_id" '
  .id == $id and .connector_type_id == ".webhook" and
  (.is_deprecated // false) == false and (.is_missing_secrets // false) == false
' <<<"$connector" >/dev/null || fail "connector must be an enabled .webhook connector with configured secrets"

body=$(jq -c --arg environment "$environment" '.environment = $environment' "$body_template")
action=$(jq -cn --arg id "$connector_id" --arg body "$body" '{
  action_type_id:".webhook",
  group:"default",
  id:$id,
  params:{body:$body},
  frequency:{summary:false,notifyWhen:"onActiveAlert",throttle:null}
}')

verify_rule() {
  local rule_id=$1 rule=$2
  jq -e --arg id "$connector_id" --arg body "$body" '
    .actions | length == 1 and
    .[0].action_type_id == ".webhook" and
    .[0].group == "default" and .[0].id == $id and .[0].params.body == $body and
    .[0].frequency.summary == false and
    .[0].frequency.notifyWhen == "onActiveAlert" and
    .[0].frequency.throttle == null
  ' <<<"$rule" >/dev/null || return 1
}

for rule_id in "${rule_ids[@]}"; do
  rule=$(curl "${curl_args[@]}" "$kibana_url$kibana_prefix/api/detection_engine/rules?rule_id=$rule_id")
  if [[ "$mode" == verify ]]; then
    verify_rule "$rule_id" "$rule" || fail "rule action does not match reviewed contract: $rule_id"
    printf '[elastic-soc] verified privacy-safe webhook action: %s\n' "$rule_id"
    continue
  fi
  if [[ $(jq '.actions | length' <<<"$rule") -gt 0 ]]; then
    verify_rule "$rule_id" "$rule" ||
      fail "rule already has a different action; refusing replacement: $rule_id"
    printf '[elastic-soc] retained matching webhook action: %s\n' "$rule_id"
    continue
  fi
  payload=$(jq -cn --arg rule_id "$rule_id" --argjson action "$action" '{rule_id:$rule_id,actions:[$action]}')
  updated=$(curl "${curl_args[@]}" --request PATCH --header 'kbn-xsrf: openshell-soc' \
    --header 'Content-Type: application/json' --data "$payload" \
    "$kibana_url$kibana_prefix/api/detection_engine/rules")
  verify_rule "$rule_id" "$updated" || fail "Kibana did not retain the reviewed action: $rule_id"
  printf '[elastic-soc] bound privacy-safe webhook action: %s\n' "$rule_id"
done

printf '[elastic-soc] %s complete for change %s; connector secrets remained customer-managed\n' "$mode" "$OPENSHELL_CHANGE_TICKET"
