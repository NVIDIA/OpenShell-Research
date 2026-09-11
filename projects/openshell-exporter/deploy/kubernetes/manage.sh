#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHART="$ROOT/chart"
cd "$ROOT"
fail() { printf 'kubernetes deployment: %s\n' "$*" >&2; exit 1; }
CERTIFICATE_MANIFEST=""
ALLOW_PENDING_CERTIFICATES=false

load_env() {
  [[ -f .env ]] || fail "copy deploy/kubernetes/.env.example to .env and edit it"
  set -a
  source ./.env
  set +a
  : "${KUBERNETES_NAMESPACE:=openshell-observability}"
  : "${HELM_RELEASE:=openshell-exporter}"
  : "${HELM_VALUES_FILE:=values.local.yaml}"
  : "${HELM_TIMEOUT:=10m}"
  [[ "$HELM_VALUES_FILE" = /* ]] || HELM_VALUES_FILE="$ROOT/$HELM_VALUES_FILE"
  [[ -r "$HELM_VALUES_FILE" ]] || fail "Helm values file is unreadable: $HELM_VALUES_FILE"
}
helm_cmd() {
  local args=()
  [[ -z "${KUBERNETES_CONTEXT:-}" ]] || args+=(--kube-context "$KUBERNETES_CONTEXT")
  helm "${args[@]}" "$@"
}
kubectl_cmd() {
  local args=()
  [[ -z "${KUBERNETES_CONTEXT:-}" ]] || args+=(--context "$KUBERNETES_CONTEXT")
  kubectl "${args[@]}" "$@"
}
render() {
  helm_cmd template "$HELM_RELEASE" "$CHART"     --namespace "$KUBERNETES_NAMESPACE" -f "$HELM_VALUES_FILE"
}
validate() {
  load_env
  command -v helm >/dev/null || fail "Helm 3 is required"
  local output
  output=$(mktemp)
  trap 'rm -f "$output"' RETURN
  helm_cmd lint "$CHART" -f "$HELM_VALUES_FILE" >/dev/null
  render >"$output"
  grep -q 'kind: Deployment' "$output" || fail "rendered Deployment is missing"
  grep -q 'claimName:' "$output" || fail "persistent storage mounts are missing"
  grep -q 'replicas: 1' "$output" || fail "default standalone chart must remain a single writer"
  grep -q 'type: Recreate' "$output" || fail "single-writer update strategy is missing"
  grep -q 'readOnlyRootFilesystem: true' "$output" || fail "read-only root filesystem is missing"
  grep -q 'allowPrivilegeEscalation: false' "$output" || fail "privilege escalation is not disabled"
  if grep -q 'automountServiceAccountToken: true' "$output"; then
    grep -q 'kind: Role' "$output" || fail "service account token requires namespace-scoped RBAC"
    grep -q 'k8s_objects:' "$output" || fail "service account token is enabled without Kubernetes context"
  else
    grep -q 'automountServiceAccountToken: false' "$output" || fail "service account token boundary is ambiguous"
  fi
  if grep -q 'name: native-otlp' "$output"; then
    grep -q 'kind: NetworkPolicy' "$output" || fail "native OTLP without client identity requires ingress NetworkPolicy isolation"
  fi
  # The chart validates local tag + Never, or a pinned registry digest.
  if grep -Eq 'registry\.example|example\.internal|REPLACE_|sha256:0{64}' "$output"; then
    fail "replace every example image, gateway ID, endpoint, and digest"
  fi
  if grep -q 'kind: PersistentVolumeClaim' "$output"; then
    claim_count=$(grep -c '^kind: PersistentVolumeClaim$' "$output")
    retained_count=$(grep -c 'helm.sh/resource-policy: keep' "$output")
    [[ "$claim_count" -eq "$retained_count" ]] ||
      fail "every chart-managed evidence claim must be retained on uninstall"
  fi
  printf 'kubernetes deployment: Helm chart and values render a hardened, retained, single-writer workload\n'
}
secret_for_volume() {
  local manifest=$1 volume=$2
  awk -v volume="$volume" '
    $0 == "        - name: " volume {found=1; next}
    found && /secretName:/ {print $2; exit}
  ' "$manifest"
}
claim_for_volume() {
  local manifest=$1 volume=$2
  awk -v volume="$volume" '
    $0 == "        - name: " volume {found=1; next}
    found && /claimName:/ {print $2; exit}
  ' "$manifest"
}
volume_has_path() {
  local manifest=$1 volume=$2 path=$3
  awk -v volume="$volume" -v path="$path" '
    $0 == "        - name: " volume {found=1; next}
    found && $0 ~ /^        - name: / {exit 1}
    found && $1 == "path:" && $2 == path {matched=1; exit}
    END {exit matched ? 0 : 1}
  ' "$manifest"
}
resource_name() {
  local manifest=$1 kind=$2
  awk -v kind="$kind" '
    $1 == "kind:" && $2 == kind {found=1; next}
    found && $1 == "name:" {print $2; exit}
  ' "$manifest"
}
env_value() {
  local manifest=$1 name=$2
  awk -v name="$name" '
    $0 == "            - name: " name {found=1; next}
    found && $1 == "value:" {
      value=$0; sub(/^[[:space:]]*value:[[:space:]]*/, "", value)
      gsub(/^"|"$/, "", value); print value; exit
    }
  ' "$manifest"
}
forwarder_namespaces() {
  local manifest=$1
  awk '
    /^---$/ {namespace=""}
    $1 == "namespace:" {namespace=$2}
    $0 ~ /app.kubernetes.io\/component: sandbox-forwarder/ && namespace != "" {print namespace}
  ' "$manifest" | sort -u
}
certificate_secret_managed() {
  local manifest=$1 wanted_namespace=$2 wanted_secret=$3
  [[ -n "$manifest" && -r "$manifest" ]] || return 1
  awk -v wanted_namespace="$wanted_namespace" -v wanted_secret="$wanted_secret" '
    function reset() {kind=""; namespace=""; secret=""}
    function matches() {return kind == "Certificate" && namespace == wanted_namespace && secret == wanted_secret}
    /^---$/ {if (matches()) exit 0; reset(); next}
    $1 == "kind:" {kind=$2}
    $1 == "namespace:" && namespace == "" {namespace=$2}
    $1 == "secretName:" {secret=$2}
    END {exit matches() ? 0 : 1}
  ' "$manifest"
}
require_secret_keys() {
  local namespace=$1 secret=$2; shift 2
  if ! kubectl_cmd -n "$namespace" get secret "$secret" >/dev/null 2>&1; then
    if [[ "$ALLOW_PENDING_CERTIFICATES" == true ]] &&
       certificate_secret_managed "$CERTIFICATE_MANIFEST" "$namespace" "$secret"; then
      return
    fi
    fail "missing Secret $namespace/$secret"
  fi
  local key encoded
  for key in "$@"; do
    encoded=$(kubectl_cmd -n "$namespace" get secret "$secret" -o "jsonpath={.data.${key//./\\.}}")
    if [[ -z "$encoded" ]]; then
      if [[ "$ALLOW_PENDING_CERTIFICATES" == true ]] &&
         certificate_secret_managed "$CERTIFICATE_MANIFEST" "$namespace" "$secret"; then
        return
      fi
      fail "Secret $namespace/$secret is missing $key"
    fi
  done
}
certificate_records() {
  local manifest=$1
  awk '
    function reset() {kind=""; namespace=""; secret=""}
    function emit() {if (kind == "Certificate" && namespace != "" && secret != "") print namespace, secret}
    /^---$/ {emit(); reset(); next}
    $1 == "kind:" {kind=$2}
    $1 == "namespace:" && namespace == "" {namespace=$2}
    $1 == "secretName:" {secret=$2}
    END {emit()}
  ' "$manifest" | sort -u
}
wait_for_certificates() {
  local manifest=$1 namespace secret
  [[ -z "$(certificate_records "$manifest")" ]] && return
  kubectl_cmd get crd certificates.cert-manager.io >/dev/null ||
    fail "cert-manager Certificate CRD is missing"
  while read -r namespace; do
    [[ -n "$namespace" ]] || continue
    kubectl_cmd -n "$namespace" wait --for=condition=Ready certificate \
      -l "app.kubernetes.io/instance=$HELM_RELEASE" --timeout="$HELM_TIMEOUT" >/dev/null ||
      fail "cert-manager Certificates in $namespace did not become Ready"
  done < <(certificate_records "$manifest" | awk '{print $1}' | sort -u)
  while read -r namespace secret; do
    [[ -n "$namespace" && -n "$secret" ]] || continue
    require_secret_keys "$namespace" "$secret" ca.crt tls.crt tls.key
  done < <(certificate_records "$manifest")
}
verify_webhook_ca_bundle() {
  local manifest=$1 webhook bundle
  webhook=$(resource_name "$manifest" MutatingWebhookConfiguration)
  [[ -n "$webhook" ]] || return
  bundle=$(kubectl_cmd get mutatingwebhookconfiguration "$webhook" \
    -o 'jsonpath={.webhooks[0].clientConfig.caBundle}')
  [[ -n "$bundle" ]] || fail "admission webhook CA bundle was not populated"
}
decoded_token() {
  local namespace=$1 secret=$2 output=$3 encoded
  encoded=$(kubectl_cmd -n "$namespace" get secret "$secret" -o 'jsonpath={.data.token}')
  if ! printf '%s' "$encoded" | base64 --decode >"$output" 2>/dev/null; then
    printf '%s' "$encoded" | base64 -D >"$output" 2>/dev/null ||
      fail "cannot decode token from Secret $namespace/$secret"
  fi
}
require_cluster_inputs() {
  local manifest=$1 ns source_auth source_tls destination_auth destination_tls
  ns=$KUBERNETES_NAMESPACE
  kubectl_cmd get namespace "$ns" >/dev/null ||
    fail "create and Pod-Security-label namespace $ns before installing secrets"

  source_auth=$(secret_for_volume "$manifest" source-auth)
  source_tls=$(secret_for_volume "$manifest" source-tls)
  destination_auth=$(secret_for_volume "$manifest" destination-auth)
  destination_tls=$(secret_for_volume "$manifest" destination-tls)
  require_secret_keys "$ns" "$source_auth" token
  require_secret_keys "$ns" "$source_tls" ca.crt
  require_secret_keys "$ns" "$destination_auth" token
  require_secret_keys "$ns" "$destination_tls" ca.crt

  if volume_has_path "$manifest" source-tls tls.crt; then
    require_secret_keys "$ns" "$source_tls" tls.crt tls.key
  fi
  if volume_has_path "$manifest" destination-tls tls.crt; then
    require_secret_keys "$ns" "$destination_tls" tls.crt tls.key
  fi
  local source_token destination_token
  source_token=$(mktemp)
  destination_token=$(mktemp)
  trap 'rm -f "$source_token" "$destination_token"' RETURN
  decoded_token "$ns" "$source_auth" "$source_token"
  decoded_token "$ns" "$destination_auth" "$destination_token"
  [[ $(wc -c <"$destination_token") -ge 32 ]] ||
    fail "CloudEvents destination token must contain at least 32 bytes"
  cmp -s "$source_token" "$destination_token" &&
    fail "OpenShell source and CloudEvents destination tokens must be distinct"

  local source_claim
  source_claim=$(claim_for_volume "$manifest" openshell-logs)
  if [[ -n "$source_claim" ]]; then
    kubectl_cmd -n "$ns" get pvc "$source_claim" >/dev/null ||
      fail "missing read-only OpenShell source PVC $ns/$source_claim"
  fi
  local relay_claim relay_auth relay_tls otlp_auth otlp_tls forwarded_auth forwarded_tls
  relay_claim=$(claim_for_volume "$manifest" relay-logs)
  if [[ -n "$relay_claim" ]]; then
    kubectl_cmd -n "$ns" get pvc "$relay_claim" >/dev/null ||
      fail "missing read-only Relay source PVC $ns/$relay_claim"
  fi

  relay_auth=$(secret_for_volume "$manifest" relay-input-auth)
  if [[ -n "$relay_auth" ]]; then
    relay_tls=$(secret_for_volume "$manifest" relay-input-tls)
    require_secret_keys "$ns" "$relay_auth" token
    if certificate_secret_managed "$manifest" "$ns" "$relay_tls"; then
      require_secret_keys "$ns" "$relay_tls" tls.crt tls.key ca.crt
    else
      require_secret_keys "$ns" "$relay_tls" tls.crt tls.key client-ca.crt
    fi
    local relay_token
    relay_token=$(mktemp)
    decoded_token "$ns" "$relay_auth" "$relay_token"
    [[ $(wc -c <"$relay_token") -ge 32 ]] ||
      fail "Relay input token must contain at least 32 bytes"
    for token in "$source_token" "$destination_token"; do
      cmp -s "$token" "$relay_token" &&
        fail "Relay input token must have a distinct trust boundary"
    done
  fi

  otlp_auth=$(secret_for_volume "$manifest" otlp-destination-auth)
  if [[ -n "$otlp_auth" ]]; then
    otlp_tls=$(secret_for_volume "$manifest" otlp-destination-tls)
    require_secret_keys "$ns" "$otlp_auth" token
    require_secret_keys "$ns" "$otlp_tls" ca.crt
    if volume_has_path "$manifest" otlp-destination-tls tls.crt; then
      require_secret_keys "$ns" "$otlp_tls" tls.crt tls.key
    fi
    local otlp_token
    otlp_token=$(mktemp)
    decoded_token "$ns" "$otlp_auth" "$otlp_token"
    [[ $(wc -c <"$otlp_token") -ge 32 ]] ||
      fail "OTLP destination token must contain at least 32 bytes"
    for token in "$source_token" "$destination_token"; do
      cmp -s "$token" "$otlp_token" &&
        fail "OTLP destination token must have a distinct trust boundary"
    done
    if [[ -n "${relay_token:-}" ]]; then
      cmp -s "$relay_token" "$otlp_token" &&
        fail "Relay input and OTLP destination tokens must be distinct"
    fi
  fi

  local native_tls
  native_tls=$(secret_for_volume "$manifest" native-otlp-tls)
  if [[ -n "$native_tls" ]]; then
    require_secret_keys "$ns" "$native_tls" tls.crt tls.key
  fi

  forwarded_auth=$(secret_for_volume "$manifest" forwarded-ocsf-auth)
  if [[ -n "$forwarded_auth" ]]; then
    forwarded_tls=$(secret_for_volume "$manifest" forwarded-ocsf-tls)
    require_secret_keys "$ns" "$forwarded_auth" token
    if certificate_secret_managed "$manifest" "$ns" "$forwarded_tls"; then
      require_secret_keys "$ns" "$forwarded_tls" tls.crt tls.key ca.crt
    else
      require_secret_keys "$ns" "$forwarded_tls" tls.crt tls.key client-ca.crt
    fi
    local forwarded_token
    forwarded_token=$(mktemp)
    decoded_token "$ns" "$forwarded_auth" "$forwarded_token"
    [[ $(wc -c <"$forwarded_token") -ge 32 ]] ||
      fail "forwarded OCSF input token must contain at least 32 bytes"
    for token in "$source_token" "$destination_token" "${relay_token:-}" "${otlp_token:-}"; do
      [[ -z "$token" ]] || ! cmp -s "$token" "$forwarded_token" ||
        fail "forwarded OCSF input token must have a distinct trust boundary"
    done
  fi
  if grep -q 'kind: MutatingWebhookConfiguration' "$manifest"; then
    local webhook_tls forwarder_tls_name forwarder_auth_name evidence_class state_class sandbox_ns forwarder_copy
    webhook_tls=$(secret_for_volume "$manifest" webhook-tls)
    require_secret_keys "$ns" "$webhook_tls" tls.crt tls.key
    forwarder_tls_name=$(env_value "$manifest" SANDBOX_INJECTOR_FORWARDER_TLS_SECRET)
    forwarder_auth_name=$(env_value "$manifest" SANDBOX_INJECTOR_FORWARDER_AUTH_SECRET)
    evidence_class=$(env_value "$manifest" SANDBOX_INJECTOR_EVIDENCE_STORAGE_CLASS)
    state_class=$(env_value "$manifest" SANDBOX_INJECTOR_STATE_STORAGE_CLASS)
    for storage_class in "$evidence_class" "$state_class"; do
      if [[ -n "$storage_class" ]]; then
        local provisioner
        provisioner=$(kubectl_cmd get storageclass "$storage_class" -o 'jsonpath={.provisioner}') ||
          fail "missing sidecar StorageClass $storage_class"
        [[ "$provisioner" != kubernetes.io/* ]] ||
          fail "ReadWriteOncePod sidecar storage requires a CSI StorageClass; $storage_class uses $provisioner"
      fi
    done
    while IFS= read -r sandbox_ns; do
      [[ -n "$sandbox_ns" ]] || continue
      kubectl_cmd get namespace "$sandbox_ns" >/dev/null ||
        fail "create allow-listed sandbox namespace $sandbox_ns before installation"
      require_secret_keys "$sandbox_ns" "$forwarder_tls_name" ca.crt tls.crt tls.key
      require_secret_keys "$sandbox_ns" "$forwarder_auth_name" token
      forwarder_copy=$(mktemp)
      decoded_token "$sandbox_ns" "$forwarder_auth_name" "$forwarder_copy"
      [[ $(wc -c <"$forwarder_copy") -ge 32 ]] ||
        fail "sandbox forwarder token in $sandbox_ns/$forwarder_auth_name must contain at least 32 bytes"
      [[ -n "${forwarded_token:-}" ]] && cmp -s "$forwarded_token" "$forwarder_copy" ||
        fail "sandbox forwarder token in $sandbox_ns must match the exporter's forwarded-file receiver token"
      for token in "$source_token" "$destination_token" "${relay_token:-}" "${otlp_token:-}"; do
        [[ -z "$token" ]] || ! cmp -s "$token" "$forwarder_copy" ||
          fail "sandbox forwarder token in $sandbox_ns must have a distinct trust boundary"
      done
      rm -f "$forwarder_copy"
    done < <(forwarder_namespaces "$manifest")
  fi
  rm -f "${relay_token:-}" "${otlp_token:-}" "${forwarded_token:-}"

}

action=${1:-}
case "$action" in
  validate) validate ;;
  up)
    validate
    manifest=$(mktemp)
    render >"$manifest"
    CERTIFICATE_MANIFEST=$manifest
    ALLOW_PENDING_CERTIFICATES=true
    require_cluster_inputs "$manifest"
    ALLOW_PENDING_CERTIFICATES=false
    helm_cmd upgrade --install "$HELM_RELEASE" "$CHART"       --namespace "$KUBERNETES_NAMESPACE" -f "$HELM_VALUES_FILE"       --atomic --wait --timeout "$HELM_TIMEOUT"
    wait_for_certificates "$manifest"
    verify_webhook_ca_bundle "$manifest"
    require_cluster_inputs "$manifest"
    rm -f "$manifest"
    ;;
  down)
    load_env
    helm_cmd uninstall "$HELM_RELEASE" --namespace "$KUBERNETES_NAMESPACE"       --wait --timeout "$HELM_TIMEOUT"
    printf 'kubernetes deployment: retained evidence PVCs were not deleted\n'
    ;;
  status)
    load_env
    helm_cmd status "$HELM_RELEASE" --namespace "$KUBERNETES_NAMESPACE"
    kubectl_cmd -n "$KUBERNETES_NAMESPACE" get deployment,pod,service,pvc       -l app.kubernetes.io/instance="$HELM_RELEASE"
    if kubectl_cmd get crd certificates.cert-manager.io >/dev/null 2>&1; then
      kubectl_cmd -n "$KUBERNETES_NAMESPACE" get certificate \
        -l app.kubernetes.io/instance="$HELM_RELEASE"
    fi
    ;;
  logs)
    load_env
    manifest=$(mktemp)
    render >"$manifest"
    deployment_name=$(resource_name "$manifest" Deployment)
    rm -f "$manifest"
    kubectl_cmd -n "$KUBERNETES_NAMESPACE" logs -f       "deployment/$deployment_name" -c exporter
    ;;
  *) fail "usage: manage.sh validate|up|down|status|logs" ;;
esac
