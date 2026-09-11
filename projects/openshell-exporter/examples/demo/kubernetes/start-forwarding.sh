#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
resolve_tools

stop_owned() {
  local file=$1 pid
  [[ -r "$file" ]] || return 0
  pid=$(cat "$file")
  if [[ "$pid" =~ ^[0-9]+$ ]] && [[ -r "/proc/$pid/cmdline" ]] && tr '\0' ' ' <"/proc/$pid/cmdline" | grep -q 'port-forward'; then
    kill "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
  fi
  rm -f "$file"
}
start_one() {
  local name=$1 namespace=$2 target=$3 ports=$4
  stop_owned "$RUNTIME_DIR/$name.pid"
  "$KUBECTL_BIN" --context "$CONTEXT" -n "$namespace" port-forward --address 127.0.0.1 "$target" "$ports" >"$RUNTIME_DIR/$name.log" 2>&1 &
  printf '%s\n' "$!" >"$RUNTIME_DIR/$name.pid"
}

exporter_service=$(k -n "$OBS_NAMESPACE" get service -l app.kubernetes.io/instance=openshell-exporter -o json | jq -r '.items[] | select(any(.spec.ports[]; .port == 13133)) | .metadata.name' | head -1)
[[ -n "$exporter_service" ]] || fail "exporter service was not found"
start_one kibana "$OBS_NAMESPACE" service/kibana 15601:5601
start_one grafana "$OBS_NAMESPACE" service/grafana 13000:3000
start_one elasticsearch "$OBS_NAMESPACE" service/elasticsearch 19200:9200
start_one health "$OBS_NAMESPACE" "service/$exporter_service" 18133:13133
start_one metrics "$OBS_NAMESPACE" "service/$exporter_service" 18888:8888
k -n "$OBS_NAMESPACE" get secret openshell-elastic-kibana -o jsonpath='{.data.ca\.crt}' | base64 --decode >"$RUNTIME_DIR/elastic-ca.crt"
chmod 0600 "$RUNTIME_DIR/elastic-ca.crt"
for attempt in $(seq 1 60); do
  if curl --fail --silent --show-error --cacert "$RUNTIME_DIR/elastic-ca.crt" https://127.0.0.1:15601/api/status >/dev/null 2>&1 \
    && curl --fail --silent --show-error http://127.0.0.1:13000/api/health >/dev/null 2>&1 \
    && curl --fail --silent --show-error http://127.0.0.1:18133/ >/dev/null 2>&1 \
    && curl --fail --silent --show-error http://127.0.0.1:18888/metrics >/dev/null 2>&1; then
    note "loopback Kibana, Grafana, exporter health, and metrics forwards are ready"
    exit 0
  fi
  sleep 2
done
fail "port forwards did not become ready; inspect $RUNTIME_DIR/*.log"
