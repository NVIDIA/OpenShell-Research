#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Shared control-plane helpers for the manual sandbox demo commands. Callers
# must enable strict mode before sourcing this file.

source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
[[ -r "$RUNTIME_DIR/images.env" ]] || fail "run ./build-images.sh or ./run.sh first"
# shellcheck disable=SC1091
source "$RUNTIME_DIR/images.env"
resolve_tools

CONTROL_POD=openshell-demo-control
MAX_SANDBOX_NAME_LENGTH=19
SANDBOX_IMAGE="docker.io/local/openshell-sandbox@${SANDBOX_DIGEST}"

validate_sandbox_name() {
  local name=$1
  [[ "$name" =~ ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$ ]] \
    || fail "sandbox name must be a lowercase DNS label"
  ((${#name} <= MAX_SANDBOX_NAME_LENGTH)) || fail "OpenShell sandbox names contain at most $MAX_SANDBOX_NAME_LENGTH characters"
}

start_sandbox_control() {
  local existing_owner
  if k -n "$GATEWAY_NAMESPACE" get pod "$CONTROL_POD" >/dev/null 2>&1; then
    existing_owner=$(k -n "$GATEWAY_NAMESPACE" get pod "$CONTROL_POD" \
      -o jsonpath='{.metadata.labels.app\.kubernetes\.io/part-of}')
    [[ "$existing_owner" == openshell-kubernetes-demo ]] \
      || fail "refusing to replace unowned pod $GATEWAY_NAMESPACE/$CONTROL_POD"
    k -n "$GATEWAY_NAMESPACE" delete pod "$CONTROL_POD" --wait=true >/dev/null
  fi

  cat >"$RUNTIME_DIR/sandbox-control-pod.yaml" <<YAML
apiVersion: v1
kind: Pod
metadata:
  name: $CONTROL_POD
  namespace: $GATEWAY_NAMESPACE
  labels:
    app.kubernetes.io/part-of: openshell-kubernetes-demo
    app.kubernetes.io/component: manual-sandbox-control
spec:
  restartPolicy: Never
  automountServiceAccountToken: false
  containers:
    - name: control
      image: docker.io/local/openshell-control:kubernetes-demo
      imagePullPolicy: Never
      command: [/bin/sh, -c, "trap : TERM INT; sleep infinity & wait"]
      env:
        - name: NVIDIA_API_KEY
          valueFrom: {secretKeyRef: {name: openshell-demo-nvidia-api, key: NVIDIA_API_KEY}}
      securityContext:
        allowPrivilegeEscalation: false
        capabilities: {drop: [ALL]}
      resources:
        requests: {cpu: 25m, memory: 32Mi}
        limits: {cpu: 500m, memory: 256Mi}
      volumeMounts:
        - {name: gateway-tls, mountPath: /run/gateway-tls, readOnly: true}
        - {name: task, mountPath: /demo, readOnly: true}
        - {name: config, mountPath: /root/.config}
  volumes:
    - {name: gateway-tls, secret: {secretName: openshell-client-tls}}
    - {name: task, configMap: {name: openshell-demo-task}}
    - {name: config, emptyDir: {sizeLimit: 8Mi}}
YAML
  k apply -f "$RUNTIME_DIR/sandbox-control-pod.yaml" >/dev/null
  k -n "$GATEWAY_NAMESPACE" wait --for=condition=Ready "pod/$CONTROL_POD" --timeout=2m >/dev/null

  k -n "$GATEWAY_NAMESPACE" exec "$CONTROL_POD" -- /bin/sh -ec '
    mkdir -p /root/.config/openshell/gateways/kubernetes-demo/mtls
    cp /run/gateway-tls/ca.crt /root/.config/openshell/gateways/kubernetes-demo/mtls/ca.crt
    cp /run/gateway-tls/tls.crt /root/.config/openshell/gateways/kubernetes-demo/mtls/tls.crt
    cp /run/gateway-tls/tls.key /root/.config/openshell/gateways/kubernetes-demo/mtls/tls.key
    chmod 0600 /root/.config/openshell/gateways/kubernetes-demo/mtls/*
    openshell gateway add https://openshell.openshell.svc.cluster.local:8080 --local --name kubernetes-demo >/dev/null
    export OPENSHELL_GATEWAY=kubernetes-demo
    openshell settings set --global --yes --key ocsf_json_enabled --value true >/dev/null
    openshell settings set --global --yes --key providers_v2_enabled --value true >/dev/null
    openshell provider profile export openshell-relay-otlp -o yaml >/dev/null
    openshell provider get openshell-relay-otlp-provider-v2 >/dev/null
    if openshell provider get nvidia-provider-v2 >/dev/null 2>&1; then
      openshell provider update nvidia-provider-v2 --from-existing >/dev/null
    else
      openshell provider create --name nvidia-provider-v2 --type nvidia --from-existing >/dev/null
    fi
    openshell inference set --provider nvidia-provider-v2 --model nvidia/nemotron-3-super-120b-a12b --timeout 300 >/dev/null
  '
}

stop_sandbox_control() {
  local existing_owner
  if ! k -n "$GATEWAY_NAMESPACE" get pod "$CONTROL_POD" >/dev/null 2>&1; then
    return
  fi
  existing_owner=$(k -n "$GATEWAY_NAMESPACE" get pod "$CONTROL_POD" \
    -o jsonpath='{.metadata.labels.app\.kubernetes\.io/part-of}')
  if [[ "$existing_owner" == openshell-kubernetes-demo ]]; then
    k -n "$GATEWAY_NAMESPACE" delete pod "$CONTROL_POD" --wait=false >/dev/null
  fi
}

control_openshell() {
  k -n "$GATEWAY_NAMESPACE" exec "$CONTROL_POD" -- env OPENSHELL_GATEWAY=kubernetes-demo openshell "$@"
}

create_demo_sandbox() {
  local name=$1 scenario=$2
  validate_sandbox_name "$name"
  if control_openshell sandbox get "$name" -o json >/dev/null 2>&1; then
    fail "sandbox $name already exists; no existing sandbox was changed"
  fi
  if ! control_openshell sandbox create --name "$name" \
      --from "$SANDBOX_IMAGE" \
      --policy /demo/policy.yaml \
      --label demo=openshell-event-exporter \
      --label "scenario=$scenario" \
      --provider openshell-relay-otlp-provider-v2 \
      --no-auto-providers --no-tty -- /bin/true >/dev/null; then
    fail "failed to create sandbox $name; no sandbox metadata was requested"
  fi
  control_openshell sandbox get "$name" -o json
}
