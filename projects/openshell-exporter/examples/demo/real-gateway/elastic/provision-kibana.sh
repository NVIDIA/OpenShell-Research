#!/bin/sh
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -eu

kibana='https://kibana:5601'
secure_curl() {
  curl --cacert /certs/ca.crt "$@"
}
auth="elastic:${ELASTIC_PASSWORD}"
elasticsearch="https://elasticsearch:9200"
detection_password_file=/run/secrets/elastic-detection-password
[ -s "$detection_password_file" ] || { echo "OpenShell detection-service password is missing" >&2; exit 1; }
detection_password=$(cat "$detection_password_file")
printf '%s' "$detection_password" | grep -Eq '^[0-9a-f]{64}$' || { echo "OpenShell detection-service password has an invalid format" >&2; exit 1; }
detection_auth="openshell_detection_service:${detection_password}"

attempts=90
until secure_curl --fail --silent --user "$auth" "$kibana/api/status" >/dev/null; do
  attempts=$((attempts - 1))
  [ "$attempts" -gt 0 ] || { echo "Kibana did not become ready" >&2; exit 1; }
  sleep 2
done


for role in \
  openshell_soc_viewer:/soc-viewer-role.json \
  openshell_soc_analyst:/soc-analyst-role.json \
  openshell_soc_detection_engineer:/soc-detection-engineer-role.json
do
  role_name=${role%%:*}
  role_file=${role#*:}
  [ -s "$role_file" ] || { echo "Kibana role definition is missing: $role_file" >&2; exit 1; }
  transformed_role="/tmp/$(basename "$role_file")"
  sed -e 's/logs-openshell\.security-\*-v1/logs-openshell.security-*-v2/g' -e 's/traces-openshell\.agent-\*-v1/traces-apm*/g' "$role_file" >"$transformed_role"
  secure_curl --fail --silent --show-error \
    --user "$auth" \
    --request PUT \
    --header 'kbn-xsrf: openshell-soc' \
    --header 'Content-Type: application/json' \
    --data-binary "@$transformed_role" \
    "$kibana/api/security/role/$role_name" >/dev/null
done

echo "[kibana-setup] configure least-privilege detection service identity"
secure_curl --fail --silent --show-error \
  --user "$auth" --request PUT --header 'Content-Type: application/json' \
  --data "{\"password\":\"${detection_password}\",\"roles\":[\"openshell_soc_detection_engineer\"],\"full_name\":\"OpenShell SOC detection service\",\"enabled\":true,\"metadata\":{\"managed_by\":\"openshell-event-exporter-reference\"}}" \
  "$elasticsearch/_security/user/openshell_detection_service" >/dev/null
secure_curl --fail --silent --show-error \
  --user "$auth" \
  --request POST \
  --header 'kbn-xsrf: openshell-demo' \
  --header 'Content-Type: application/json' \
  --data '{"data_view":{"id":"openshell-security","name":"OpenShell Security Evidence","title":"logs-openshell.security-*-v2","timeFieldName":"@timestamp","allowNoIndex":true},"override":true}' \
  "$kibana/api/data_views/data_view" >/dev/null
secure_curl --fail --silent --show-error \
  --user "$auth" \
  --request POST \
  --header 'kbn-xsrf: openshell-demo' \
  --header 'Content-Type: application/json' \
  --data '{"data_view":{"id":"openshell-agent-traces","name":"OpenShell Agent Traces (Privacy Filtered)","title":"traces-apm*","timeFieldName":"@timestamp","allowNoIndex":true},"override":true}' \
  "$kibana/api/data_views/data_view" >/dev/null


saved_objects=/soc-saved-objects.ndjson
investigation_saved_objects=/soc-investigation-saved-objects.ndjson
policy_engine_saved_objects=/policy-engine-saved-objects.ndjson
[ -s "$saved_objects" ] || { echo "OpenShell SOC saved-object bundle is missing" >&2; exit 1; }
[ -s "$investigation_saved_objects" ] || { echo "OpenShell SOC investigation bundle is missing" >&2; exit 1; }
[ -s "$policy_engine_saved_objects" ] || { echo "OpenShell policy-engine workbench bundle is missing" >&2; exit 1; }
cat "$saved_objects" "$investigation_saved_objects" "$policy_engine_saved_objects" > /tmp/openshell-soc-all-saved-objects.ndjson
sed -e 's/logs-openshell\.security-\*-v1/logs-openshell.security-*-v2/g' -e 's/traces-openshell\.agent-\*-v1/traces-apm*/g' /tmp/openshell-soc-all-saved-objects.ndjson >/tmp/openshell-soc-all-saved-objects.v2.ndjson
mv /tmp/openshell-soc-all-saved-objects.v2.ndjson /tmp/openshell-soc-all-saved-objects.ndjson
saved_objects=/tmp/openshell-soc-all-saved-objects.ndjson
secure_curl --fail --silent --show-error \
  --user "$auth" \
  --request POST \
  --header 'kbn-xsrf: openshell-soc' \
  --form "file=@$saved_objects" \
  "$kibana/api/saved_objects/_import?overwrite=true" \
  >/tmp/saved-object-import.json
grep -q '"success":true' /tmp/saved-object-import.json || {
  cat /tmp/saved-object-import.json >&2
  echo "OpenShell SOC saved-object import failed" >&2
  exit 1
}
grep -q '"successCount":26' /tmp/saved-object-import.json || {
  cat /tmp/saved-object-import.json >&2
  echo "OpenShell SOC saved-object import count is incorrect" >&2
  exit 1
}

# Initialize the Elastic Security alert index. A conflict means it already exists.
status=$(secure_curl --silent --output /tmp/detection-index.json --write-out '%{http_code}' \
  --user "$detection_auth" \
  --request POST \
  --header 'kbn-xsrf: openshell-demo' \
  "$kibana/api/detection_engine/index")
case "$status" in
  200|409) ;;
  *) cat /tmp/detection-index.json >&2; echo "detection index setup returned HTTP $status" >&2; exit 1 ;;
esac

rules_file=/soc-rules.ndjson
[ -s "$rules_file" ] || { echo "OpenShell SOC rule pack is missing" >&2; exit 1; }
rules_source=$rules_file
rules_file=/tmp/openshell-soc-rules.ndjson
sed -e 's/logs-openshell\.security-\*-v1/logs-openshell.security-*-v2/g' -e 's/traces-openshell\.agent-\*-v1/traces-apm*/g' "$rules_source" >"$rules_file"
rule_count=0
while IFS= read -r rule; do
  [ -n "$rule" ] || continue
  rule_id=$(printf '%s\n' "$rule" | sed -n 's/.*"rule_id":"\([^"]*\)".*/\1/p')
  [ -n "$rule_id" ] || { echo "SOC rule has no rule_id" >&2; exit 1; }

  existing_status=$(secure_curl --silent --output /tmp/openshell-rule-lookup.json --write-out '%{http_code}' \
    --user "$detection_auth" \
    "$kibana/api/detection_engine/rules?rule_id=$rule_id")
  case "$existing_status" in
    200) rule_method=PUT ;;
    404) rule_method=POST ;;
    *)
      cat /tmp/openshell-rule-lookup.json >&2
      echo "SOC rule lookup returned HTTP $existing_status: $rule_id" >&2
      exit 1
      ;;
  esac

  secure_curl --fail --silent --show-error \
    --user "$detection_auth" \
    --request "$rule_method" \
    --header 'kbn-xsrf: openshell-demo' \
    --header 'Content-Type: application/json' \
    --data "$rule" \
    "$kibana/api/detection_engine/rules" >/dev/null
  rule_count=$((rule_count + 1))
done < "$rules_file"
[ "$rule_count" -gt 0 ] || { echo "OpenShell SOC rule pack is empty" >&2; exit 1; }

notification_index=openshell-soc-notifications-default-v1
notification_role=openshell_soc_local_notification_writer
notification_index_payload='{"settings":{"number_of_shards":1,"number_of_replicas":0,"auto_expand_replicas":"0-1"},"mappings":{"dynamic":"strict","properties":{"schema_version":{"type":"keyword"},"producer":{"type":"keyword"},"environment":{"type":"keyword"},"occurred_at":{"type":"date"},"indexed_at":{"type":"date"},"rule":{"properties":{"id":{"type":"keyword"},"name":{"type":"keyword"},"severity":{"type":"keyword"},"space_id":{"type":"keyword"}}},"alert":{"properties":{"id":{"type":"keyword"}}},"url":{"type":"keyword","index":false}}}}'
notification_status=$(secure_curl --silent --output /tmp/openshell-notification-index.json --write-out '%{http_code}' \
  --user "$auth" --request PUT --header 'Content-Type: application/json' \
  --data "$notification_index_payload" "$elasticsearch/$notification_index")
case "$notification_status" in
  200) ;;
  400) grep -q 'resource_already_exists_exception' /tmp/openshell-notification-index.json || { cat /tmp/openshell-notification-index.json >&2; exit 1; } ;;
  *) cat /tmp/openshell-notification-index.json >&2; echo "SOC notification index returned HTTP $notification_status" >&2; exit 1 ;;
esac
secure_curl --fail --silent --show-error \
  --user "$auth" --request PUT --header 'Content-Type: application/json' \
  --data "{\"indices\":[{\"names\":[\"$notification_index\"],\"privileges\":[\"create_doc\",\"view_index_metadata\"],\"allow_restricted_indices\":false}]}" \
  "$elasticsearch/_security/role/$notification_role" >/dev/null
secure_curl --fail --silent --show-error \
  --user "$auth" --request PUT --header 'Content-Type: application/json' \
  --data "{\"password\":\"${detection_password}\",\"roles\":[\"openshell_soc_detection_engineer\",\"$notification_role\"],\"full_name\":\"OpenShell SOC detection service\",\"enabled\":true,\"metadata\":{\"managed_by\":\"openshell-event-exporter-reference\"}}" \
  "$elasticsearch/_security/user/openshell_detection_service" >/dev/null

connector_id=openshell-soc-case-index-v1
connector_create_payload="{\"name\":\"OpenShell SOC notification audit (local fixture)\",\"connector_type_id\":\".index\",\"config\":{\"index\":\"$notification_index\",\"refresh\":true,\"executionTimeField\":\"indexed_at\"},\"secrets\":{}}"
connector_update_payload="{\"name\":\"OpenShell SOC notification audit (local fixture)\",\"config\":{\"index\":\"$notification_index\",\"refresh\":true,\"executionTimeField\":\"indexed_at\"},\"secrets\":{}}"
connector_status=$(secure_curl --silent --output /tmp/openshell-connector.json --write-out '%{http_code}' \
  --user "$auth" "$kibana/api/actions/connector/$connector_id")
case "$connector_status" in
  200) connector_method=PUT; connector_payload=$connector_update_payload ;;
  404) connector_method=POST; connector_payload=$connector_create_payload ;;
  *) cat /tmp/openshell-connector.json >&2; echo "SOC connector lookup returned HTTP $connector_status" >&2; exit 1 ;;
esac
secure_curl --fail --silent --show-error \
  --user "$auth" --request "$connector_method" \
  --header 'kbn-xsrf: openshell-soc' --header 'Content-Type: application/json' \
  --data "$connector_payload" "$kibana/api/actions/connector/$connector_id" >/dev/null

[ -s /soc-webhook-body.json ] || { echo "reviewed SOC notification body is missing" >&2; exit 1; }
notification_document=$(sed 's/"environment": "production"/"environment": "default"/' /soc-webhook-body.json | tr -d '\n')
action="{\"action_type_id\":\".index\",\"group\":\"default\",\"id\":\"$connector_id\",\"params\":{\"documents\":[$notification_document]},\"frequency\":{\"summary\":false,\"notifyWhen\":\"onActiveAlert\",\"throttle\":null}}"
for rule_id in \
  openshell-validation-failure-v1 \
  openshell-source-gap-v1 \
  openshell-policy-denial-burst-v1 \
  openshell-agent-policy-denial-sequence-v1 \
  openshell-policy-denial-destination-probe-v1 \
  openshell-denial-source-gap-sequence-v1
do
  secure_curl --fail --silent --show-error \
    --user "$detection_auth" --request PATCH \
    --header 'kbn-xsrf: openshell-soc' --header 'Content-Type: application/json' \
    --data "{\"rule_id\":\"$rule_id\",\"actions\":[$action]}" \
    "$kibana/api/detection_engine/rules" >/dev/null
done

echo "Kibana evidence and privacy-filtered trace data views, 6 SOC dashboards with 11 live visualizations and 7 saved searches, 3 separated SOC roles, $rule_count OpenShell detection rules, and one Basic-license local SOC notification audit lane were created or updated without destructive replacement"
