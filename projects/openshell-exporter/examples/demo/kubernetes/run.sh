#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

load_nvidia_api_key
"$DEMO_DIR/preflight.sh"
resolve_tools
"$DEMO_DIR/bootstrap-cluster.sh"
"$DEMO_DIR/install-dependencies.sh"
"$DEMO_DIR/build-images.sh"
# shellcheck disable=SC1091
source "$RUNTIME_DIR/images.env"

note "installing OpenShell $OPEN_SHELL_VERSION"
h upgrade --install openshell "$OPEN_SHELL_CHART" --version "$OPEN_SHELL_VERSION" \
  --namespace "$GATEWAY_NAMESPACE" -f "$DEMO_DIR/values/openshell.yaml" \
  --atomic --wait --timeout 15m
k -n "$GATEWAY_NAMESPACE" rollout status statefulset/openshell --timeout=10m >/dev/null
wait_certificate "$GATEWAY_NAMESPACE" openshell-server
wait_certificate "$GATEWAY_NAMESPACE" openshell-client
k -n "$GATEWAY_NAMESPACE" create secret generic openshell-demo-nvidia-api \
  --from-literal=NVIDIA_API_KEY="$NVIDIA_API_KEY" --dry-run=client -o yaml | k apply -f - >/dev/null

secret_token "$OBS_NAMESPACE" openshell-exporter-source-auth
secret_token "$OBS_NAMESPACE" openshell-exporter-relay-input-auth
secret_token "$OBS_NAMESPACE" openshell-exporter-forwarded-ocsf-auth
copy_secret "$GATEWAY_NAMESPACE" openshell-client-tls "$OBS_NAMESPACE" openshell-exporter-source-tls
copy_secret "$GATEWAY_NAMESPACE" openshell-client-tls "$SANDBOX_NAMESPACE" openshell-client-tls

forwarded_token=$(k -n "$OBS_NAMESPACE" get secret openshell-exporter-forwarded-ocsf-auth -o jsonpath='{.data.token}' | base64 --decode)
k -n "$SANDBOX_NAMESPACE" create secret generic openshell-exporter-forwarded-ocsf-auth \
  --from-literal=token="$forwarded_token" --dry-run=client -o yaml | k apply -f - >/dev/null
unset forwarded_token

"$DEMO_DIR/run-elastic.sh"

exporter_values="$VALUES_DIR/exporter.yaml"
sed -e "s|EXPORTER_DIGEST|$EXPORTER_DIGEST|g" \
    -e "s|gatewayId: omnistation-kubernetes-demo|gatewayId: $GATEWAY_ID|" \
    "$DEMO_DIR/values/exporter.yaml.tpl" >"$exporter_values"
chmod 0600 "$exporter_values"

"$HELM_BIN" lint "$REPO_ROOT/deploy/kubernetes/chart" -f "$exporter_values" >/dev/null
"$HELM_BIN" template openshell-exporter "$REPO_ROOT/deploy/kubernetes/chart" \
  --namespace "$OBS_NAMESPACE" -f "$exporter_values" >"$RUNTIME_DIR/exporter-rendered.yaml"
h upgrade --install openshell-exporter "$REPO_ROOT/deploy/kubernetes/chart" \
  --namespace "$OBS_NAMESPACE" -f "$exporter_values" --atomic --wait --timeout 15m
wait_deployments_by_instance "$OBS_NAMESPACE" openshell-exporter
wait_certificates_by_instance "$OBS_NAMESPACE" openshell-exporter
wait_certificates_by_instance "$SANDBOX_NAMESPACE" openshell-exporter

exporter_host="openshell-exporter-openshell-event-exporter.${OBS_NAMESPACE}.svc.cluster.local"
provider_profile="$RUNTIME_DIR/relay-otlp-provider.yaml"
sed "s|__OPENSHELL_EXPORTER_HOST__|$exporter_host|g" \
  "$REPO_ROOT/deploy/kubernetes/integrations/openshell/relay-otlp-provider.yaml.tpl" >"$provider_profile"
relay_token=$(k -n "$OBS_NAMESPACE" get secret openshell-exporter-relay-input-auth -o jsonpath='{.data.token}' | base64 --decode)
k -n "$GATEWAY_NAMESPACE" create secret generic openshell-demo-relay-provider \
  --from-literal=token="$relay_token" --dry-run=client -o yaml | k apply -f - >/dev/null
unset relay_token
trap 'k -n "$GATEWAY_NAMESPACE" delete secret openshell-demo-relay-provider --ignore-not-found >/dev/null 2>&1 || true' EXIT
k -n "$GATEWAY_NAMESPACE" create configmap openshell-demo-task \
  --from-file=policy.yaml="$DEMO_DIR/sandbox/policy.yaml" \
  --from-file=draft-proposal.json="$DEMO_DIR/sandbox/draft-proposal.json" \
  --from-file=relay-otlp-provider.yaml="$provider_profile" \
  --dry-run=client -o yaml | k apply -f - >/dev/null
"$DEMO_DIR/run-task.sh"
k -n "$GATEWAY_NAMESPACE" delete secret openshell-demo-relay-provider --ignore-not-found >/dev/null
trap - EXIT
"$DEMO_DIR/verify.sh"

note "turnkey Kubernetes SOC demo is running"
note "SOC tunnel: ssh -N -L 15601:127.0.0.1:15601 -L 13000:127.0.0.1:13000 fdelgadolope@omni-lsn-7r5sk.ext-nv-prd-apps.teleport.sh"
note "Kibana SOC: https://127.0.0.1:15601"
note "Grafana operations: http://127.0.0.1:13000"
