// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package kubernetesdemo

import (
	"os"
	"strings"
	"testing"
)

func read(t *testing.T, name string) string {
	t.Helper()
	data, err := os.ReadFile(name)
	if err != nil {
		t.Fatal(err)
	}
	return string(data)
}

func TestTurnkeyDemoKeepsProductBoundaries(t *testing.T) {
	values := read(t, "values/exporter.yaml.tpl")
	openshellValues := read(t, "values/openshell.yaml")
	elastic := read(t, "manifests/elastic-soc.yaml")
	injector := read(t, "../../../deploy/kubernetes/chart/templates/sandbox-injector.yaml")
	for _, required := range []string{
		"profile: complete", "forwardedOcsf:", "kubernetesContext:", "mtlsEnabled: false",
		"https://logstash", "https://apm-server", "csi-hostpath-sc",
	} {
		if !strings.Contains(values, required) {
			t.Errorf("demo values are missing %q", required)
		}
	}
	for _, required := range []string{
		"elasticsearch:9.4.3@sha256:", "logstash:9.4.3@sha256:", "kibana:9.4.3@sha256:",
		"apm-server:9.4.3@sha256:", "prom/prometheus:v3.13.1@sha256:",
		"grafana/grafana:13.1.3@sha256:", "kind: Certificate", "PersistentVolumeClaim",
	} {
		if !strings.Contains(elastic, required) {
			t.Errorf("SOC destination stack is missing %q", required)
		}
	}
	for _, required := range []string{
		"failurePolicy: Fail", "operations: [CREATE]", "SANDBOX_INJECTOR_ALLOWED_USERNAMES",
	} {
		if !strings.Contains(injector, required) {
			t.Errorf("injector contract is missing %q", required)
		}
	}
	for _, forbidden := range []string{"policy mutation", "recommendation engine", "decision receiver"} {
		if strings.Contains(values+openshellValues+elastic, forbidden) {
			t.Errorf("demo introduces forbidden exporter responsibility %q", forbidden)
		}
	}
}

func TestDemoUsesRealPinnedHermesAndRelay(t *testing.T) {
	library := read(t, "lib.sh")
	sandboxImage := read(t, "sandbox/Dockerfile")
	plugins := read(t, "sandbox/plugins.toml")
	task := read(t, "run-task.sh")
	provider := read(t, "../../../deploy/kubernetes/integrations/openshell/relay-otlp-provider.yaml.tpl")
	for _, required := range []string{
		"OPEN_SHELL_VERSION=0.0.113", "AGENT_SANDBOX_VERSION=0.5.3",
		"MINIKUBE_VERSION=1.38.1", "KUBECTL_VERSION=1.35.0", "HELM_VERSION=3.22.0",
		"sha256_check", "--proto '=https'", "umask 077",
	} {
		if !strings.Contains(library, required) {
			t.Errorf("demo bootstrap is missing %q", required)
		}
	}
	for _, required := range []string{
		"hermes-sandbox-base@sha256:", "HERMES_VERSION=v2026.8.16", "HERMES_TARBALL_SHA256=",
		"hermes-correlated", "NeMo Relay configuration valid", "openshell_exporter_ca", "USER sandbox",
	} {
		if !strings.Contains(sandboxImage, required) {
			t.Errorf("Hermes sandbox image is missing %q", required)
		}
	}
	for _, required := range []string{
		"type = \"openinference\"", "https://openshell-exporter-openshell-event-exporter.openshell-observability.svc.cluster.local:4318/v1/traces",
		"__OPENSHELL_SANDBOX_ID__", "__OPENSHELL_POLICY_VERSION__", "NEMO_RELAY_OTEL_AUTHORIZATION",
	} {
		if !strings.Contains(plugins, required) {
			t.Errorf("NeMo Relay configuration is missing %q", required)
		}
	}
	for _, required := range []string{
		"nvidia/nemotron-3-super-120b-a12b", "HERMES_OPENSHELL_SANDBOX_ID", "HERMES_SESSION_ID", "providers_v2_enabled", "provider profile lint",
		"--provider openshell-relay-otlp-provider-v2",
		"cleanup_relay_provider_secret", "openshell-exporter-relay-input-auth", "--from-literal=token=\"$relay_token\"",
		"agent_policy_proposals_enabled", "proposal_approval_mode --value manual",
		"http://policy.local/v1/proposals", "OPENSHELL_DEMO_DRAFT_CHUNK",
		"openshell rule get k8s-evidence-demo --status pending", "draft-proposal.json",
		"NEMO_RELAY_OTLP_TOKEN", "path: /v1/traces",
	} {
		if !strings.Contains(task+provider, required) {
			t.Errorf("real task is missing %q", required)
		}
	}
}

func TestManualSandboxCommandsPreserveIdentityAndOwnership(t *testing.T) {
	control := read(t, "sandbox-control-lib.sh")
	create := read(t, "create-sandboxes.sh")
	terminal := read(t, "hermes-terminal.sh")
	for _, required := range []string{
		"automountServiceAccountToken: false", "capabilities: {drop: [ALL]}",
		"demo=openshell-event-exporter", "no existing sandbox was changed",
		"MAX_SANDBOX_NAME_LENGTH=19", "failed to create sandbox",
		"openshell-client-tls", "nvidia/nemotron-3-super-120b-a12b",
	} {
		if !strings.Contains(control, required) {
			t.Errorf("manual control contract is missing %q", required)
		}
	}
	for _, required := range []string{
		"count >= 1 && count <= 20", "date -u '+%H%M%S'", "PREFIX must contain at most 9 characters", "create_demo_sandbox",
	} {
		if !strings.Contains(create, required) {
			t.Errorf("batch sandbox command is missing %q", required)
		}
	}
	for _, required := range []string{
		"HERMES_OPENSHELL_SANDBOX_ID", "HERMES_OPENSHELL_POLICY_VERSION",
		"hermes-correlated", "refusing to enter sandbox", "exec -it",
	} {
		if !strings.Contains(terminal, required) {
			t.Errorf("interactive Hermes command is missing %q", required)
		}
	}
}
