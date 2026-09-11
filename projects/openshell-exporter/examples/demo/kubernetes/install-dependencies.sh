#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
resolve_tools

for namespace in "$OBS_NAMESPACE" "$GATEWAY_NAMESPACE" "$SANDBOX_NAMESPACE" "$PROVIDER_CREDENTIAL_NAMESPACE"; do
  k create namespace "$namespace" --dry-run=client -o yaml | k apply -f - >/dev/null
done
k label namespace "$SANDBOX_NAMESPACE" observability.openshell.nvidia.com/inject=enabled --overwrite >/dev/null

if ! k get storageclass csi-hostpath-sc >/dev/null 2>&1; then
  "$MINIKUBE_BIN" addons enable csi-hostpath-driver -p "$PROFILE" >/dev/null
fi
k -n default rollout status statefulset/csi-hostpathplugin --timeout=10m >/dev/null

if ! k get crd certificates.cert-manager.io >/dev/null 2>&1; then
  note "installing cert-manager v1.21.1"
  h upgrade --install cert-manager oci://quay.io/jetstack/charts/cert-manager \
    --version v1.21.1 --namespace cert-manager --create-namespace --set crds.enabled=true \
    --atomic --wait --timeout 10m
fi
k -n cert-manager wait --for=condition=Available deployment/cert-manager deployment/cert-manager-cainjector deployment/cert-manager-webhook --timeout=10m >/dev/null
k apply -f "$DEMO_DIR/manifests/pki.yaml" >/dev/null
wait_certificate cert-manager openshell-exporter-root-ca
k -n cert-manager get secret openshell-exporter-root-ca \
  -o jsonpath='{.data.tls\.crt}' | base64 --decode >"$RUNTIME_DIR/openshell-exporter-root-ca.crt"
[[ -s "$RUNTIME_DIR/openshell-exporter-root-ca.crt" ]] || fail "exporter demo CA certificate is empty"
chmod 0644 "$RUNTIME_DIR/openshell-exporter-root-ca.crt"

"$DEMO_DIR/ensure-agent-sandbox-image.sh"
agent_manifest_source="$RUNTIME_DIR/agent-sandbox-$AGENT_SANDBOX_VERSION.source.yaml"
agent_manifest="$RUNTIME_DIR/agent-sandbox-$AGENT_SANDBOX_VERSION.yaml"
if [[ ! -s "$agent_manifest_source" ]]; then
  download "$AGENT_SANDBOX_URL" "$agent_manifest_source"
fi
sha256_check "$AGENT_SANDBOX_SHA256" "$agent_manifest_source"
controller_digest=$(docker exec "$NODE_CONTAINER" ctr -n k8s.io images ls | awk -v image="$AGENT_SANDBOX_IMAGE" '$1 == image {digest=$3} END {print digest}')
[[ "$controller_digest" =~ ^sha256:[0-9a-f]{64}$ ]] || fail "could not resolve the local Agent Sandbox controller digest"
controller_repository=${AGENT_SANDBOX_IMAGE%:*}
docker exec "$NODE_CONTAINER" ctr -n k8s.io images tag "$AGENT_SANDBOX_IMAGE" "$controller_repository@$controller_digest" >/dev/null 2>&1 || true
sed "s|image: $AGENT_SANDBOX_IMAGE|image: $controller_repository@$controller_digest\\n        imagePullPolicy: Never|" "$agent_manifest_source" >"$agent_manifest"
k apply -f "$agent_manifest" >/dev/null
k wait --for=condition=Established crd/sandboxes.agents.x-k8s.io --timeout=5m >/dev/null
controller=$(k get deployment -A -o json | jq -r '.items[] | select(.metadata.name | test("sandbox")) | [.metadata.namespace,.metadata.name] | @tsv' | head -1)
[[ -n "$controller" ]] || fail "Agent Sandbox controller deployment was not found"
read -r controller_ns controller_name <<<"$controller"
k -n "$controller_ns" rollout status "deployment/$controller_name" --timeout=10m >/dev/null
note "cert-manager and Agent Sandbox $AGENT_SANDBOX_VERSION are ready"
