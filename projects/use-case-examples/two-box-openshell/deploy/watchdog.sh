#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Run the egress watchdog on the control node as a plain process and register it with the
# gateway as supervisor middleware. Run after control-node.sh.
#
# Usage:
#   deploy/watchdog.sh          mint TLS material if needed, (re)start, register
#   deploy/watchdog.sh --stop   stop the watchdog process
#
# Environment:
#   WATCHDOG_HOST  address the gateway and supervisor pods use to reach this
#                  process (default: CONTROL_IP from the operator environment)
#
# The gateway calls Describe at startup and refuses to start if the watchdog is
# unreachable, so the process starts before the gateway is reconfigured.

set -euo pipefail

deploy_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(dirname "${deploy_dir}")"
# shellcheck source=versions.env
source "${deploy_dir}/versions.env"
# shellcheck disable=SC1091
source "${TWOBOX_HOME:-${HOME}/.twobox}/env"

WATCHDOG_HOST="${WATCHDOG_HOST:-${CONTROL_IP}}"
WATCHDOG_PORT="${WATCHDOG_PORT:-50051}"
state="${TWOBOX_HOME}/watchdog"
mkdir -p "${state}"

stop_watchdog() {
  if [[ -f "${state}/pid" ]] && kill -0 "$(cat "${state}/pid")" 2>/dev/null; then
    kill "$(cat "${state}/pid")"
  fi
  rm -f "${state}/pid"
}

if [[ "${1:-}" == "--stop" ]]; then
  stop_watchdog
  exit 0
fi

if [[ ! -f "${state}/ca.crt" ]]; then
  echo "==> Minting watchdog CA and server certificate for ${WATCHDOG_HOST}"
  openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 365 -subj "/CN=twobox-watchdog-ca" \
    -keyout "${state}/ca.key" -out "${state}/ca.crt" 2>/dev/null
  openssl req -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -subj "/CN=twobox-watchdog" \
    -keyout "${state}/server.key" -out "${state}/server.csr" 2>/dev/null
  openssl x509 -req -in "${state}/server.csr" -CA "${state}/ca.crt" -CAkey "${state}/ca.key" \
    -CAcreateserial -days 365 -out "${state}/server.crt" \
    -extfile <(printf 'subjectAltName=IP:%s\nextendedKeyUsage=serverAuth\n' "${WATCHDOG_HOST}") 2>/dev/null
  chmod 600 "${state}"/*.key
fi

echo "==> Exporting the gateway's JWT public key"
kubectl -n openshell get secret openshell-jwt-keys -o jsonpath='{.data.public\.pem}' |
  base64 -d >"${state}/jwt-public.pem"

echo "==> Starting the watchdog on ${WATCHDOG_HOST}:${WATCHDOG_PORT}"
stop_watchdog
uv sync --project "${project_dir}" --locked --quiet
nohup uv run --project "${project_dir}" --locked twobox-watchdog \
  --listen "${WATCHDOG_HOST}:${WATCHDOG_PORT}" \
  --tls-cert "${state}/server.crt" --tls-key "${state}/server.key" \
  --jwt-public-key "${state}/jwt-public.pem" \
  >>"${state}/watchdog.jsonl" 2>>"${state}/watchdog.err" &
echo $! >"${state}/pid"
for _ in $(seq 1 30); do
  grep -q "listening" "${state}/watchdog.err" 2>/dev/null && break
  sleep 1
done

echo "==> Registering the watchdog with the gateway"
kubectl -n openshell create secret generic twobox-watchdog-ca \
  --from-file=ca.crt="${state}/ca.crt" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
cat >"${state}/values.yaml" <<EOF
gatewayConfig:
  openshell.supervisor.middleware:
    - name: twobox-watchdog
      grpc_endpoint: https://${WATCHDOG_HOST}:${WATCHDOG_PORT}
      tls_ca_cert_path: /etc/twobox-watchdog/ca.crt
      max_payload_bytes: 4194304
      timeout: 500ms
server:
  extraVolumes:
    - name: twobox-watchdog-ca
      secret:
        secretName: twobox-watchdog-ca
  extraVolumeMounts:
    - name: twobox-watchdog-ca
      mountPath: /etc/twobox-watchdog
      readOnly: true
EOF
helm upgrade openshell oci://ghcr.io/nvidia/openshell/helm-chart \
  --version "${OPENSHELL_VERSION}" --namespace openshell \
  --reuse-values --values "${state}/values.yaml" --wait --timeout 5m >/dev/null
# Registration is read at gateway startup; a config-only change may not roll the pod.
kubectl -n openshell rollout restart statefulset/openshell >/dev/null
kubectl -n openshell rollout status statefulset/openshell --timeout=180s
for _ in $(seq 1 30); do
  openshell status 2>/dev/null | grep -q "Status: Connected" && break
  sleep 2
done
openshell gateway info | grep -A1 "twobox-watchdog"
echo "Watchdog log: ${state}/watchdog.jsonl"
