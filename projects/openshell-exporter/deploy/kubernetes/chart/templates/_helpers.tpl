{{- define "openshell-event-exporter.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end }}

{{- define "openshell-event-exporter.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name (include "openshell-event-exporter.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end }}

{{- define "openshell-event-exporter.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | quote }}
app.kubernetes.io/name: {{ include "openshell-event-exporter.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: openshell
{{- end }}

{{- define "openshell-event-exporter.selectorLabels" -}}
app.kubernetes.io/name: {{ include "openshell-event-exporter.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "openshell-event-exporter.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "openshell-event-exporter.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- required "serviceAccount.name is required when serviceAccount.create=false" .Values.serviceAccount.name -}}
{{- end -}}
{{- end }}

{{- define "openshell-event-exporter.image" -}}
{{- $repository := required "image.repository is required" .Values.image.repository -}}
{{- if .Values.image.local -}}
{{- if ne .Values.image.pullPolicy "Never" -}}{{ fail "local images require image.pullPolicy=Never" }}{{- end -}}
{{- if .Values.image.digest -}}{{ fail "local images require image.digest to be empty" }}{{- end -}}
{{- $tag := required "local images require image.tag" .Values.image.tag -}}
{{- if eq $tag "latest" -}}{{ fail "local images require an explicit version tag" }}{{- end -}}
{{- printf "%s:%s" $repository $tag -}}
{{- else -}}
{{- $digest := required "registry images require image.digest" .Values.image.digest -}}
{{- if not (regexMatch "^sha256:[0-9a-f]{64}$" $digest) -}}{{ fail "registry images require a valid sha256 digest" }}{{- end -}}
{{- printf "%s@%s" $repository $digest -}}
{{- end -}}
{{- end }}

{{- define "openshell-event-exporter.queueClaim" -}}
{{- default (printf "%s-queue" (include "openshell-event-exporter.fullname" .)) .Values.persistence.queue.existingClaim -}}
{{- end }}
{{- define "openshell-event-exporter.recoveryClaim" -}}
{{- default (printf "%s-recovery" (include "openshell-event-exporter.fullname" .)) .Values.persistence.recovery.existingClaim -}}
{{- end }}
{{- define "openshell-event-exporter.checkpointClaim" -}}
{{- default (printf "%s-checkpoints" (include "openshell-event-exporter.fullname" .)) .Values.persistence.checkpoints.existingClaim -}}
{{- end }}
{{- define "openshell-event-exporter.otlpGrpcQueueClaim" -}}
{{- default (printf "%s-otlp-grpc-queue" (include "openshell-event-exporter.fullname" .)) .Values.persistence.otlpGrpcQueue.existingClaim -}}
{{- end }}
{{- define "openshell-event-exporter.otlpHttpQueueClaim" -}}
{{- default (printf "%s-otlp-http-queue" (include "openshell-event-exporter.fullname" .)) .Values.persistence.otlpHttpQueue.existingClaim -}}
{{- end }}

{{/* Component selectors. stream and complete are presets; custom is explicit composition. */}}
{{- define "openshell-event-exporter.module.watchSandbox" -}}{{ if eq .Values.profile "custom" }}{{ .Values.modules.acquisition.watchSandbox }}{{ else }}true{{ end }}{{- end }}
{{- define "openshell-event-exporter.module.policyReconciliation" -}}{{ if eq .Values.profile "custom" }}{{ .Values.modules.acquisition.policyReconciliation }}{{ else }}true{{ end }}{{- end }}
{{- define "openshell-event-exporter.module.ocsfFiles" -}}{{ if eq .Values.profile "custom" }}{{ .Values.modules.acquisition.ocsfFiles }}{{ else if and (eq .Values.profile "complete") .Values.files.openshell.enabled }}true{{ else }}false{{ end }}{{- end }}
{{- define "openshell-event-exporter.module.sandboxFileLogs" -}}{{ if eq .Values.profile "custom" }}{{ .Values.modules.acquisition.sandboxFileLogs }}{{ else if and (eq .Values.profile "complete") .Values.files.openshell.enabled }}true{{ else }}false{{ end }}{{- end }}
{{- define "openshell-event-exporter.module.relayFileLogs" -}}{{ if eq .Values.profile "custom" }}{{ .Values.modules.acquisition.relayFileLogs }}{{ else if and (eq .Values.profile "complete") .Values.files.relay.enabled }}true{{ else }}false{{ end }}{{- end }}
{{- define "openshell-event-exporter.module.relayOtlp" -}}{{ if eq .Values.profile "custom" }}{{ .Values.modules.acquisition.relayOtlp }}{{ else if and (eq .Values.profile "complete") .Values.relay.input.enabled }}true{{ else }}false{{ end }}{{- end }}
{{- define "openshell-event-exporter.module.nativeOtlp" -}}{{ if eq .Values.profile "custom" }}{{ .Values.modules.acquisition.nativeOtlp }}{{ else if and (eq .Values.profile "complete") .Values.nativeOtlp.enabled }}true{{ else }}false{{ end }}{{- end }}
{{- define "openshell-event-exporter.module.forwardedSandboxFiles" -}}{{ if eq .Values.profile "custom" }}{{ .Values.modules.acquisition.forwardedSandboxFiles }}{{ else if and (eq .Values.profile "complete") .Values.forwardedOcsf.enabled }}true{{ else }}false{{ end }}{{- end }}
{{- define "openshell-event-exporter.module.kubernetesContext" -}}{{ if eq .Values.profile "custom" }}{{ .Values.modules.acquisition.kubernetesContext }}{{ else if and (eq .Values.profile "complete") .Values.kubernetesContext.enabled }}true{{ else }}false{{ end }}{{- end }}
{{- define "openshell-event-exporter.module.internalOtlp" -}}{{ if and (eq .Values.topology.role "central") .Values.modules.acquisition.internalOtlp }}true{{ else }}false{{ end }}{{- end }}
{{- define "openshell-event-exporter.edgeShardRouting" -}}{{ if and (eq .Values.topology.role "edge") .Values.topology.central.shardRouting.enabled }}true{{ else }}false{{ end }}{{- end }}
{{- define "openshell-event-exporter.centralStateful" -}}{{ if and (eq .Values.topology.role "central") .Values.topology.central.shardRouting.enabled }}true{{ else }}false{{ end }}{{- end }}
{{- define "openshell-event-exporter.module.cloudEvents" -}}{{ if eq .Values.profile "custom" }}{{ .Values.modules.delivery.cloudEvents }}{{ else }}true{{ end }}{{- end }}
{{- define "openshell-event-exporter.module.otlpGrpc" -}}{{ if eq .Values.profile "custom" }}{{ .Values.modules.delivery.otlpGrpc }}{{ else if eq .Values.profile "complete" }}true{{ else }}false{{ end }}{{- end }}
{{- define "openshell-event-exporter.module.otlpHttp" -}}{{ if eq .Values.profile "custom" }}{{ .Values.modules.delivery.otlpHttp }}{{ else if eq .Values.profile "complete" }}true{{ else }}false{{ end }}{{- end }}
{{- define "openshell-event-exporter.module.recovery" -}}{{ if eq .Values.profile "custom" }}{{ .Values.modules.delivery.recovery }}{{ else }}true{{ end }}{{- end }}
{{- define "openshell-event-exporter.module.hasLogs" -}}{{ if or (eq (include "openshell-event-exporter.module.watchSandbox" .) "true") (eq (include "openshell-event-exporter.module.ocsfFiles" .) "true") (eq (include "openshell-event-exporter.module.sandboxFileLogs" .) "true") (eq (include "openshell-event-exporter.module.relayFileLogs" .) "true") (eq (include "openshell-event-exporter.module.forwardedSandboxFiles" .) "true") (eq (include "openshell-event-exporter.module.kubernetesContext" .) "true") }}true{{ else }}false{{ end }}{{- end }}
{{- define "openshell-event-exporter.module.checkpoints" -}}{{ if or (eq (include "openshell-event-exporter.module.policyReconciliation" .) "true") (eq (include "openshell-event-exporter.module.ocsfFiles" .) "true") (eq (include "openshell-event-exporter.module.sandboxFileLogs" .) "true") (eq (include "openshell-event-exporter.module.relayFileLogs" .) "true") (eq (include "openshell-event-exporter.module.kubernetesContext" .) "true") }}true{{ else }}false{{ end }}{{- end }}
{{- define "openshell-event-exporter.logExporters" -}}{{- $items := list -}}{{- if eq (include "openshell-event-exporter.edgeShardRouting" .) "true" -}}{{- $items = append $items "load_balancing/internal" -}}{{- else if eq (include "openshell-event-exporter.module.otlpGrpc" .) "true" -}}{{- $items = append $items "otlp" -}}{{- end -}}{{- if eq (include "openshell-event-exporter.module.otlpHttp" .) "true" -}}{{- $items = append $items "otlphttp" -}}{{- end -}}{{- if eq (include "openshell-event-exporter.module.cloudEvents" .) "true" -}}{{- $items = append $items "cloudevents" -}}{{- end -}}{{- if eq (include "openshell-event-exporter.module.recovery" .) "true" -}}{{- $items = append $items "file/recovery" -}}{{- end -}}{{ join ", " $items }}{{- end }}
{{- define "openshell-event-exporter.traceExporters" -}}{{- $items := list -}}{{- if eq (include "openshell-event-exporter.edgeShardRouting" .) "true" -}}{{- $items = append $items "load_balancing/internal" -}}{{- else if eq (include "openshell-event-exporter.module.otlpGrpc" .) "true" -}}{{- $items = append $items "otlp" -}}{{- end -}}{{- if eq (include "openshell-event-exporter.module.otlpHttp" .) "true" -}}{{- $items = append $items "otlphttp" -}}{{- end -}}{{ join ", " $items }}{{- end }}

{{- define "openshell-event-exporter.validateModules" -}}
{{- if not (has .Values.topology.role (list "standalone" "edge" "central")) -}}{{ fail "topology.role must be standalone, edge, or central" }}{{- end -}}
{{- if and (ne .Values.topology.role "standalone") (not (regexMatch "^[a-z0-9][a-z0-9._-]{0,62}$" .Values.topology.tenantId)) -}}{{ fail "edge and central roles require a bounded topology.tenantId" }}{{- end -}}
{{- $rawAcquisition := or .Values.modules.acquisition.watchSandbox .Values.modules.acquisition.ocsfFiles .Values.modules.acquisition.sandboxFileLogs .Values.modules.acquisition.relayFileLogs .Values.modules.acquisition.relayOtlp .Values.modules.acquisition.nativeOtlp .Values.modules.acquisition.forwardedSandboxFiles .Values.modules.acquisition.kubernetesContext -}}
{{- if eq .Values.topology.role "edge" -}}
  {{- if ne .Values.profile "custom" -}}{{ fail "topology.role=edge requires profile=custom" }}{{- end -}}
  {{- if not $rawAcquisition -}}{{ fail "edge role requires at least one raw acquisition module" }}{{- end -}}
  {{- if .Values.modules.acquisition.internalOtlp -}}{{ fail "edge role cannot enable internalOtlp acquisition" }}{{- end -}}
  {{- if or .Values.modules.delivery.cloudEvents .Values.modules.delivery.recovery -}}{{ fail "edge role forwards only through durable OTLP; CloudEvents and recovery belong to standalone or central" }}{{- end -}}
  {{- if not (or .Values.modules.delivery.otlpGrpc .Values.modules.delivery.otlpHttp) -}}{{ fail "edge role requires an OTLP delivery module" }}{{- end -}}
  {{- if .Values.topology.central.shardRouting.enabled -}}
    {{- if not .Values.modules.delivery.otlpGrpc -}}{{ fail "edge shard routing requires OTLP gRPC delivery" }}{{- end -}}
    {{- if .Values.modules.delivery.otlpHttp -}}{{ fail "edge shard routing does not support simultaneous OTLP HTTP delivery" }}{{- end -}}
    {{- if not .Values.destination.otlp.mtlsEnabled -}}{{ fail "edge shard routing requires destination OTLP mTLS" }}{{- end -}}
    {{- if not .Values.networkPolicy.enabled -}}{{ fail "edge shard routing requires networkPolicy.enabled=true" }}{{- end -}}
    {{- if not .Values.topology.central.shardRouting.hostname -}}{{ fail "edge shard routing requires a central headless-service hostname" }}{{- end -}}
  {{- end -}}
{{- end -}}
{{- if eq .Values.topology.role "central" -}}
  {{- if ne .Values.profile "custom" -}}{{ fail "topology.role=central requires profile=custom" }}{{- end -}}
  {{- if $rawAcquisition -}}{{ fail "central role accepts only internalOtlp; raw acquisition belongs to edge or standalone" }}{{- end -}}
  {{- if not .Values.modules.acquisition.internalOtlp -}}{{ fail "central role requires modules.acquisition.internalOtlp=true" }}{{- end -}}
  {{- if not .Values.internalOtlp.enabled -}}{{ fail "central role requires internalOtlp.enabled=true" }}{{- end -}}
  {{- if not .Values.internalOtlp.mtlsEnabled -}}{{ fail "central internal OTLP requires mTLS" }}{{- end -}}
  {{- if not .Values.networkPolicy.enabled -}}{{ fail "central internal OTLP requires networkPolicy.enabled=true" }}{{- end -}}
  {{- if or (lt (int .Values.topology.central.replicas) 1) (gt (int .Values.topology.central.replicas) 32) -}}{{ fail "topology.central.replicas must be between 1 and 32" }}{{- end -}}
  {{- if and (gt (int .Values.topology.central.replicas) 1) (not .Values.topology.central.shardRouting.enabled) -}}{{ fail "central replicas greater than one require shardRouting.enabled=true" }}{{- end -}}
  {{- if .Values.topology.central.shardRouting.enabled -}}
    {{- if lt (int .Values.topology.central.replicas) 2 -}}{{ fail "central shard routing requires at least two replicas" }}{{- end -}}
    {{- if or .Values.persistence.queue.existingClaim .Values.persistence.recovery.existingClaim .Values.persistence.otlpGrpcQueue.existingClaim .Values.persistence.otlpHttpQueue.existingClaim -}}{{ fail "scaled central owns one persistent queue and recovery claim per replica; existing shared claims are forbidden" }}{{- end -}}
  {{- end -}}
{{- else if .Values.modules.acquisition.internalOtlp -}}
  {{- fail "internalOtlp acquisition is valid only for topology.role=central" -}}
{{- end -}}
{{- if and .Values.sandboxInjection.enabled (ne (include "openshell-event-exporter.module.forwardedSandboxFiles" .) "true") -}}{{ fail "sandboxInjection.enabled requires the forwardedSandboxFiles acquisition module" }}{{- end -}}
{{- if eq .Values.profile "custom" -}}
  {{- $hasLogs := or .Values.modules.acquisition.watchSandbox .Values.modules.acquisition.ocsfFiles .Values.modules.acquisition.sandboxFileLogs .Values.modules.acquisition.relayFileLogs .Values.modules.acquisition.forwardedSandboxFiles .Values.modules.acquisition.kubernetesContext .Values.modules.acquisition.internalOtlp -}}
  {{- $hasTraces := or .Values.modules.acquisition.relayOtlp .Values.modules.acquisition.nativeOtlp .Values.modules.acquisition.internalOtlp -}}
  {{- if and .Values.modules.acquisition.policyReconciliation (not .Values.modules.acquisition.watchSandbox) -}}{{ fail "modules.acquisition.policyReconciliation requires watchSandbox" }}{{- end -}}
  {{- if and (or .Values.modules.acquisition.ocsfFiles .Values.modules.acquisition.sandboxFileLogs) (not .Values.files.openshell.enabled) -}}{{ fail "custom OCSF or sandbox file modules require files.openshell.enabled=true" }}{{- end -}}
  {{- if and .Values.modules.acquisition.relayFileLogs (not .Values.files.relay.enabled) -}}{{ fail "modules.acquisition.relayFileLogs requires files.relay.enabled=true" }}{{- end -}}
  {{- if and .Values.modules.acquisition.relayOtlp (not .Values.relay.input.enabled) -}}{{ fail "modules.acquisition.relayOtlp requires relay.input.enabled=true" }}{{- end -}}
  {{- if and .Values.modules.acquisition.nativeOtlp (not .Values.nativeOtlp.enabled) -}}{{ fail "modules.acquisition.nativeOtlp requires nativeOtlp.enabled=true" }}{{- end -}}
  {{- if and .Values.modules.acquisition.forwardedSandboxFiles (not .Values.forwardedOcsf.enabled) -}}{{ fail "modules.acquisition.forwardedSandboxFiles requires forwardedOcsf.enabled=true" }}{{- end -}}
  {{- if and (or .Values.modules.acquisition.relayOtlp .Values.modules.acquisition.nativeOtlp .Values.modules.acquisition.forwardedSandboxFiles) (not .Values.networkPolicy.enabled) -}}{{ fail "custom inbound OTLP modules require networkPolicy.enabled=true" }}{{- end -}}
  {{- if and .Values.modules.acquisition.kubernetesContext (not .Values.kubernetesContext.enabled) -}}{{ fail "modules.acquisition.kubernetesContext requires kubernetesContext.enabled=true" }}{{- end -}}
  {{- if and .Values.files.openshell.enabled (not (or .Values.modules.acquisition.ocsfFiles .Values.modules.acquisition.sandboxFileLogs)) -}}{{ fail "files.openshell.enabled requires ocsfFiles or sandboxFileLogs" }}{{- end -}}
  {{- if and .Values.files.relay.enabled (not .Values.modules.acquisition.relayFileLogs) -}}{{ fail "files.relay.enabled requires relayFileLogs" }}{{- end -}}
  {{- if and .Values.relay.input.enabled (not .Values.modules.acquisition.relayOtlp) -}}{{ fail "relay.input.enabled requires relayOtlp" }}{{- end -}}
  {{- if and .Values.nativeOtlp.enabled (not .Values.modules.acquisition.nativeOtlp) -}}{{ fail "nativeOtlp.enabled requires the nativeOtlp acquisition module" }}{{- end -}}
  {{- if and .Values.forwardedOcsf.enabled (not .Values.modules.acquisition.forwardedSandboxFiles) -}}{{ fail "forwardedOcsf.enabled requires forwardedSandboxFiles" }}{{- end -}}
  {{- if and .Values.kubernetesContext.enabled (not .Values.modules.acquisition.kubernetesContext) -}}{{ fail "kubernetesContext.enabled requires the kubernetesContext acquisition module" }}{{- end -}}
  {{- if and (or .Values.modules.delivery.cloudEvents .Values.modules.delivery.recovery) (not $hasLogs) -}}{{ fail "CloudEvents and recovery delivery require at least one log acquisition module" }}{{- end -}}
  {{- if not (or .Values.modules.acquisition.watchSandbox .Values.modules.acquisition.ocsfFiles .Values.modules.acquisition.sandboxFileLogs .Values.modules.acquisition.relayFileLogs .Values.modules.acquisition.relayOtlp .Values.modules.acquisition.nativeOtlp .Values.modules.acquisition.forwardedSandboxFiles .Values.modules.acquisition.kubernetesContext .Values.modules.acquisition.internalOtlp) -}}{{ fail "custom profile must enable at least one acquisition module" }}{{- end -}}
  {{- if not (or .Values.modules.delivery.cloudEvents .Values.modules.delivery.otlpGrpc .Values.modules.delivery.otlpHttp .Values.modules.delivery.recovery) -}}{{ fail "custom profile must enable at least one delivery module" }}{{- end -}}
  {{- if and $hasTraces (not (or .Values.modules.delivery.otlpGrpc .Values.modules.delivery.otlpHttp)) -}}{{ fail "trace acquisition requires at least one OTLP delivery module" }}{{- end -}}
{{- end -}}
{{- end }}

{{/* Render Collector YAML while keeping every checked-in config valid standalone. */}}
{{- define "openshell-event-exporter.renderConfig" -}}
{{- include "openshell-event-exporter.validateModules" . -}}
{{- if eq .Values.topology.role "central" -}}
{{- tpl (.Files.Get "files/config-central.yaml") . -}}
{{- else -}}
{{- $configProfile := ternary "complete" .Values.profile (eq .Values.profile "custom") -}}
{{- $configFile := printf "files/config-%s.yaml" $configProfile -}}
{{- $config := .Files.Get $configFile -}}
{{- if and (eq (include "openshell-event-exporter.module.relayOtlp" .) "true") (not .Values.relay.input.mtlsEnabled) -}}
{{- $config = replace "          client_ca_file_reload: true\n          client_ca_file: ${env:NEMO_RELAY_OTLP_CLIENT_CA_FILE}\n" "" $config -}}
{{- $config = replace "          client_ca_file: ${env:NEMO_RELAY_OTLP_CLIENT_CA_FILE}\n" "" $config -}}
{{- end -}}
{{- tpl $config . -}}
{{- end -}}
{{- end -}}
