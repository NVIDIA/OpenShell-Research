#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Run the egress watchdog as a pod on the control node and register it with the
# gateway as supervisor middleware. Run on the control node after
# control-node.sh, from this checkout: the pod runs the code in it.
#
# Usage:
#   deploy/watchdog.sh            mint TLS material if needed, deploy, register
#   deploy/watchdog.sh --remove   delete the watchdog and its registration
#
# The gateway calls Describe at startup and refuses to start if the watchdog is
# unreachable, so the pod is ready before the gateway is reconfigured.

set -euo pipefail

deploy_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(dirname "${deploy_dir}")"
# shellcheck source=versions.env
source "${deploy_dir}/versions.env"
# shellcheck disable=SC1091
source "${TWOBOX_HOME:-${HOME}/.twobox}/env"

SERVICE_DNS=egress-watchdog.openshell.svc.cluster.local
state="${TWOBOX_HOME}/watchdog"
mkdir -p "${state}"

render_manifest() {
  sed -e "s|__WATCHDOG_IMAGE__|${WATCHDOG_IMAGE}|" -e "s|__PROJECT_DIR__|${project_dir}|" \
    -e "s|__RUN_AS_UID__|$(owner_id -u)|" -e "s|__RUN_AS_GID__|$(owner_id -g)|" \
    "${deploy_dir}/watchdog.yaml"
}

# The checkout's owner uid (-u) or gid (-g). GNU stat on Linux, BSD stat on macOS.
owner_id() {
  local gnu=%u bsd=%u
  if [[ "$1" == -g ]]; then gnu=%g bsd=%g; fi
  stat -c "${gnu}" "${project_dir}" 2>/dev/null || stat -f "${bsd}" "${project_dir}"
}

main() {
  case "${1:-}" in
    --remove) remove; return ;;
    "") ;;
    *) echo "usage: $0 [--remove]" >&2; exit 2 ;;
  esac
  mint_tls
  deploy_pod
  register
}

mint_tls() {
  if [[ -f "${state}/server.crt" ]] &&
    openssl x509 -in "${state}/server.crt" -noout -checkhost "${SERVICE_DNS}" | grep -q "does match"; then
    return
  fi
  echo "==> Minting watchdog CA and server certificate for ${SERVICE_DNS}"
  openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 365 \
    -subj "/CN=egress-watchdog-ca" -keyout "${state}/ca.key" -out "${state}/ca.crt" 2>/dev/null
  openssl req -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -subj "/CN=egress-watchdog" \
    -keyout "${state}/server.key" -out "${state}/server.csr" 2>/dev/null
  openssl x509 -req -in "${state}/server.csr" -CA "${state}/ca.crt" -CAkey "${state}/ca.key" \
    -CAcreateserial -days 365 -out "${state}/server.crt" \
    -extfile <(printf 'subjectAltName=DNS:%s,DNS:egress-watchdog.openshell.svc\nextendedKeyUsage=serverAuth\n' \
      "${SERVICE_DNS}") 2>/dev/null
  chmod 600 "${state}"/*.key
}

deploy_pod() {
  echo "==> Deploying the watchdog pod"
  kubectl -n openshell create secret tls egress-watchdog-tls \
    --cert="${state}/server.crt" --key="${state}/server.key" --dry-run=client -o yaml |
    kubectl apply -f - >/dev/null
  kubectl -n openshell create secret generic egress-watchdog-ca \
    --from-file=ca.crt="${state}/ca.crt" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
  render_manifest | kubectl apply -f - >/dev/null
  # Restart so a changed certificate or checkout is picked up.
  kubectl -n openshell rollout restart deployment/egress-watchdog >/dev/null
  # The first start installs dependencies from PyPI.
  kubectl -n openshell rollout status deployment/egress-watchdog --timeout=300s
}

register() {
  echo "==> Registering the watchdog with the gateway"
  cat >"${state}/values.yaml" <<EOF
gatewayConfig:
  openshell.supervisor.middleware:
    - name: egress-watchdog
      grpc_endpoint: https://${SERVICE_DNS}:50051
      tls_ca_cert_path: /etc/egress-watchdog/ca.crt
      max_payload_bytes: 4194304
      timeout: 500ms
server:
  extraVolumes:
    - name: egress-watchdog-ca
      secret:
        secretName: egress-watchdog-ca
  extraVolumeMounts:
    - name: egress-watchdog-ca
      mountPath: /etc/egress-watchdog
      readOnly: true
EOF
  helm upgrade openshell oci://ghcr.io/nvidia/openshell/helm-chart \
    --version "${OPENSHELL_VERSION}" --namespace openshell \
    --reuse-values --values "${state}/values.yaml" --wait --timeout 5m >/dev/null
  restart_gateway
  openshell gateway info | grep -A1 "egress-watchdog"
  echo "Watchdog log: kubectl -n openshell logs deployment/egress-watchdog"
}

remove() {
  # Unregister first: the gateway will not start while a registered watchdog is down.
  helm upgrade openshell oci://ghcr.io/nvidia/openshell/helm-chart \
    --version "${OPENSHELL_VERSION}" --namespace openshell --reuse-values \
    --set-json 'gatewayConfig.openshell\.supervisor\.middleware=[]' \
    --set-json 'server.extraVolumes=[]' --set-json 'server.extraVolumeMounts=[]' \
    --wait --timeout 5m >/dev/null
  restart_gateway
  render_manifest | kubectl delete --ignore-not-found -f - >/dev/null
  kubectl -n openshell delete secret egress-watchdog-tls egress-watchdog-ca --ignore-not-found >/dev/null
}

restart_gateway() {
  # Registration is read at gateway startup; a config-only change may not roll the pod.
  kubectl -n openshell rollout restart statefulset/openshell >/dev/null
  kubectl -n openshell rollout status statefulset/openshell --timeout=180s
  for _ in $(seq 1 30); do
    openshell status 2>/dev/null | grep -q "Status: Connected" && return
    sleep 2
  done
}

main "$@"
