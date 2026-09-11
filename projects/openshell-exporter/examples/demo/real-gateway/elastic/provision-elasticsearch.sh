#!/bin/sh
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -eu

: "${ELASTIC_PASSWORD:?ELASTIC_PASSWORD is required}"
: "${ELASTIC_INGEST_PASSWORD:?ELASTIC_INGEST_PASSWORD is required}"
: "${ELASTIC_OTLP_PASSWORD:?ELASTIC_OTLP_PASSWORD is required}"
: "${KIBANA_SYSTEM_PASSWORD:?KIBANA_SYSTEM_PASSWORD is required}"

elasticsearch=https://elasticsearch:9200
elastic_curl() {
  curl --cacert /certs/ca.crt "$@"
}
admin_auth="elastic:${ELASTIC_PASSWORD}"

put_json() {
  path=$1
  file=$2
  echo "[elastic-init] PUT $path"
  elastic_curl --fail --silent --show-error     --user "$admin_auth"     --request PUT     --header 'Content-Type: application/json'     --data-binary "@$file"     "$elasticsearch$path" >/dev/null
}

ensure_index() {
  index=$1
  response=/tmp/index-response.json
  echo "[elastic-init] ensure $index"
  status=$(elastic_curl --silent --output "$response" --write-out '%{http_code}'     --user "$admin_auth" --request PUT "$elasticsearch/$index")
  case "$status" in
    200) ;;
    400)
      grep -q 'resource_already_exists_exception' "$response" || {
        cat "$response" >&2
        exit 1
      }
      ;;
    *)
      cat "$response" >&2
      echo "$index setup returned HTTP $status" >&2
      exit 1
      ;;
  esac
}

echo "[elastic-init] configure kibana_system password"
elastic_curl --fail --silent --show-error   --user "$admin_auth"   --request POST   --header 'Content-Type: application/json'   --data "{\"password\":\"${KIBANA_SYSTEM_PASSWORD}\"}"   "$elasticsearch/_security/user/kibana_system/_password" >/dev/null

# Keep v1 assets and indices for non-destructive migration. New CloudEvents are
# represented by security mapping v2; Relay uses Elastic's native OTLP mappings.
put_json /_component_template/openshell-security-ecs-v1 /component-template.json
put_json /_index_template/openshell-security-v1 /index-template.json
put_json /_component_template/openshell-agent-trace-ecs-v1 /trace-component-template.json
put_json /_index_template/openshell-agent-trace-v1 /trace-index-template.json
put_json /_component_template/openshell-security-ecs-v2 /component-template-v2.json
put_json /_index_template/openshell-security-v2 /index-template-v2.json
put_json /_ingest/pipeline/openshell-relay-apm-normalize-v1 /apm-ingest-pipeline.json
# Elastic's managed APM data streams invoke this stable customization hook
# before their built-in final pipeline. The OpenShell pipeline is additive and
# leaves every Elastic-native APM field intact.
put_json /_ingest/pipeline/traces-apm@custom /apm-ingest-pipeline.json
put_json /_security/role/openshell_ingest /ingest-role.json
put_json /_security/role/openshell_otlp /otlp-ingest-role.json

echo "[elastic-init] configure distinct runtime ingest users"
elastic_curl --fail --silent --show-error   --user "$admin_auth" --request PUT --header 'Content-Type: application/json'   --data '{"password":"'"${ELASTIC_INGEST_PASSWORD}"'","roles":["openshell_ingest"],"full_name":"OpenShell Logstash security evidence ingest","enabled":true,"metadata":{"managed_by":"openshell-event-exporter-reference","lane":"cloudevents"}}'   "$elasticsearch/_security/user/openshell_ingest" >/dev/null
elastic_curl --fail --silent --show-error   --user "$admin_auth" --request PUT --header 'Content-Type: application/json'   --data '{"password":"'"${ELASTIC_OTLP_PASSWORD}"'","roles":["openshell_otlp"],"full_name":"OpenShell native OTLP trace ingest","enabled":true,"metadata":{"managed_by":"openshell-event-exporter-reference","lane":"otlp"}}'   "$elasticsearch/_security/user/openshell_otlp" >/dev/null

ensure_index logs-openshell.security-default-v1
ensure_index traces-openshell.agent-default-v1
ensure_index logs-openshell.security-default-v2

echo "[elastic-init] verify least-privilege lane separation"
security_privileges=$(elastic_curl --fail --silent --show-error   --user "openshell_ingest:${ELASTIC_INGEST_PASSWORD}"   --request POST --header 'Content-Type: application/json'   --data '{"cluster":["monitor"],"index":[{"names":["logs-openshell.security-default-v2"],"privileges":["create_doc","view_index_metadata"]},{"names":["traces-apm-default","logs-apm.error-default","metrics-apm.service_summary.1m-default"],"privileges":["create_doc"]}]}'   "$elasticsearch/_security/user/_has_privileges")
printf '%s' "$security_privileges" | grep -q '"has_all_requested":false' || {
  echo "Logstash identity unexpectedly has native OTLP privileges" >&2
  exit 1
}
printf '%s' "$security_privileges" | grep -q '"create_doc":true' || {
  echo "Logstash identity lacks security create_doc" >&2
  exit 1
}

otlp_privileges=$(elastic_curl --fail --silent --show-error   --user "openshell_otlp:${ELASTIC_OTLP_PASSWORD}"   --request POST --header 'Content-Type: application/json'   --data '{"cluster":["monitor"],"index":[{"names":["traces-apm-default","logs-apm.error-default","metrics-apm.service_summary.1m-default"],"privileges":["auto_configure","create_doc","view_index_metadata"]},{"names":["logs-openshell.security-default-v2"],"privileges":["create_doc"]}]}'   "$elasticsearch/_security/user/_has_privileges")
printf '%s' "$otlp_privileges" | grep -q '"has_all_requested":false' || {
  echo "OTLP identity unexpectedly has CloudEvents security privileges" >&2
  exit 1
}
printf '%s' "$otlp_privileges" | grep -q '"auto_configure":true' || {
  echo "OTLP identity lacks native trace auto_configure" >&2
  exit 1
}

echo "Elasticsearch security representation v2 and native OTLP lane identities are ready"
