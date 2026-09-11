#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
resolve_tools

ensure_credentials() {
  if k -n "$OBS_NAMESPACE" get secret openshell-elastic-credentials >/dev/null 2>&1; then
    return
  fi
  k -n "$OBS_NAMESPACE" create secret generic openshell-elastic-credentials \
    --from-literal=ELASTIC_PASSWORD="$(random_token)" \
    --from-literal=ELASTIC_INGEST_PASSWORD="$(random_token)" \
    --from-literal=ELASTIC_OTLP_PASSWORD="$(random_token)" \
    --from-literal=ELASTIC_APM_SECRET_TOKEN="$(random_token)" \
    --from-literal=ELASTIC_DETECTION_PASSWORD="$(random_token)" \
    --from-literal=KIBANA_SYSTEM_PASSWORD="$(random_token)" \
    --from-literal=KIBANA_SECURITY_KEY="$(random_token)" \
    --from-literal=KIBANA_SAVED_OBJECTS_KEY="$(random_token)" \
    --from-literal=KIBANA_REPORTING_KEY="$(random_token)" >/dev/null
}

apply_configmap() {
  local name=$1
  shift
  k -n "$OBS_NAMESPACE" create configmap "$name" "$@" --dry-run=client -o yaml | k apply -f - >/dev/null
}

ensure_credentials
apm_token=$(k -n "$OBS_NAMESPACE" get secret openshell-elastic-credentials -o jsonpath='{.data.ELASTIC_APM_SECRET_TOKEN}' | base64 --decode)
k -n "$OBS_NAMESPACE" create secret generic openshell-exporter-elastic-apm-auth \
  --from-literal=token="$apm_token" --dry-run=client -o yaml | k apply -f - >/dev/null
unset apm_token
secret_token "$OBS_NAMESPACE" openshell-exporter-elastic-cloudevents-auth

apply_configmap openshell-elastic-logstash \
  --from-file="$REPO_ROOT/integrations/elastic/logstash/logstash.yml" \
  --from-file="$REPO_ROOT/integrations/elastic/logstash/pipelines.yml" \
  --from-file="$REPO_ROOT/integrations/elastic/logstash/openshell-cloudevents.conf" \
  --from-file="$REPO_ROOT/integrations/elastic/logstash/cloud_events.rb" \
  --from-file="$REPO_ROOT/integrations/elastic/logstash/entrypoint.sh"
apply_configmap openshell-elastic-apm \
  --from-file="$REPO_ROOT/integrations/elastic/apm-server/apm-server.yml" \
  --from-file="$REPO_ROOT/integrations/elastic/apm-server/entrypoint.sh"
apply_configmap openshell-elastic-init-assets \
  --from-file="$REPO_ROOT/examples/demo/real-gateway/elastic/provision-elasticsearch.sh" \
  --from-file="$REPO_ROOT/integrations/elastic/assets/component-template.json" \
  --from-file="$REPO_ROOT/integrations/elastic/assets/index-template.json" \
  --from-file="$REPO_ROOT/integrations/elastic/assets/trace-component-template.json" \
  --from-file="$REPO_ROOT/integrations/elastic/assets/trace-index-template.json" \
  --from-file="$REPO_ROOT/integrations/elastic/assets/ingest-role.json" \
  --from-file="$REPO_ROOT/integrations/elastic/assets/otlp-ingest-role.json" \
  --from-file="$REPO_ROOT/integrations/elastic/assets/component-template-v2.json" \
  --from-file="$REPO_ROOT/integrations/elastic/assets/index-template-v2.json" \
  --from-file="$REPO_ROOT/integrations/elastic/assets/apm-ingest-pipeline.json"
apply_configmap openshell-elastic-kibana-assets \
  --from-file="$REPO_ROOT/examples/demo/real-gateway/elastic/provision-kibana.sh" \
  --from-file="$REPO_ROOT/integrations/elastic/assets/soc-rules.ndjson" \
  --from-file="$REPO_ROOT/integrations/elastic/assets/soc-saved-objects.ndjson" \
  --from-file="$REPO_ROOT/integrations/elastic/assets/soc-investigation-saved-objects.ndjson" \
  --from-file="$REPO_ROOT/integrations/elastic/assets/policy-engine-saved-objects.ndjson" \
  --from-file="$REPO_ROOT/integrations/elastic/assets/soc-viewer-role.json" \
  --from-file="$REPO_ROOT/integrations/elastic/assets/soc-analyst-role.json" \
  --from-file="$REPO_ROOT/integrations/elastic/assets/soc-detection-engineer-role.json" \
  --from-file="$REPO_ROOT/integrations/elastic/assets/soc-webhook-body.json"
apply_configmap openshell-prometheus-config --from-file=prometheus.yml="$DEMO_DIR/elastic/prometheus.yaml"
apply_configmap openshell-grafana-dashboard \
  --from-file="$REPO_ROOT/examples/demo/real-gateway/monitoring/grafana/dashboards/openshell-exporter-operations.json"
apply_configmap openshell-grafana-provisioning \
  --from-file=datasources.yaml="$REPO_ROOT/examples/demo/real-gateway/monitoring/grafana/provisioning/datasources/prometheus.yaml" \
  --from-file=dashboards.yaml="$REPO_ROOT/examples/demo/real-gateway/monitoring/grafana/provisioning/dashboards/default.yaml"

k -n "$OBS_NAMESPACE" delete job openshell-elastic-init openshell-kibana-setup --ignore-not-found --wait=true >/dev/null
k apply -f "$DEMO_DIR/manifests/elastic-soc.yaml" >/dev/null
for deployment in logstash apm-server kibana prometheus grafana; do
  k -n "$OBS_NAMESPACE" rollout restart "deployment/$deployment" >/dev/null
  wait_deployment "$OBS_NAMESPACE" "$deployment" >/dev/null
done
for certificate in openshell-elastic-elasticsearch openshell-elastic-kibana openshell-elastic-logstash-server openshell-elastic-logstash-client openshell-elastic-apm-server; do
  wait_certificate "$OBS_NAMESPACE" "$certificate"
done
k -n "$OBS_NAMESPACE" rollout status statefulset/elasticsearch --timeout=10m >/dev/null
k -n "$OBS_NAMESPACE" wait --for=condition=Complete job/openshell-elastic-init --timeout=10m >/dev/null
for deployment in logstash apm-server kibana prometheus grafana; do
  wait_deployment "$OBS_NAMESPACE" "$deployment" >/dev/null
done
k -n "$OBS_NAMESPACE" wait --for=condition=Complete job/openshell-kibana-setup --timeout=10m >/dev/null
note "Elastic SOC, native APM intake, Prometheus, Grafana, and reviewed dashboards are ready"
