#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
resolve_tools
for name in kibana grafana elasticsearch health metrics; do
  file="$RUNTIME_DIR/$name.pid"
  [[ -r "$file" ]] || continue
  pid=$(cat "$file")
  if [[ "$pid" =~ ^[0-9]+$ ]] && [[ -r "/proc/$pid/cmdline" ]] && tr '\0' ' ' <"/proc/$pid/cmdline" | grep -q 'port-forward'; then
    kill "$pid" 2>/dev/null || true
  fi
  rm -f "$file"
done
if [[ ${1:-} == --uninstall ]]; then
  note "uninstalling demo workloads; retained evidence and Elastic PVCs remain"
  h uninstall openshell-exporter -n "$OBS_NAMESPACE" --ignore-not-found --wait
  h uninstall openshell -n "$GATEWAY_NAMESPACE" --ignore-not-found --wait
  k delete -f "$DEMO_DIR/manifests/elastic-soc.yaml" --ignore-not-found >/dev/null
  k delete -f "$DEMO_DIR/manifests/demo-policy.yaml" --ignore-not-found >/dev/null
else
  note "stopped loopback forwards; workloads and evidence remain running"
fi
