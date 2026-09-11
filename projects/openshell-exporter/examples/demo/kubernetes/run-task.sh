#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
[[ -r "$RUNTIME_DIR/images.env" ]] || fail "run build-images.sh first"
# shellcheck disable=SC1091
source "$RUNTIME_DIR/images.env"
resolve_tools

cleanup_relay_provider_secret() {
  k -n "$GATEWAY_NAMESPACE" delete secret openshell-demo-relay-provider --ignore-not-found >/dev/null 2>&1 || true
}
trap cleanup_relay_provider_secret EXIT

relay_token=$(k -n "$OBS_NAMESPACE" get secret openshell-exporter-relay-input-auth -o jsonpath='{.data.token}' | base64 --decode)
k -n "$GATEWAY_NAMESPACE" create secret generic openshell-demo-relay-provider \
  --from-literal=token="$relay_token" --dry-run=client -o yaml | k apply -f - >/dev/null
unset relay_token

job=openshell-evidence-demo
k -n "$GATEWAY_NAMESPACE" delete job "$job" --ignore-not-found --wait=true >/dev/null
cat >"$RUNTIME_DIR/task-job.yaml" <<YAML
apiVersion: batch/v1
kind: Job
metadata:
  name: $job
  namespace: $GATEWAY_NAMESPACE
  labels:
    app.kubernetes.io/part-of: openshell-kubernetes-demo
    app.kubernetes.io/component: real-hermes-task
spec:
  backoffLimit: 0
  activeDeadlineSeconds: 600
  ttlSecondsAfterFinished: 3600
  template:
    metadata:
      labels:
        app.kubernetes.io/part-of: openshell-kubernetes-demo
    spec:
      restartPolicy: Never
      automountServiceAccountToken: false
      containers:
        - name: control
          image: docker.io/local/openshell-control:kubernetes-demo
          imagePullPolicy: Never
          env:
            - name: NVIDIA_API_KEY
              valueFrom: {secretKeyRef: {name: openshell-demo-nvidia-api, key: NVIDIA_API_KEY}}
          command: [/bin/sh, -ec]
          args:
            - |
              mkdir -p /root/.config/openshell/gateways/kubernetes-demo/mtls
              cp /run/gateway-tls/ca.crt /root/.config/openshell/gateways/kubernetes-demo/mtls/ca.crt
              cp /run/gateway-tls/tls.crt /root/.config/openshell/gateways/kubernetes-demo/mtls/tls.crt
              cp /run/gateway-tls/tls.key /root/.config/openshell/gateways/kubernetes-demo/mtls/tls.key
              chmod 0600 /root/.config/openshell/gateways/kubernetes-demo/mtls/*
              openshell gateway add https://openshell.openshell.svc.cluster.local:8080 --local --name kubernetes-demo >/dev/null
              export OPENSHELL_GATEWAY=kubernetes-demo
              openshell settings set --global --yes --key ocsf_json_enabled --value true >/dev/null
              openshell settings set --global --yes --key providers_v2_enabled --value true >/dev/null
              openshell settings set --global --yes --key agent_policy_proposals_enabled --value true >/dev/null
              openshell settings set --global --yes --key proposal_approval_mode --value manual >/dev/null
              if openshell provider profile export openshell-relay-otlp -o yaml >/tmp/relay-profile.yaml 2>/dev/null; then
                grep -F 'host: openshell-exporter-openshell-event-exporter.openshell-observability.svc.cluster.local' /tmp/relay-profile.yaml >/dev/null
                grep -F 'path: /v1/traces' /tmp/relay-profile.yaml >/dev/null
              else
                openshell provider profile lint -f /demo/relay-otlp-provider.yaml >/dev/null
                openshell provider profile import -f /demo/relay-otlp-provider.yaml >/dev/null
              fi
              export NEMO_RELAY_OTLP_TOKEN="\$(cat /run/relay-provider/token)"
              if openshell provider get openshell-relay-otlp-provider-v2 >/dev/null 2>&1; then
                openshell provider update openshell-relay-otlp-provider-v2 --credential NEMO_RELAY_OTLP_TOKEN >/dev/null
              else
                openshell provider create --name openshell-relay-otlp-provider-v2 --type openshell-relay-otlp --credential NEMO_RELAY_OTLP_TOKEN >/dev/null
              fi
              unset NEMO_RELAY_OTLP_TOKEN
              if openshell provider get nvidia-provider-v2 >/dev/null 2>&1; then
                openshell provider update nvidia-provider-v2 --from-existing >/dev/null
              else
                openshell provider create --name nvidia-provider-v2 --type nvidia --from-existing >/dev/null
              fi
              openshell inference set --provider nvidia-provider-v2 --model nvidia/nemotron-3-super-120b-a12b --timeout 300 >/dev/null
              if openshell sandbox get k8s-evidence-demo -o json >/tmp/existing.json 2>/dev/null; then
                owner=\$(sed -n 's/.*"demo"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' /tmp/existing.json | head -1)
                [ "\$owner" = openshell-event-exporter ] || { echo 'refusing to replace an unowned sandbox' >&2; exit 1; }
                openshell sandbox delete k8s-evidence-demo >/dev/null
              fi
              openshell sandbox create --name k8s-evidence-demo \
                --from docker.io/local/openshell-sandbox@${SANDBOX_DIGEST} \
                --policy /demo/policy.yaml \
                --label demo=openshell-event-exporter \
                --label scenario=kubernetes-hermes-relay-soc \
                --provider openshell-relay-otlp-provider-v2 \
                --no-auto-providers --no-tty -- /bin/true \
                >/tmp/create.log
              openshell sandbox get k8s-evidence-demo -o json >/tmp/sandbox.json
              sandbox_id=\$(sed -n 's/.*"id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' /tmp/sandbox.json | head -1)
              [ -n "\$sandbox_id" ] || { echo 'could not read authoritative sandbox ID' >&2; exit 1; }
              policy_version=\$(sed -n 's/.*"current_policy_version"[[:space:]]*:[[:space:]]*\([0-9][0-9]*\).*/\1/p' /tmp/sandbox.json | head -1)
              identity="--env HERMES_OPENSHELL_SANDBOX_ID=\$sandbox_id --env HERMES_OPENSHELL_SANDBOX_NAME=k8s-evidence-demo"
              if [ -n "\$policy_version" ]; then
                identity="\$identity --env HERMES_OPENSHELL_POLICY_VERSION=\$policy_version"
              fi
              prompt='Use the terminal tool to create /sandbox/showcase-agent-evidence.txt containing the non-secret text OpenShell Kubernetes SOC showcase. Report the operating system, current working directory, and five entries in /sandbox. Attempt curl --fail --silent --show-error https://example.com/ once; the OpenShell policy denial is expected, so continue. Then explain in two sentences why sandbox evidence belongs in a SOC.'
              # The identity values come from the authorized gateway response. The
              # option list is intentionally word-split after strict server-side ID validation.
              # shellcheck disable=SC2086
              openshell sandbox exec --name k8s-evidence-demo \$identity \
                --workdir /sandbox --timeout 300 --no-tty -- \
                hermes-correlated chat --query "\$prompt" --provider custom \
                --model nvidia/nemotron-3-super-120b-a12b 2>&1 | tee /tmp/hermes.log
              session_id=\$(sed -n 's/.*Session:[[:space:]]*\([A-Za-z0-9._:-][A-Za-z0-9._:-]*\).*/\1/p' /tmp/hermes.log | tail -1)
              [ -n "\$session_id" ] || { echo 'could not capture the Hermes session ID' >&2; exit 1; }
              proposal_b64=\$(base64 < /demo/draft-proposal.json | tr -d '\n')
              draft_identity="--env HERMES_OPENSHELL_SANDBOX_ID=\$sandbox_id --env DEMO_DRAFT_PROPOSAL_B64=\$proposal_b64"
              # Submit a deterministic, real agent-authored proposal through the
              # sandbox-local Policy Advisor API. OpenShell remains the policy
              # authority and manual approval keeps the chunk pending for SOC review.
              # shellcheck disable=SC2086
              openshell sandbox exec --name k8s-evidence-demo \$draft_identity \
                --workdir /sandbox --timeout 60 --no-tty -- /bin/sh -ec '
                  response=\$(printf "%s" "\$DEMO_DRAFT_PROPOSAL_B64" | base64 -d | curl --fail --silent --show-error \
                    --header "Content-Type: application/json" \
                    --data-binary @- http://policy.local/v1/proposals)
                  printf "%s" "\$response" | python3 -c "import json, sys; ids = json.load(sys.stdin).get(\"accepted_chunk_ids\", []); assert len(ids) == 1, \"expected exactly one accepted draft chunk\"; print(\"OPENSHELL_DEMO_DRAFT_CHUNK=\" + ids[0])"
                ' 2>&1 | tee /tmp/draft.log
              draft_chunk_id=\$(sed -n 's/^OPENSHELL_DEMO_DRAFT_CHUNK=//p' /tmp/draft.log | tail -1)
              [ -n "\$draft_chunk_id" ] || { echo 'could not capture the real OpenShell draft chunk ID' >&2; exit 1; }
              openshell rule get k8s-evidence-demo --status pending | grep -F "\$draft_chunk_id" >/dev/null \
                || { echo 'submitted draft chunk is not pending for review' >&2; exit 1; }
              echo OPENSHELL_DEMO_SANDBOX_JSON_BEGIN
              cat /tmp/sandbox.json
              echo OPENSHELL_DEMO_SANDBOX_JSON_END
              echo "OPENSHELL_DEMO_HERMES_SESSION=\$session_id"
              echo "OPENSHELL_DEMO_DRAFT_CHUNK=\$draft_chunk_id"
          securityContext:
            allowPrivilegeEscalation: false
            capabilities: {drop: [ALL]}
          resources:
            requests: {cpu: 25m, memory: 32Mi}
            limits: {cpu: 500m, memory: 256Mi}
          volumeMounts:
            - {name: relay-provider, mountPath: /run/relay-provider, readOnly: true}
            - {name: gateway-tls, mountPath: /run/gateway-tls, readOnly: true}
            - {name: task, mountPath: /demo, readOnly: true}
            - {name: config, mountPath: /root/.config}
      volumes:
        - {name: relay-provider, secret: {secretName: openshell-demo-relay-provider, items: [{key: token, path: token}]}}
        - {name: gateway-tls, secret: {secretName: openshell-client-tls}}
        - {name: task, configMap: {name: openshell-demo-task}}
        - {name: config, emptyDir: {sizeLimit: 8Mi}}
YAML
k apply -f "$RUNTIME_DIR/task-job.yaml" >/dev/null
k -n "$GATEWAY_NAMESPACE" wait --for=condition=Complete "job/$job" --timeout=10m >/dev/null || {
  k -n "$GATEWAY_NAMESPACE" logs "job/$job" >&2 || true
  fail "real Hermes/OpenShell sandbox task did not complete"
}
k -n "$GATEWAY_NAMESPACE" logs "job/$job" >"$RUNTIME_DIR/task.log"
sed -n '/OPENSHELL_DEMO_SANDBOX_JSON_BEGIN/,/OPENSHELL_DEMO_SANDBOX_JSON_END/p' "$RUNTIME_DIR/task.log" | sed '1d;$d' >"$RUNTIME_DIR/sandbox.json"
SANDBOX_ID=$(jq -er '.metadata.id // .id // .sandbox.metadata.id' "$RUNTIME_DIR/sandbox.json")
HERMES_SESSION_ID=$(sed -n 's/^OPENSHELL_DEMO_HERMES_SESSION=//p' "$RUNTIME_DIR/task.log" | tail -1)
DRAFT_CHUNK_ID=$(sed -n 's/^OPENSHELL_DEMO_DRAFT_CHUNK=//p' "$RUNTIME_DIR/task.log" | tail -1)
[[ "$HERMES_SESSION_ID" =~ ^[A-Za-z0-9._:-]{1,256}$ ]] || fail "Hermes session ID is invalid"
[[ "$DRAFT_CHUNK_ID" =~ ^[A-Za-z0-9._:-]{1,256}$ ]] || fail "OpenShell draft chunk ID is invalid"
printf 'SANDBOX_ID=%s\nHERMES_SESSION_ID=%s\nDRAFT_CHUNK_ID=%s\n' "$SANDBOX_ID" "$HERMES_SESSION_ID" "$DRAFT_CHUNK_ID" >"$RUNTIME_DIR/task.env"
chmod 0600 "$RUNTIME_DIR/task.env"
note "real OpenShell sandbox $SANDBOX_ID completed Hermes session $HERMES_SESSION_ID and created pending draft chunk $DRAFT_CHUNK_ID"
