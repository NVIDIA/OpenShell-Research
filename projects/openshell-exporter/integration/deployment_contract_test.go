// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package integration

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"go.yaml.in/yaml/v3"
)

func deploymentPath(name string) string {
	return filepath.Join("..", "deploy", filepath.Clean(name))
}

func readFile(t *testing.T, name string) string {
	t.Helper()
	path := deploymentPath(name)
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	return string(data)
}

func parseYAML(t *testing.T, name string) map[string]any {
	t.Helper()
	var document map[string]any
	if err := yaml.Unmarshal([]byte(readFile(t, name)), &document); err != nil {
		t.Fatalf("parse %s: %v", name, err)
	}
	return document
}

func parseRepositoryYAML(t *testing.T, name string) map[string]any {
	t.Helper()
	path := filepath.Join("..", filepath.Clean(name))
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	var document map[string]any
	if err := yaml.Unmarshal(data, &document); err != nil {
		t.Fatalf("parse %s: %v", name, err)
	}
	return document
}

func nestedMap(t *testing.T, value map[string]any, keys ...string) map[string]any {
	t.Helper()
	current := value
	for _, key := range keys {
		next, ok := current[key].(map[string]any)
		if !ok {
			t.Fatalf("%s is not a map in %v", key, keys)
		}
		current = next
	}
	return current
}

func TestDeploymentMatrixIsDiscoverable(t *testing.T) {
	command := readFile(t, "deploy.sh")
	readme := readFile(t, "README.md")
	for _, target := range []string{"docker", "podman", "kubernetes"} {
		if !strings.Contains(command, target) {
			t.Errorf("deploy.sh does not route target %q", target)
		}
		if !strings.Contains(readme, target) {
			t.Errorf("deployment README does not explain target %q", target)
		}
	}
	for _, unsupported := range []string{"local-gateway", "microvm"} {
		if strings.Contains(command, unsupported) {
			t.Errorf("deploy.sh still routes deferred target %q", unsupported)
		}
	}
}

func TestDeploymentRootContainsOnlyMethods(t *testing.T) {
	t.Parallel()
	entries, err := os.ReadDir(deploymentPath("."))
	if err != nil {
		t.Fatal(err)
	}
	allowed := map[string]bool{
		"README.md": true, "deploy.sh": true, "docker": true, "podman": true,
		"kubernetes": true,
	}
	for _, entry := range entries {
		if !allowed[entry.Name()] {
			t.Errorf("deploy root contains non-deployment entry %q", entry.Name())
		}
	}
}

func TestCollectorProfilesDescribeRealVisibility(t *testing.T) {
	full := parseYAML(t, "docker/config.yaml")
	fullReceivers := nestedMap(t, full, "receivers")
	for _, receiver := range []string{
		"file_log/openshell_ocsf",
		"file_log/openshell_sandbox",
		"watchsandbox",
	} {
		if _, ok := fullReceivers[receiver]; !ok {
			t.Errorf("full profile is missing %s", receiver)
		}
	}

	stream := parseYAML(t, "kubernetes/chart/files/config-stream.yaml")
	streamReceivers := nestedMap(t, stream, "receivers")
	if len(streamReceivers) != 1 {
		t.Fatalf("stream profile has %d receivers, want only WatchSandbox", len(streamReceivers))
	}
	if _, ok := streamReceivers["watchsandbox"]; !ok {
		t.Fatal("stream profile is missing WatchSandbox")
	}
	if strings.Contains(readFile(t, "kubernetes/chart/files/config-stream.yaml"), "ocsf.file") {
		t.Fatal("stream profile claims the unavailable OCSF file capability")
	}
}

func TestModularHelmProfileDeclaresSafeComposition(t *testing.T) {
	values := parseYAML(t, "kubernetes/chart/values.yaml")
	modules := nestedMap(t, values, "modules")
	acquisition := nestedMap(t, modules, "acquisition")
	delivery := nestedMap(t, modules, "delivery")

	for _, name := range []string{
		"watchSandbox", "policyReconciliation", "ocsfFiles", "sandboxFileLogs",
		"relayFileLogs", "relayOtlp", "nativeOtlp", "forwardedSandboxFiles",
		"kubernetesContext",
	} {
		if _, ok := acquisition[name].(bool); !ok {
			t.Errorf("acquisition module %q is not an explicit boolean", name)
		}
	}
	for _, name := range []string{"cloudEvents", "otlpGrpc", "otlpHttp", "recovery"} {
		if _, ok := delivery[name].(bool); !ok {
			t.Errorf("delivery module %q is not an explicit boolean", name)
		}
	}

	helpers := readFile(t, "kubernetes/chart/templates/_helpers.tpl")
	for _, invariant := range []string{
		"policyReconciliation requires watchSandbox",
		"trace acquisition requires at least one OTLP delivery module",
		"sandboxInjection.enabled requires the forwardedSandboxFiles acquisition module",
		"custom inbound OTLP modules require networkPolicy.enabled=true",
		"CloudEvents and recovery delivery require at least one log acquisition module",
		"custom profile must enable at least one acquisition module",
		"custom profile must enable at least one delivery module",
	} {
		if !strings.Contains(helpers, invariant) {
			t.Errorf("module validation is missing %q", invariant)
		}
	}

	config := readFile(t, "kubernetes/chart/files/config-complete.yaml")
	for _, safetyStage := range []string{
		"memory_limiter, openshell",
		"resource/nemo_relay, relay/nemo_relay",
		"resource/openshell_native, relay/openshell_native",
	} {
		if !strings.Contains(config, safetyStage) {
			t.Errorf("modular config can bypass required processing stage %q", safetyStage)
		}
	}
}

func TestCustomForwarderExampleIsFocusedAndFailClosed(t *testing.T) {
	values := parseYAML(t, "kubernetes/chart/values.custom.forwarder.example.yaml")
	if values["profile"] != "custom" {
		t.Fatalf("profile=%v, want custom", values["profile"])
	}
	modules := nestedMap(t, values, "modules")
	acquisition := nestedMap(t, modules, "acquisition")
	for name, enabled := range acquisition {
		want := name == "forwardedSandboxFiles"
		if enabled != want {
			t.Errorf("acquisition module %q=%v, want %v", name, enabled, want)
		}
	}
	for _, block := range []string{"forwardedOcsf", "sandboxInjection", "certManager", "networkPolicy"} {
		if nestedMap(t, values, block)["enabled"] != true {
			t.Errorf("%s.enabled must be true", block)
		}
	}
	delivery := nestedMap(t, modules, "delivery")
	if delivery["cloudEvents"] != true || delivery["recovery"] != true ||
		delivery["otlpGrpc"] != false || delivery["otlpHttp"] != false {
		t.Fatalf("unexpected delivery modules: %#v", delivery)
	}
}

func TestMethodConfigsReuseCheckedCollectorProfiles(t *testing.T) {
	for _, profile := range []string{"config.yaml", "config.full.yaml", "config.complete.yaml"} {
		if got, want := readFile(t, "podman/"+profile), readFile(t, "docker/"+profile); got != want {
			t.Fatalf("Podman %s drifted from Docker portable profile", profile)
		}
	}
}

func TestPortableFileProfilesBackpressureBeforeCheckpointedReplay(t *testing.T) {
	t.Parallel()
	for _, profile := range []string{"docker/config.yaml", "docker/config.full.yaml", "docker/config.complete.yaml"} {
		t.Run(profile, func(t *testing.T) {
			document := parseYAML(t, profile)
			receivers := nestedMap(t, document, "receivers")
			for name, raw := range receivers {
				if !strings.HasPrefix(name, "file_log/") {
					continue
				}
				receiver, ok := raw.(map[string]any)
				if !ok {
					t.Fatalf("receiver %s is not a map", name)
				}
				retry, ok := receiver["retry_on_failure"].(map[string]any)
				if !ok || retry["enabled"] != true || retry["max_elapsed_time"] != "0s" {
					t.Errorf("receiver %s must retry downstream admission indefinitely: %v", name, retry)
				}
			}

			pipelines := nestedMap(t, document, "service", "pipelines")
			exporters := nestedMap(t, document, "exporters")
			filePipelines := 0
			for pipelineName, raw := range pipelines {
				pipeline, ok := raw.(map[string]any)
				if !ok {
					t.Fatalf("pipeline %s is not a map", pipelineName)
				}
				pipelineReceivers, ok := pipeline["receivers"].([]any)
				if !ok || !containsPrefixedString(pipelineReceivers, "file_log/") {
					continue
				}
				filePipelines++
				if containsStringValue(pipeline["processors"], "batch") {
					t.Errorf("file pipeline %s places an asynchronous batch before queue admission", pipelineName)
				}
				pipelineExporters, ok := pipeline["exporters"].([]any)
				if !ok {
					t.Fatalf("file pipeline %s exporters are not a list", pipelineName)
				}
				for _, item := range pipelineExporters {
					exporterName, ok := item.(string)
					if !ok || strings.HasPrefix(exporterName, "file/") {
						continue
					}
					exporter, ok := exporters[exporterName].(map[string]any)
					if !ok {
						t.Fatalf("file pipeline %s exporter %s is not configured", pipelineName, exporterName)
					}
					queue, ok := exporter["sending_queue"].(map[string]any)
					storage, storageOK := queue["storage"].(string)
					if !ok || queue["enabled"] != true || queue["block_on_overflow"] != true || !storageOK || storage == "" {
						t.Errorf("exporter %s must use blocking persistent admission: %v", exporterName, queue)
					}
					if _, ok := queue["batch"].(map[string]any); !ok {
						t.Errorf("exporter %s must batch only after persistent queue admission", exporterName)
					}
				}
			}
			if filePipelines == 0 {
				t.Fatal("profile has no checkpointed file pipeline")
			}
		})
	}
}

func TestEveryShippedRemotePipelineAdmitsBeforeBatching(t *testing.T) {
	t.Parallel()
	profiles := []string{
		"otlpproxy/config.yaml",
		"examples/config.ocsf.yaml",
		"examples/config.openshell-log.yaml",
		"examples/config.full.yaml",
		"examples/config.nemo-relay.yaml",
		"examples/config.nemo-relay-file.yaml",
		"examples/demo/real-gateway/exporter.yaml",
		"examples/demo/real-gateway/exporter.elastic.yaml",
		"examples/demo/real-gateway/exporter.external.yaml",
		"deploy/docker/config.yaml",
		"deploy/docker/config.full.yaml",
		"deploy/docker/config.complete.yaml",
		"deploy/podman/config.yaml",
		"deploy/podman/config.full.yaml",
		"deploy/podman/config.complete.yaml",
		"deploy/kubernetes/chart/files/config-stream.yaml",
	}
	for _, profile := range profiles {
		profile := profile
		t.Run(profile, func(t *testing.T) {
			t.Parallel()
			document := parseRepositoryYAML(t, profile)
			if processors, ok := document["processors"].(map[string]any); ok {
				if _, exists := processors["batch"]; exists {
					t.Fatal("pre-queue batch processor remains configured")
				}
			}
			if service, ok := document["service"].(map[string]any); ok {
				if pipelines, ok := service["pipelines"].(map[string]any); ok {
					for pipelineName, raw := range pipelines {
						pipeline, ok := raw.(map[string]any)
						if !ok {
							t.Fatalf("pipeline %s is not a map", pipelineName)
						}
						if containsStringValue(pipeline["processors"], "batch") {
							t.Errorf("pipeline %s acknowledges through a pre-queue batch processor", pipelineName)
						}
					}
				}
			}

			exporters, ok := document["exporters"].(map[string]any)
			if !ok {
				return
			}
			for exporterName, raw := range exporters {
				if !strings.HasPrefix(exporterName, "otlp") && !strings.HasPrefix(exporterName, "cloudevents") {
					continue
				}
				exporter, ok := raw.(map[string]any)
				if !ok {
					t.Fatalf("remote exporter %s is not a map", exporterName)
				}
				queue, ok := exporter["sending_queue"].(map[string]any)
				if !ok {
					t.Errorf("remote exporter %s has no persistent sending queue", exporterName)
					continue
				}
				storage, storageOK := queue["storage"].(string)
				if queue["enabled"] != true || queue["block_on_overflow"] != true || !storageOK || storage == "" {
					t.Errorf("remote exporter %s does not block on persistent admission: %v", exporterName, queue)
				}
				if queueSize, ok := queue["queue_size"].(int); !ok || queueSize <= 0 {
					t.Errorf("remote exporter %s has invalid queue_size: %v", exporterName, queue["queue_size"])
				}
				batch, ok := queue["batch"].(map[string]any)
				if !ok || batch["sizer"] != "items" || batch["flush_timeout"] == "" {
					t.Errorf("remote exporter %s does not batch after admission: %v", exporterName, batch)
				}
				retry, ok := exporter["retry_on_failure"].(map[string]any)
				if !ok || retry["enabled"] != true || retry["max_elapsed_time"] != "0s" {
					t.Errorf("remote exporter %s does not retry indefinitely: %v", exporterName, retry)
				}
			}
		})
	}
}

func TestTemplatedCompleteProfileAdmitsBeforeBatching(t *testing.T) {
	t.Parallel()
	config := readFile(t, "kubernetes/chart/files/config-complete.yaml")
	for _, forbidden := range []string{"batchprocessor", ", batch]", "batch,"} {
		if strings.Contains(config, forbidden) {
			t.Errorf("complete profile contains pre-queue batching %q", forbidden)
		}
	}
	if got := strings.Count(config, "block_on_overflow: true"); got != 4 {
		t.Errorf("complete profile blocking persistent queues = %d, want 4", got)
	}
	if got := strings.Count(config, "      batch:"); got != 4 {
		t.Errorf("complete profile queue batch entries = %d, want 4", got)
	}
	if strings.Contains(readFile(t, "../builder-config.yaml"), "batchprocessor") {
		t.Fatal("main distribution still compiles the pre-queue batch processor")
	}
}

func TestDemoDestinationOverlaysPreserveSplitEvidencePipelines(t *testing.T) {
	t.Parallel()
	for _, name := range []string{"exporter.elastic.yaml", "exporter.elastic-headless.yaml", "exporter.external.yaml"} {
		path := filepath.Join("..", "examples", "demo", "real-gateway", name)
		encoded, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		config := string(encoded)
		for _, pipeline := range []string{"logs/openshell_files:", "logs/watchsandbox:"} {
			if !strings.Contains(config, pipeline) {
				t.Errorf("%s does not route %s to its destination", name, pipeline)
			}
		}
		if strings.Contains(config, "logs/openshell:") || strings.Contains(config, "logs/files:") {
			t.Errorf("%s patches a stale pipeline name", name)
		}
	}
}

func containsPrefixedString(values []any, prefix string) bool {
	for _, value := range values {
		if text, ok := value.(string); ok && strings.HasPrefix(text, prefix) {
			return true
		}
	}
	return false
}

func containsStringValue(value any, expected string) bool {
	values, ok := value.([]any)
	if !ok {
		return false
	}
	for _, value := range values {
		if value == expected {
			return true
		}
	}
	return false
}

func TestHelmWorkloadIsHardenedAndSingleWriter(t *testing.T) {
	deployment := readFile(t, "kubernetes/chart/templates/deployment.yaml")
	workload := deployment + readFile(t, "kubernetes/chart/values.yaml")
	for _, required := range []string{
		"replicas: 1",
		"type: Recreate",
		"automountServiceAccountToken: {{ eq (include \"openshell-event-exporter.module.kubernetesContext\" .) \"true\" }}",
		"allowPrivilegeEscalation: false",
		"readOnlyRootFilesystem: true",
		"drop: [ALL]",
		"command: [\"/usr/local/bin/healthcheck\"]",
		"checksum/config:",
		"source-auth",
		"destination-auth",
		"defaultMode: 0440",
	} {
		if !strings.Contains(workload, required) {
			t.Errorf("Helm workload contract is missing %q", required)
		}
	}
	for _, forbidden := range []string{"replicas: 2", "docker.sock", "podman.sock", "compute-driver.sock"} {
		if strings.Contains(deployment, forbidden) {
			t.Errorf("Helm Deployment contains forbidden value %q", forbidden)
		}
	}
}

func TestHelmProfilesPreserveDurabilityBoundaries(t *testing.T) {
	values := readFile(t, "kubernetes/chart/values.yaml")
	schema := readFile(t, "kubernetes/chart/values.schema.json")
	pvcs := readFile(t, "kubernetes/chart/templates/pvc.yaml")
	deployment := readFile(t, "kubernetes/chart/templates/deployment.yaml")
	for _, profile := range []string{"stream", "complete", "custom"} {
		if !strings.Contains(schema, "\""+profile+"\"") {
			t.Errorf("Helm values schema is missing profile %q", profile)
		}
	}
	for _, required := range []string{
		"queue:", "recovery:", "checkpoints:", "otlpGrpcQueue:", "otlpHttpQueue:",
	} {
		if !strings.Contains(values, required) {
			t.Errorf("Helm values are missing persistence lane %q", required)
		}
	}
	if got := strings.Count(pvcs, "helm.sh/resource-policy: keep"); got != 5 {
		t.Fatalf("retained PVC templates = %d, want 5", got)
	}
	for _, required := range []string{
		"required \"files.openshell.existingClaim",
		"required \"files.relay.existingClaim",
		"readOnly: true",
		"EXPORTER_CHECKPOINT_DIR",
		"EXPORTER_OTLP_GRPC_QUEUE_DIR",
		"EXPORTER_OTLP_HTTP_QUEUE_DIR",
	} {
		if !strings.Contains(deployment, required) {
			t.Errorf("Helm profile wiring is missing %q", required)
		}
	}
}

func TestHelmValuesRequireImmutableSecureConfiguration(t *testing.T) {
	schema := readFile(t, "kubernetes/chart/values.schema.json")
	for _, required := range []string{
		"^sha256:[0-9a-f]{64}$",
		"^https://",
		"\"minimum\": 30",
		"\"ReadWriteOncePod\"",
	} {
		if !strings.Contains(schema, required) {
			t.Errorf("Helm schema is missing %q", required)
		}
	}
	chart := parseYAML(t, "kubernetes/chart/Chart.yaml")
	if chart["apiVersion"] != "v2" || chart["type"] != "application" {
		t.Fatalf("unexpected Helm chart metadata: %v", chart)
	}
}

func TestHelmCompleteProfileCollectsAllQualifiedKubernetesLanes(t *testing.T) {
	config := readFile(t, "kubernetes/chart/files/config-complete.yaml")
	builder := readFile(t, "../builder-config.yaml")
	deployment := readFile(t, "kubernetes/chart/templates/deployment.yaml")
	service := readFile(t, "kubernetes/chart/templates/service.yaml")
	rbac := readFile(t, "kubernetes/chart/templates/rbac.yaml")
	policy := readFile(t, "kubernetes/chart/templates/networkpolicy.yaml")
	for _, required := range []string{
		"watchsandbox:", "file_log/openshell_ocsf:", "otlp/forwarded_ocsf:",
		"k8s_objects:", "otlp/openshell_native:", "otlp/nemo_relay:",
		"logs/forwarded_ocsf:", "logs/kubernetes_context:", "traces/openshell_native:",
		"source_profiles:", "ocsf.forwarded", "openshell.log.forwarded",
		"kubernetes.context", "openshell.trace", "attributes/forwarded_files:",
	} {
		if !strings.Contains(config, required) {
			t.Errorf("complete Collector profile is missing %q", required)
		}
	}
	if strings.Contains(config, "key: openshell.acquisition.kind\n        value: ocsf.forwarded") {
		t.Fatal("Collector must preserve the sidecar's OCSF versus operational acquisition kind")
	}
	for _, required := range []string{
		"receiver/k8sobjectsreceiver v0.160.0",
		"processor/attributesprocessor v0.160.0",
	} {
		if !strings.Contains(builder, required) {
			t.Errorf("Collector Builder is missing %q", required)
		}
	}
	for _, required := range []string{
		"native-otlp", "forwarded-otlp", "forwarded-ocsf-auth",
		"automountServiceAccountToken: {{ eq (include \"openshell-event-exporter.module.kubernetesContext\"", "EXPORTER_CHECKPOINT_DIR",
	} {
		if !strings.Contains(deployment+service, required) {
			t.Errorf("complete Helm workload is missing %q", required)
		}
	}
	if !strings.Contains(rbac, "kind: Role") || strings.Contains(rbac, "kind: ClusterRole") {
		t.Fatal("Kubernetes context RBAC must remain namespace-scoped")
	}
	for _, required := range []string{"kind: NetworkPolicy", "port: 4319", "port: 4320"} {
		if !strings.Contains(policy, required) {
			t.Errorf("ingress policy is missing %q", required)
		}
	}
}

func TestHelmCompleteDefaultsToAutomaticPerSandboxForwarding(t *testing.T) {
	values := parseYAML(t, "kubernetes/chart/values.complete.example.yaml")
	for _, path := range [][]string{
		{"relay", "input"},
		{"nativeOtlp"},
		{"kubernetesContext"},
		{"forwardedOcsf"},
		{"sandboxInjection"},
	} {
		settings := nestedMap(t, values, path...)
		if enabled, ok := settings["enabled"].(bool); !ok || !enabled {
			t.Errorf("recommended evidence lane %v is not enabled", path)
		}
	}
	files := nestedMap(t, values, "files", "openshell")
	if enabled, ok := files["enabled"].(bool); !ok || enabled {
		t.Fatal("complete profile must not depend on a shared OpenShell evidence PVC")
	}
	injection := nestedMap(t, values, "sandboxInjection")
	for _, key := range []string{"allowedNamespaces", "allowedGatewayUsernames"} {
		items, ok := injection[key].([]any)
		if !ok || len(items) == 0 {
			t.Fatalf("sandbox injection %s=%v", key, injection[key])
		}
	}
	for _, path := range [][]string{{"evidencePersistence"}, {"statePersistence"}} {
		persistence := nestedMap(t, injection, path...)
		if persistence["storageClass"] == "" || persistence["size"] == "" {
			t.Fatalf("sandbox persistence %v=%v", path, persistence)
		}
	}
}

func TestAutomaticSandboxForwarderIsPinnedDurableAndFailClosed(t *testing.T) {
	injector := readFile(t, "kubernetes/chart/templates/sandbox-injector.yaml")
	configMap := readFile(t, "kubernetes/chart/templates/forwarder-configmap.yaml")
	agentConfig := readFile(t, "kubernetes/chart/files/fluent-bit.conf")
	networkConfig := readFile(t, "kubernetes/chart/files/fluent-bit-network.conf")
	config := agentConfig + networkConfig
	goInjector := readFile(t, "../sandboxinjector/injector.go")
	dockerfile := readFile(t, "../Dockerfile")
	for _, required := range []string{
		"kind: MutatingWebhookConfiguration", "failurePolicy: Fail", "operations: [CREATE]",
		"apiGroups: [agents.x-k8s.io]", "resources: [sandboxes]", "namespaceSelector:",
		"automountServiceAccountToken: false", "SANDBOX_INJECTOR_ALLOWED_USERNAMES",
	} {
		if !strings.Contains(injector, required) {
			t.Errorf("sandbox injector is missing %q", required)
		}
	}
	for _, required := range []string{
		"ReadWriteOncePod", "openshell-evidence-forwarder", "metadata.annotations['",
		"readOnlyRootFilesystem", "runAsNonRoot", "openshell-forwarder-state",
		"openshell-evidence-agent", "openshell-evidence-network",
	} {
		if !strings.Contains(goInjector, required) {
			t.Errorf("injected Sandbox contract is missing %q", required)
		}
	}
	for _, required := range []string{
		"DB.Sync                   Full", "Offset_Key                log.file.record_offset",
		"Path_Key                  log.file.path", "storage.type              filesystem",
		"Retry_Limit               no_limits", "Tls.Verify                On",
		"Tls.Verify_Hostname       On", "Authorization Bearer ${FORWARDED_OCSF_TOKEN}",
		"openshell.acquisition.kind ocsf.forwarded",
		"openshell.acquisition.kind openshell.log.forwarded",
		"Logs_Body_Key             log",
	} {
		if !strings.Contains(config, required) {
			t.Errorf("Fluent Bit forwarding contract is missing %q", required)
		}
	}
	if strings.Contains(config, "Parser                    json") {
		t.Fatal("Fluent Bit must retain raw lines; central normalization owns parsing and redaction")
	}
	if strings.Contains(agentConfig, "/evidence/network/") {
		t.Fatal("agent-only Fluent Bit profile must not scan an unmounted network path")
	}
	for _, required := range []string{
		"openshell.ocsf.network", "openshell.log.network", "/evidence/network/",
	} {
		if !strings.Contains(networkConfig, required) {
			t.Errorf("network Fluent Bit profile is missing %q", required)
		}
	}
	for _, profile := range []string{agentConfig, networkConfig} {
		for _, required := range []string{
			"openshell.acquisition.kind source.capability",
			"openshell.source.network_file_lane",
			"Samples                   1",
		} {
			if !strings.Contains(profile, required) {
				t.Errorf("Fluent Bit capability diagnostic is missing %q", required)
			}
		}
	}
	if !strings.Contains(configMap, "range $index, $namespace") ||
		!strings.Contains(configMap, "forwarder-agent") ||
		!strings.Contains(configMap, "forwarder-network") {
		t.Fatal("forwarder ConfigMap must be installed in every allow-listed sandbox namespace")
	}
	if !strings.Contains(dockerfile, "/out/sandbox-injector") ||
		!strings.Contains(dockerfile, "/usr/local/bin/sandbox-injector") {
		t.Fatal("final exporter image does not contain the admission injector binary")
	}
	for _, text := range []string{injector, configMap, goInjector} {
		for _, forbidden := range []string{"ReadWriteMany", "hostPath:", "docker.sock"} {
			if strings.Contains(text, forbidden) {
				t.Errorf("automatic sidecar path contains forbidden %q", forbidden)
			}
		}
	}
}

func TestCertManagerEncryptsEveryInternalExporterInput(t *testing.T) {
	certificates := readFile(t, "kubernetes/chart/templates/certificates.yaml")
	baseValues := readFile(t, "kubernetes/chart/values.yaml")
	injector := readFile(t, "kubernetes/chart/templates/sandbox-injector.yaml")
	deployment := readFile(t, "kubernetes/chart/templates/deployment.yaml")
	config := readFile(t, "kubernetes/chart/files/config-complete.yaml")
	values := readFile(t, "kubernetes/chart/values.complete.example.yaml")
	manage := readFile(t, "kubernetes/manage.sh")
	gateway := readFile(t, "kubernetes/integrations/openshell/gateway-otlp.toml")

	for _, required := range []string{
		"apiVersion: cert-manager.io/v1", "kind: Certificate", "usages: [server auth]",
		"usages: [client auth]", "forwardedOcsf.tlsSecret", "relay.input.tlsSecret",
		"nativeOtlp.tlsSecret", "sandboxInjection.forwarderTLSSecret",
		"relayClientNamespaces", "rotationPolicy",
	} {
		if !strings.Contains(certificates+baseValues, required) {
			t.Errorf("cert-manager contract is missing %q", required)
		}
	}
	for _, required := range []string{
		"cert-manager.io/inject-ca-from", "if not .Values.certManager.enabled",
		"failurePolicy: Fail",
	} {
		if !strings.Contains(injector, required) {
			t.Errorf("webhook certificate contract is missing %q", required)
		}
	}
	for _, required := range []string{
		"OPENSHELL_NATIVE_OTLP_TLS_CERT_FILE", "OPENSHELL_NATIVE_OTLP_TLS_KEY_FILE",
		"native-otlp-tls", `ternary "ca.crt" "client-ca.crt" .Values.certManager.enabled`,
	} {
		if !strings.Contains(deployment, required) {
			t.Errorf("exporter certificate wiring is missing %q", required)
		}
	}
	for _, required := range []string{
		"otlp/openshell_native:", "cert_file: ${env:OPENSHELL_NATIVE_OTLP_TLS_CERT_FILE}",
		"key_file: ${env:OPENSHELL_NATIVE_OTLP_TLS_KEY_FILE}", "reload_interval: 1m",
		"client_ca_file_reload: true",
	} {
		if !strings.Contains(config, required) {
			t.Errorf("native OTLP TLS receiver is missing %q", required)
		}
	}
	for _, required := range []string{
		"certManager:\n  enabled: true", "name: openshell-exporter-pki",
		"wait_for_certificates", "verify_webhook_ca_bundle", "ca.crt tls.crt tls.key",
	} {
		if !strings.Contains(values+manage, required) {
			t.Errorf("cert-manager install contract is missing %q", required)
		}
	}
	injectorCommand := readFile(t, "../cmd/sandbox-injector/main.go")
	if !strings.Contains(injectorCommand, "GetCertificate:") ||
		!strings.Contains(injectorCommand, `ListenAndServeTLS("", "")`) {
		t.Fatal("admission injector does not reload rotated cert-manager leaf files")
	}
	if !strings.Contains(gateway, `endpoint = "https://`) {
		t.Fatal("native OpenShell OTLP example does not require TLS")
	}
	chart := readFile(t, "kubernetes/chart/Chart.yaml")
	if strings.Contains(chart, "dependencies:") || strings.Contains(chart, "jetstack") {
		t.Fatal("cert-manager must be installed once by the cluster operator, not embedded as a subchart")
	}
}

func TestObsoleteRWXSandboxIntegrationIsRemoved(t *testing.T) {
	for _, name := range []string{
		"kubernetes/chart/templates/evidence-initializer-job.yaml",
		"kubernetes/integrations/openshell/evidence-pvc.example.yaml",
		"kubernetes/integrations/openshell/sandbox-evidence-driver-config.example.json",
		"kubernetes/integrations/fluent-bit/sidecar-fragment.yaml",
	} {
		if _, err := os.Stat(deploymentPath(name)); !os.IsNotExist(err) {
			t.Errorf("obsolete RWX/manual integration still exists: %s", name)
		}
	}
	for _, name := range []string{
		"kubernetes/chart/values.yaml",
		"kubernetes/chart/values.schema.json",
		"kubernetes/manage.sh",
	} {
		text := readFile(t, name)
		for _, forbidden := range []string{"evidenceInitializer", "sandboxNames", "must support ReadWriteMany"} {
			if strings.Contains(text, forbidden) {
				t.Errorf("%s retains obsolete RWX setting %q", name, forbidden)
			}
		}
	}
}

func TestDockerProfilesAreRunnableAndValidated(t *testing.T) {
	compose := readFile(t, "docker/compose.yaml")
	fullCompose := readFile(t, "docker/compose.full.yaml")
	manage := readFile(t, "docker/manage.sh")
	preflight := readFile(t, "docker/preflight.sh")
	fullConfig := readFile(t, "docker/config.full.yaml")
	for _, required := range []string{
		"SOURCE_SECRETS_DIR", "DESTINATION_SECRETS_DIR",
		"EXPORTER_CONFIG_FILE", "OPENSHELL_TOKEN_FILE_INTERNAL-", "pull_policy:", "driver: local",
	} {
		if !strings.Contains(compose, required) {
			t.Errorf("Docker core profile is missing %q", required)
		}
	}
	for _, required := range []string{
		"NEMO_RELAY_INPUT_SECRETS_DIR", "OTLP_DESTINATION_SECRETS_DIR",
		"OTLP_GRPC_QUEUE_DIR", "OTLP_HTTP_QUEUE_DIR", "127.0.0.1:4317:4317",
	} {
		if !strings.Contains(fullCompose, required) {
			t.Errorf("Docker full Compose profile is missing %q", required)
		}
	}
	for _, required := range []string{
		"otlp/nemo_relay:", "file_log/nemo_relay:", "file_storage/otlp_queue:",
		"file_storage/otlphttp_queue:", "traces/nemo_relay:", "bearertokenauth/otlp_destination",
	} {
		if !strings.Contains(fullConfig, required) {
			t.Errorf("Docker full Collector profile is missing %q", required)
		}
	}
	for _, required := range []string{
		"prepare-image.sh", "Collector configuration validation failed",
		"credentials must be distinct", "persistent_dirs",
		"SOURCE_BEARER_ENABLED", "enable source bearer authentication, source mTLS, or both",
	} {
		if !strings.Contains(preflight, required) {
			t.Errorf("Docker preflight is missing %q", required)
		}
	}
	for _, required := range []string{
		"OPENSHELL_TOKEN_FILE_INTERNAL", ".runtime", "install -m 0444",
	} {
		if !strings.Contains(manage, required) {
			t.Errorf("Docker manager is missing %q", required)
		}
	}
}

func TestPortableCompleteProfilesCollectEveryPortableLane(t *testing.T) {
	completeConfig := readFile(t, "docker/config.complete.yaml")
	completeCompose := readFile(t, "docker/compose.complete.yaml")
	podman := readFile(t, "podman/manage.sh")
	for _, required := range []string{
		"file_log/openshell_ocsf:", "file_log/openshell_sandbox:",
		"file_log/nemo_relay:", "watchsandbox:", "otlp/nemo_relay:",
		"otlp/openshell_native:", "otlp/forwarded_ocsf:",
		"logs/forwarded_ocsf:", "traces/nemo_relay:",
		"traces/openshell_native:", "ocsf.forwarded",
		"openshell.log.forwarded", "openshell.trace",
	} {
		if !strings.Contains(completeConfig, required) {
			t.Errorf("portable complete Collector profile is missing %q", required)
		}
	}
	for _, required := range []string{
		"OPENSHELL_NATIVE_OTLP_INPUT_SECRETS_DIR",
		"FORWARDED_OCSF_INPUT_SECRETS_DIR",
		"127.0.0.1}:4319:4319", "127.0.0.1}:4320:4320",
	} {
		if !strings.Contains(completeCompose, required) {
			t.Errorf("Docker complete Compose profile is missing %q", required)
		}
	}
	for _, required := range []string{
		"PODMAN_PROFILE", "config.complete.yaml",
		"SOURCE_BEARER_ENABLED", "source_token_internal",
		"enable source bearer authentication, source mTLS, or both",
		"validate_resource_controls", `--pids-limit="${EXPORTER_PIDS_LIMIT:-256}"`,
		"rootless Podman cannot enforce CPU, memory, and PID limits",
		"prepare-image.sh",
		"OPENSHELL_NATIVE_OTLP_INPUT_SECRETS_DIR",
		"FORWARDED_OCSF_INPUT_SECRETS_DIR",
		"must not bind an unauthenticated wildcard address",
		"source, input, and destination credentials must be distinct",
	} {
		if !strings.Contains(podman, required) {
			t.Errorf("Podman complete profile is missing %q", required)
		}
	}
}

func TestDeploymentScriptsAreExecutable(t *testing.T) {
	for _, name := range []string{
		"deploy.sh",
		"docker/preflight.sh",
		"docker/manage.sh",
		"podman/manage.sh",
		"kubernetes/manage.sh",
	} {
		info, err := os.Stat(deploymentPath(name))
		if err != nil {
			t.Fatalf("stat %s: %v", name, err)
		}
		if info.Mode().Perm()&0o111 == 0 {
			t.Errorf("%s is not executable", name)
		}
	}
}

func TestRuntimeDeploymentsHaveNoRuntimeAuthority(t *testing.T) {
	for _, name := range []string{
		"docker/compose.yaml",
		"docker/compose.full.yaml",
		"docker/compose.complete.yaml",
		"podman/manage.sh",
		"kubernetes/chart/templates/deployment.yaml",
	} {
		text := readFile(t, name)
		for _, socket := range []string{"docker.sock", "podman.sock", "compute-driver.sock"} {
			if strings.Contains(text, socket) {
				t.Errorf("%s grants forbidden runtime socket %s", name, socket)
			}
		}
		if (name == "docker/compose.yaml" || !strings.HasPrefix(name, "docker/compose.")) &&
			(!strings.Contains(text, "OPENSHELL_ALLOW_INSECURE_HTTP") ||
				!strings.Contains(text, "false")) {
			t.Errorf("%s does not force insecure HTTP off", name)
		}
	}
}

func TestExampleFilesContainNoCredentialMaterial(t *testing.T) {
	for _, name := range []string{
		"docker/.env.example",
		"podman/.env.example",
		"kubernetes/chart/values.yaml",
		"kubernetes/chart/ci/values.yaml",
		"kubernetes/chart/values.complete.example.yaml",
	} {
		text := readFile(t, filepath.Clean(name))
		for _, forbidden := range []string{"nvapi-", "sk-ant-", "Bearer eyJ"} {
			if strings.Contains(text, forbidden) {
				t.Errorf("%s contains credential-shaped text %q", name, forbidden)
			}
		}
	}
}

func TestSingleBinaryRoleTopologyIsFailClosed(t *testing.T) {
	helpers := readFile(t, "kubernetes/chart/templates/_helpers.tpl")
	centralConfig := readFile(t, "kubernetes/chart/files/config-central.yaml")
	edgeConfig := readFile(t, "kubernetes/chart/files/config-complete.yaml")
	deployment := readFile(t, "kubernetes/chart/templates/deployment.yaml")
	networkPolicy := readFile(t, "kubernetes/chart/templates/networkpolicy.yaml")

	for _, required := range []string{
		"topology.role must be standalone, edge, or central",
		"central role accepts only internalOtlp",
		"central internal OTLP requires mTLS",
		"edge role forwards only through durable OTLP",
	} {
		if !strings.Contains(helpers, required) {
			t.Errorf("role validation is missing %q", required)
		}
	}
	for _, required := range []string{
		"bearertokenauth/internal",
		"client_ca_file",
		"evidencecontract/verify",
		"tenant_id:",
		"otlp/internal",
	} {
		if !strings.Contains(centralConfig, required) {
			t.Errorf("central config is missing %q", required)
		}
	}
	for _, required := range []string{
		"evidencecontract/stamp",
		"load_balancing/internal",
		"routing_attributes: [openshell.exporter.tenant.id, openshell.exporter.shard.key]",
	} {
		if !strings.Contains(edgeConfig, required) {
			t.Errorf("edge config is missing %q", required)
		}
	}
	for _, required := range []string{
		"INTERNAL_OTLP_TOKEN_FILE",
		"INTERNAL_OTLP_CLIENT_CA_FILE",
		"internal-input-auth",
		"internal-input-tls",
	} {
		if !strings.Contains(deployment, required) {
			t.Errorf("central workload is missing %q", required)
		}
	}
	for _, port := range []string{"4330", "4331"} {
		if !strings.Contains(networkPolicy, port) {
			t.Errorf("central NetworkPolicy is missing port %s", port)
		}
	}
}

func TestRoleExamplesUseTheSameImmutableProductImage(t *testing.T) {
	edge := parseYAML(t, "kubernetes/chart/values.edge.example.yaml")
	central := parseYAML(t, "kubernetes/chart/values.central.example.yaml")
	edgeImage := nestedMap(t, edge, "image")
	centralImage := nestedMap(t, central, "image")
	if edgeImage["repository"] != centralImage["repository"] || edgeImage["digest"] != centralImage["digest"] {
		t.Fatal("edge and central examples do not use the same immutable product image")
	}
	edgeTopology := nestedMap(t, edge, "topology")
	centralTopology := nestedMap(t, central, "topology")
	if edgeTopology["role"] != "edge" || centralTopology["role"] != "central" {
		t.Fatal("role examples do not select their declared runtime roles")
	}
}

func TestScaledCentralOwnsPerReplicaState(t *testing.T) {
	deployment := readFile(t, "kubernetes/chart/templates/deployment.yaml")
	pvc := readFile(t, "kubernetes/chart/templates/pvc.yaml")
	headless := readFile(t, "kubernetes/chart/templates/headless-service.yaml")
	helpers := readFile(t, "kubernetes/chart/templates/_helpers.tpl")

	for _, required := range []string{
		"kind: {{ ternary \"StatefulSet\" \"Deployment\"",
		"persistentVolumeClaimRetentionPolicy:",
		"whenScaled: Retain",
		"volumeClaimTemplates:",
		"name: queue",
		"name: recovery",
		"name: otlp-grpc-queue",
	} {
		if !strings.Contains(deployment, required) {
			t.Errorf("scaled central workload is missing %q", required)
		}
	}
	if !strings.Contains(pvc, "openshell-event-exporter.centralStateful") {
		t.Error("standalone PVC template does not exclude StatefulSet-owned central claims")
	}
	for _, required := range []string{"clusterIP: None", "internal-grpc", "publishNotReadyAddresses: false"} {
		if !strings.Contains(headless, required) {
			t.Errorf("headless central service is missing %q", required)
		}
	}
	for _, required := range []string{
		"central replicas greater than one require shardRouting.enabled=true",
		"scaled central owns one persistent queue and recovery claim per replica",
		"edge shard routing requires destination OTLP mTLS",
		"edge and central roles require a bounded topology.tenantId",
	} {
		if !strings.Contains(helpers, required) {
			t.Errorf("scaled role validation is missing %q", required)
		}
	}

	central := parseYAML(t, "kubernetes/chart/values.central.example.yaml")
	topology := nestedMap(t, central, "topology")
	if topology["tenantId"] != "customer-a" {
		t.Fatal("central example does not declare a tenant boundary")
	}
	centralSettings, ok := topology["central"].(map[string]any)
	if !ok || centralSettings["replicas"] != 3 {
		t.Fatalf("central example does not select three stateful replicas: %#v", centralSettings)
	}
}
