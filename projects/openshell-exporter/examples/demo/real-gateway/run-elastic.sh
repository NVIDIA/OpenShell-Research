#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail
umask 077

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
cd "$SCRIPT_DIR"

# First prove the unchanged real gateway, sandbox, model, OCSF, WatchSandbox,
# Relay, conformance receiver, queues, and recovery path.
echo "[1/5] Running the real OpenShell, Hermes, and NeMo Relay demo..."
case "${DEMO_REUSE_VERIFIED_BASE:-false}" in
  true)
    echo "Reusing the existing real task after re-running its complete evidence gate..."
    ./verify.sh
    ;;
  false)
    DEMO_EXTERNAL_DESTINATIONS=false DEMO_SUPPRESS_CONFORMANCE_UI=true ./run.sh
    ;;
  *)
    echo "DEMO_REUSE_VERIFIED_BASE must be true or false" >&2
    exit 1
    ;;
esac
echo "[1/5] Base evidence and correlation are verified."

BASE_ENV="$SCRIPT_DIR/runtime/demo.env"
ELASTIC_ENV="$SCRIPT_DIR/runtime/elastic.env"
TLS_DIR="$SCRIPT_DIR/runtime/tls"
# shellcheck disable=SC1090
source "$BASE_ENV"

if [ -f "$ELASTIC_ENV" ]; then
  # Generated here, mode 0600, and ignored by Git.
  # shellcheck disable=SC1090
  source "$ELASTIC_ENV"
else
  ELASTIC_PASSWORD=$(openssl rand -hex 32)
  KIBANA_SYSTEM_PASSWORD=kibana-$(openssl rand -hex 32)
  KIBANA_SECURITY_KEY=kibana-$(openssl rand -hex 32)
  KIBANA_SAVED_OBJECTS_KEY=kibana-$(openssl rand -hex 32)
  KIBANA_REPORTING_KEY=kibana-$(openssl rand -hex 32)
fi

# Loading a prior Elastic runtime file also loads its old candidate metadata.
# Recompute the authoritative build identity only after that file is sourced.
DEMO_REPOSITORY_COMMIT=$(git -C "$SCRIPT_DIR" rev-parse HEAD)
DEMO_EXPORTER_VERSION=$("$SCRIPT_DIR/../../../scripts/check-version.sh")
DEMO_BUILD_CREATED=$(git -C "$SCRIPT_DIR" show -s --format=%cI HEAD)

ELASTIC_INGEST_PASSWORD=${ELASTIC_INGEST_PASSWORD:-$(openssl rand -hex 32)}
ELASTIC_OTLP_PASSWORD=${ELASTIC_OTLP_PASSWORD:-$(openssl rand -hex 32)}
ELASTIC_APM_SECRET_TOKEN=${ELASTIC_APM_SECRET_TOKEN:-$(openssl rand -hex 32)}
[ "$ELASTIC_OTLP_PASSWORD" != "$ELASTIC_INGEST_PASSWORD" ] || { echo "CloudEvents and OTLP credentials must be distinct" >&2; exit 1; }
DETECTION_PASSWORD_FILE="$TLS_DIR/elastic-detection-password"
if [ ! -s "$DETECTION_PASSWORD_FILE" ]; then
  openssl rand -hex 32 >"$DETECTION_PASSWORD_FILE"
fi

{
  awk -F= '
    $1 != "DEMO_REPOSITORY_COMMIT" &&
    $1 != "DEMO_EXPORTER_VERSION" &&
    $1 != "DEMO_BUILD_CREATED"
  ' "$BASE_ENV"
  printf 'DEMO_REPOSITORY_COMMIT=%s\n' "$DEMO_REPOSITORY_COMMIT"
  printf 'DEMO_EXPORTER_VERSION=%s\n' "$DEMO_EXPORTER_VERSION"
  printf 'DEMO_BUILD_CREATED=%s\n' "$DEMO_BUILD_CREATED"
  printf 'ELASTIC_PASSWORD=%s\n' "$ELASTIC_PASSWORD"
  printf 'ELASTIC_INGEST_PASSWORD=%s\n' "$ELASTIC_INGEST_PASSWORD"
  printf 'ELASTIC_OTLP_PASSWORD=%s\n' "$ELASTIC_OTLP_PASSWORD"
  printf 'ELASTIC_APM_SECRET_TOKEN=%s\n' "$ELASTIC_APM_SECRET_TOKEN"
  printf 'KIBANA_SYSTEM_PASSWORD=%s\n' "$KIBANA_SYSTEM_PASSWORD"
  printf 'KIBANA_SECURITY_KEY=%s\n' "$KIBANA_SECURITY_KEY"
  printf 'KIBANA_SAVED_OBJECTS_KEY=%s\n' "$KIBANA_SAVED_OBJECTS_KEY"
  printf 'KIBANA_REPORTING_KEY=%s\n' "$KIBANA_REPORTING_KEY"
} >"$ELASTIC_ENV.tmp"
mv "$ELASTIC_ENV.tmp" "$ELASTIC_ENV"
chmod 0600 "$ELASTIC_ENV"

ensure_server_certificate() {
  local name=$1
  local common_name=$2
  local subject_alt_names=$3
  local extended_key_usage=${4:-serverAuth}
  local certificate="$TLS_DIR/$name.crt"
  local key="$TLS_DIR/$name.key"
  if [ -s "$certificate" ] && [ -s "$key" ] &&
    openssl verify -CAfile "$TLS_DIR/ca.crt" "$certificate" >/dev/null 2>&1 &&
    openssl x509 -checkend 86400 -noout -in "$certificate" >/dev/null 2>&1; then
    return
  fi
  openssl req -newkey rsa:3072 -sha256 -nodes \
    -subj "/CN=$common_name" \
    -keyout "$key" -out "$TLS_DIR/$name.csr" >/dev/null 2>&1
  printf 'subjectAltName=%s\nextendedKeyUsage=%s\n' "$subject_alt_names" "$extended_key_usage" >"$TLS_DIR/$name.ext"
  openssl x509 -req -sha256 -days 30 \
    -in "$TLS_DIR/$name.csr" \
    -CA "$TLS_DIR/ca.crt" -CAkey "$TLS_DIR/ca.key" -CAcreateserial \
    -extfile "$TLS_DIR/$name.ext" \
    -out "$certificate" >/dev/null 2>&1
}

ensure_server_certificate logstash-server logstash.demo.internal \
  'DNS:logstash.demo.internal,DNS:logstash'
ensure_server_certificate logstash-client openshell-exporter \
  'DNS:openshell-exporter' clientAuth
ensure_server_certificate apm-server apm-server.demo.internal \
  'DNS:apm-server.demo.internal,DNS:apm-server,IP:127.0.0.1'
ensure_server_certificate elasticsearch-server elasticsearch.demo.internal \
  'DNS:elasticsearch.demo.internal,DNS:elasticsearch,IP:127.0.0.1'
ensure_server_certificate kibana-server kibana.demo.internal \
  'DNS:kibana.demo.internal,DNS:kibana,IP:127.0.0.1'
printf '%s' "$ELASTIC_INGEST_PASSWORD" >"$TLS_DIR/elastic-ingest-password"
printf '%s' "$ELASTIC_OTLP_PASSWORD" >"$TLS_DIR/elastic-otlp-password"
printf '%s' "$ELASTIC_APM_SECRET_TOKEN" >"$TLS_DIR/elastic-apm-secret-token"
chmod 0600 \
  "$TLS_DIR/logstash-server.key" \
  "$TLS_DIR/logstash-client.key" \
  "$TLS_DIR/apm-server.key" \
  "$TLS_DIR/elasticsearch-server.key" \
  "$TLS_DIR/kibana-server.key" \
  "$TLS_DIR/elastic-ingest-password" \
  "$TLS_DIR/elastic-otlp-password" \
  "$TLS_DIR/elastic-apm-secret-token" \
  "$DETECTION_PASSWORD_FILE"
chmod 0644 \
  "$TLS_DIR/logstash-server.crt" \
  "$TLS_DIR/logstash-client.crt" \
  "$TLS_DIR/apm-server.crt" \
  "$TLS_DIR/elasticsearch-server.crt" \
  "$TLS_DIR/kibana-server.crt"

COMPOSE=(docker compose --env-file "$ELASTIC_ENV" -f "$SCRIPT_DIR/compose.yaml" -f "$SCRIPT_DIR/compose.elastic.yaml")
echo "[2/5] Building the exporter; Logstash uses its pinned multi-architecture image..."
"${COMPOSE[@]}" build exporter
echo "[2/5] Starting Elasticsearch (first boot can take several minutes)..."
"${COMPOSE[@]}" up --detach elasticsearch elastic-init
echo "[2/5] Starting Kibana, mTLS Logstash, and authenticated APM Server OTLP intake..."
"${COMPOSE[@]}" up --detach kibana logstash apm-server prometheus grafana

wait_for() {
  local description=$1
  shift
  local attempts=120
  local elapsed=0
  while (( attempts > 0 )); do
    if "$@" >/dev/null 2>&1; then
      echo "$description is ready"
      return 0
    fi
    attempts=$((attempts - 1))
    elapsed=$((elapsed + 2))
    if (( elapsed % 20 == 0 )); then
      echo "Waiting for $description (${elapsed}s elapsed)..."
    fi
    sleep 2
  done
  echo "$description did not become ready" >&2
  return 1
}

wait_for Elasticsearch curl --fail --silent --cacert "$TLS_DIR/ca.crt" \
  --user "elastic:$ELASTIC_PASSWORD" https://127.0.0.1:9200/_cluster/health
wait_for Kibana curl --fail --silent --cacert "$TLS_DIR/ca.crt" \
  --user "elastic:$ELASTIC_PASSWORD" https://127.0.0.1:5601/api/status
wait_for "Logstash API" curl --fail --silent http://127.0.0.1:19600/_node/pipelines
wait_for "APM Server OTLP intake" curl --fail --silent \
  --cacert "$TLS_DIR/ca.crt" \
  --header "Authorization: Bearer $ELASTIC_APM_SECRET_TOKEN" \
  https://127.0.0.1:18200/
wait_for Prometheus curl --fail --silent http://127.0.0.1:9090/-/ready
wait_for Grafana curl --fail --silent http://127.0.0.1:3000/api/health
echo "[3/5] Provisioning the Kibana data view and SOC detection pack..."
"${COMPOSE[@]}" run --rm --no-deps kibana-setup

# Restart the exporter with its independent Elastic queues before starting the
# task whose trace must be visible in the current native OTLP destination.
"${COMPOSE[@]}" up --detach --force-recreate exporter
wait_for "OpenShell event exporter" curl --fail --silent "http://127.0.0.1:${DEMO_HEALTH_PORT:-13133}/"

echo "[4/5] Running a fresh real Hermes/Nemotron task through both Elastic lanes..."
./run-live-task.sh
# Refresh the task and candidate coordinates consumed by the Elastic verifier.
# Generated environment files are mode 0600 and ignored by Git.
# shellcheck disable=SC1090
source "$BASE_ENV"
DEMO_REPOSITORY_COMMIT=$(git -C "$SCRIPT_DIR" rev-parse HEAD)
DEMO_EXPORTER_VERSION=$("$SCRIPT_DIR/../../../scripts/check-version.sh")
DEMO_BUILD_CREATED=$(git -C "$SCRIPT_DIR" show -s --format=%cI HEAD)
awk -F= '
  $1 != "OPENSHELL_DEMO_SANDBOX_ID" &&
  $1 != "OPENSHELL_DEMO_POLICY_VERSION" &&
  $1 != "DEMO_REPOSITORY_COMMIT" &&
  $1 != "DEMO_EXPORTER_VERSION" &&
  $1 != "DEMO_BUILD_CREATED" &&
  $1 != "DEMO_TASK_STARTED_AT" &&
  $1 != "DEMO_TASK_FINISHED_AT" &&
  $1 != "DEMO_TASK_FILE_SHA256" &&
  $1 != "DEMO_AGENT_SESSION_ID" &&
  $1 != "DEMO_RELAY_SESSION_INSTANCE_ID"
' "$ELASTIC_ENV" >"$ELASTIC_ENV.tmp"
{
  printf 'OPENSHELL_DEMO_SANDBOX_ID=%s\n' "$OPENSHELL_DEMO_SANDBOX_ID"
  printf 'OPENSHELL_DEMO_POLICY_VERSION=%s\n' "$OPENSHELL_DEMO_POLICY_VERSION"
  printf 'DEMO_REPOSITORY_COMMIT=%s\n' "$DEMO_REPOSITORY_COMMIT"
  printf 'DEMO_EXPORTER_VERSION=%s\n' "$DEMO_EXPORTER_VERSION"
  printf 'DEMO_BUILD_CREATED=%s\n' "$DEMO_BUILD_CREATED"
  printf 'DEMO_TASK_STARTED_AT=%s\n' "$DEMO_TASK_STARTED_AT"
  printf 'DEMO_TASK_FINISHED_AT=%s\n' "$DEMO_TASK_FINISHED_AT"
  printf 'DEMO_TASK_FILE_SHA256=%s\n' "$DEMO_TASK_FILE_SHA256"
  printf 'DEMO_AGENT_SESSION_ID=%s\n' "$DEMO_AGENT_SESSION_ID"
  printf 'DEMO_RELAY_SESSION_INSTANCE_ID=%s\n' "$DEMO_RELAY_SESSION_INSTANCE_ID"
} >>"$ELASTIC_ENV.tmp"
mv "$ELASTIC_ENV.tmp" "$ELASTIC_ENV"
chmod 0600 "$ELASTIC_ENV"
native_otlp_has_agent_intake() {
  curl --fail --silent --cacert "$TLS_DIR/ca.crt" --user "elastic:$ELASTIC_PASSWORD" \
    --header 'Content-Type: application/json' --request POST \
    --data "$(jq -cn --arg sandbox "$OPENSHELL_DEMO_SANDBOX_ID" '{query:{term:{"openshell.sandbox.id":$sandbox}}}')" \
    "https://127.0.0.1:9200/traces-apm*/_count" | \
    jq -e '.count > 0' >/dev/null
}
wait_for "real agent telemetry in Elastic native OTLP" native_otlp_has_agent_intake

ELASTIC_DEMO_STARTED_AT=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
printf 'ELASTIC_DEMO_STARTED_AT=%s\n' "$ELASTIC_DEMO_STARTED_AT" >>"$ELASTIC_ENV"

# Produce three distinct real policy denials after the Elastic queue is active.
# These append real gateway OCSF evidence and exercise the sandbox-scoped burst
# detection; they do not inject synthetic records.
BASE_COMPOSE=(docker compose --env-file "$BASE_ENV" -f "$SCRIPT_DIR/compose.yaml")
echo "[5/5] Sending three real policy denials through the Elastic queue..."
for denied_url in https://example.com/ https://www.example.com/ https://example.org/; do
  "${BASE_COMPOSE[@]}" run --rm \
    --entrypoint /demo/control/sandbox-ssh-exec.sh \
    control hermes-demo \
    /usr/bin/curl --fail --silent --show-error "$denied_url" >/dev/null 2>&1 || true
done

echo "Waiting for Elastic indexing and running the end-to-end proof..."
sleep 5
wait_for "Elastic evidence" "$SCRIPT_DIR/verify-elastic.sh"
./verify-elastic.sh

# The conformance receiver proves both protocols during qualification. The
# final presentation is headless: only Elastic destinations and recovery remain
# in the exporter pipelines, and the fixture UI is stopped.
HEADLESS_COMPOSE=(docker compose --env-file "$ELASTIC_ENV" -f "$SCRIPT_DIR/compose.yaml" -f "$SCRIPT_DIR/compose.elastic.yaml" -f "$SCRIPT_DIR/compose.elastic-headless.yaml")
"${HEADLESS_COMPOSE[@]}" up --detach --force-recreate exporter
"${COMPOSE[@]}" stop conformance-receiver >/dev/null
wait_for "headless OpenShell event exporter" curl --fail --silent "http://127.0.0.1:${DEMO_HEALTH_PORT:-13133}/"
prometheus_exporter_up() {
  curl --fail --silent "http://127.0.0.1:9090/api/v1/query?query=up%7Bjob%3D%22openshell-event-exporter%22%7D" |
    jq -e '.status == "success" and (.data.result | length) == 1 and .data.result[0].value[1] == "1"' >/dev/null
}
wait_for "Prometheus exporter scrape" prometheus_exporter_up

printf '\nElastic Security: https://127.0.0.1:5601\n'
printf 'User: elastic; password: ./show-elastic-password.sh\n'
printf 'SOC dashboards:\n  - OpenShell SOC - Agent Security Command Center\n  - OpenShell SOC - Alert-to-Sandbox Investigation\n  - OpenShell SOC - Agent Activity and Enforcement Timeline\n  - OpenShell SOC - Evidence Coverage and Trust\n'
printf 'Exporter operations: http://127.0.0.1:3000/d/openshell-exporter-operations\n'
printf 'Grafana is the operational health UI; Kibana is the security investigation UI.\n'
