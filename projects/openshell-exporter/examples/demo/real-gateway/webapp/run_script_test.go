// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package main

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func TestVersionCheckIsIndependentOfCallerDirectory(t *testing.T) {
	t.Parallel()
	script, err := filepath.Abs("../../../../scripts/check-version.sh")
	if err != nil {
		t.Fatal(err)
	}
	config, err := os.ReadFile("../../../../builder-config.yaml")
	if err != nil {
		t.Fatal(err)
	}
	expected := ""
	for _, line := range strings.Split(string(config), "\n") {
		if strings.HasPrefix(line, "  version: ") {
			expected = strings.TrimSpace(strings.TrimPrefix(line, "  version: "))
			break
		}
	}
	if expected == "" {
		t.Fatal("builder configuration has no distribution version")
	}
	command := exec.Command(script)
	command.Dir = t.TempDir()
	output, err := command.CombinedOutput()
	if err != nil {
		t.Fatalf("version checker failed outside repository root: %v\n%s", err, output)
	}
	if got := strings.TrimSpace(string(output)); got != expected {
		t.Fatalf("version checker returned %q, want %q", got, expected)
	}
}

func TestRunScriptBindsGatewaySandboxIdentityBeforeHermesExec(t *testing.T) {
	encoded, err := os.ReadFile("../run.sh")
	if err != nil {
		t.Fatal(err)
	}
	script := string(encoded)
	extraction := strings.Index(script, "OPENSHELL_DEMO_SANDBOX_ID=$(jq")
	helper := strings.Index(script, "hermes_exec()")
	if extraction < 0 || helper < 0 {
		t.Fatal("run script is missing sandbox-ID extraction or Hermes exec helper")
	}
	if helper < extraction {
		t.Fatal("Hermes exec helper is defined before the authoritative gateway sandbox ID is resolved")
	}
	for _, required := range []string{
		`--env "HERMES_OPENSHELL_SANDBOX_ID=$OPENSHELL_DEMO_SANDBOX_ID"`,
		`--env "HERMES_OPENSHELL_SANDBOX_NAME=hermes-demo"`,
		`identity_args+=(--env "HERMES_OPENSHELL_POLICY_VERSION=$OPENSHELL_DEMO_POLICY_VERSION")`,
		`"${identity_args[@]}" "$@"`,
	} {
		if !strings.Contains(script[helper:], required) {
			t.Fatalf("Hermes exec helper is missing %q", required)
		}
	}
	if count := strings.Count(script, "hermes-correlated"); count != 2 {
		t.Fatalf("run script has %d Hermes launcher invocations, want version check and real task", count)
	}
	if strings.Contains(script, "control sandbox exec --name hermes-demo --workdir /sandbox --timeout 60") {
		t.Fatal("version check bypasses authoritative Hermes exec identity binding")
	}
	if strings.Contains(script, `--env "OPENSHELL_`) {
		t.Fatal("run script passes an environment key reserved by the OpenShell CLI")
	}

	encoded, err = os.ReadFile("../../../../integrations/hermes/hermes-correlated.sh")
	if err != nil {
		t.Fatal(err)
	}
	launcher := string(encoded)
	for _, required := range []string{
		"HERMES_OPENSHELL_SANDBOX_ID",
		"HERMES_OPENSHELL_SANDBOX_NAME",
		"HERMES_OPENSHELL_POLICY_VERSION",
		"__OPENSHELL_SANDBOX_ID__",
		"__OPENSHELL_POLICY_VERSION__",
	} {
		if !strings.Contains(launcher, required) {
			t.Fatalf("Hermes launcher is missing %q", required)
		}
	}
	for _, demoOnly := range []string{"docker-demo-gateway", "hermes-demo", "OPENSHELL_DEMO_SANDBOX_ID"} {
		if strings.Contains(launcher, demoOnly) {
			t.Fatalf("reusable Hermes launcher contains demo-only identity %q", demoOnly)
		}
	}
}

func TestRunScriptAvoidsKnownSandboxExecCompletionHangForProofSteps(t *testing.T) {
	t.Parallel()
	encoded, err := os.ReadFile("../run.sh")
	if err != nil {
		t.Fatal(err)
	}
	script := string(encoded)
	for _, expected := range []string{
		"sandbox_ssh_exec hermes-demo",
		"/usr/bin/sha256sum /sandbox/showcase-agent-evidence.txt",
		"/usr/bin/curl --fail --silent --show-error https://example.com/",
	} {
		if !strings.Contains(script, expected) {
			t.Fatalf("run script is missing SSH proof step %q", expected)
		}
	}

	helper, err := os.ReadFile("../control/sandbox-ssh-exec.sh")
	if err != nil {
		t.Fatal(err)
	}
	for _, expected := range []string{
		"unset OPENSHELL_GATEWAY_ENDPOINT",
		"openshell gateway add \"$gateway_endpoint\" --local --name exporter-demo",
		"export OPENSHELL_GATEWAY=exporter-demo",
		"openshell sandbox ssh-config",
		"ssh -T",
		"-o ConnectTimeout=30",
	} {
		if !strings.Contains(string(helper), expected) {
			t.Fatalf("SSH workaround is missing %q", expected)
		}
	}
}

func TestReusableHermesLauncherRendersPerExecutionIdentity(t *testing.T) {
	t.Parallel()
	directory := t.TempDir()
	template := filepath.Join(directory, "plugins.toml")
	runtime := filepath.Join(directory, "runtime-plugins.toml")
	fakeHermes := filepath.Join(directory, "hermes")
	invocation := filepath.Join(directory, "invocation")

	templateBody := strings.Join([]string{
		`"openshell.sandbox.id" = "__OPENSHELL_SANDBOX_ID__"`,
		`"openshell.sandbox.name" = "__OPENSHELL_SANDBOX_NAME__"`,
		`"openshell.policy.version" = "__OPENSHELL_POLICY_VERSION__"`,
		"",
	}, "\n")
	if err := os.WriteFile(template, []byte(templateBody), 0o600); err != nil {
		t.Fatal(err)
	}
	fakeBody := "#!/bin/sh\nset -eu\nprintf '%s\\n%s\\n' \"$HERMES_NEMO_RELAY_PLUGINS_TOML\" \"$*\" >\"$HERMES_TEST_INVOCATION\"\n"
	if err := os.WriteFile(fakeHermes, []byte(fakeBody), 0o700); err != nil {
		t.Fatal(err)
	}

	launcher := filepath.Join("..", "..", "..", "..", "integrations", "hermes", "hermes-correlated.sh")
	command := exec.Command(launcher, "chat", "--query", "hello")
	command.Env = append(os.Environ(),
		"HERMES_OPENSHELL_SANDBOX_ID=sandbox-123",
		"HERMES_OPENSHELL_SANDBOX_NAME=partner-agent",
		"HERMES_OPENSHELL_POLICY_VERSION=42",
		"HERMES_NEMO_RELAY_TEMPLATE="+template,
		"HERMES_NEMO_RELAY_RUNTIME="+runtime,
		"HERMES_BINARY="+fakeHermes,
		"HERMES_TEST_INVOCATION="+invocation,
	)
	if output, err := command.CombinedOutput(); err != nil {
		t.Fatalf("launcher failed: %v\n%s", err, output)
	}

	rendered, err := os.ReadFile(runtime)
	if err != nil {
		t.Fatal(err)
	}
	for _, expected := range []string{
		`"openshell.sandbox.id" = "sandbox-123"`,
		`"openshell.sandbox.name" = "partner-agent"`,
		`"openshell.policy.version" = "42"`,
	} {
		if !strings.Contains(string(rendered), expected) {
			t.Fatalf("runtime configuration is missing %q: %s", expected, rendered)
		}
	}
	if strings.Contains(string(rendered), "__OPENSHELL_") {
		t.Fatalf("runtime configuration retains an identity placeholder: %s", rendered)
	}
	info, err := os.Stat(runtime)
	if err != nil {
		t.Fatal(err)
	}
	if info.Mode().Perm() != 0o600 {
		t.Fatalf("runtime configuration mode=%#o, want 0600", info.Mode().Perm())
	}
	invoked, err := os.ReadFile(invocation)
	if err != nil {
		t.Fatal(err)
	}
	if string(invoked) != runtime+"\nchat --query hello\n" {
		t.Fatalf("unexpected Hermes invocation: %q", invoked)
	}

	runtimeWithoutPolicy := filepath.Join(directory, "runtime-without-policy.toml")
	invocationWithoutPolicy := filepath.Join(directory, "invocation-without-policy")
	command = exec.Command(launcher, "chat")
	command.Env = append(os.Environ(),
		"HERMES_OPENSHELL_SANDBOX_ID=sandbox-123",
		"HERMES_OPENSHELL_SANDBOX_NAME=partner-agent",
		"HERMES_OPENSHELL_POLICY_VERSION=",
		"HERMES_NEMO_RELAY_TEMPLATE="+template,
		"HERMES_NEMO_RELAY_RUNTIME="+runtimeWithoutPolicy,
		"HERMES_BINARY="+fakeHermes,
		"HERMES_TEST_INVOCATION="+invocationWithoutPolicy,
	)
	if output, err := command.CombinedOutput(); err != nil {
		t.Fatalf("launcher without policy version failed: %v\n%s", err, output)
	}
	rendered, err = os.ReadFile(runtimeWithoutPolicy)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(rendered), "openshell.policy.version") ||
		strings.Contains(string(rendered), "__OPENSHELL_POLICY_VERSION__") {
		t.Fatalf("launcher invented or retained an unavailable policy version: %s", rendered)
	}
}

func TestReusableHermesLauncherRejectsUnboundIdentity(t *testing.T) {
	t.Parallel()
	launcher := filepath.Join("..", "..", "..", "..", "integrations", "hermes", "hermes-correlated.sh")
	testCases := []struct {
		name      string
		sandboxID string
		message   string
	}{
		{name: "missing", message: "did not provide HERMES_OPENSHELL_SANDBOX_ID"},
		{name: "unsafe characters", sandboxID: "sandbox/../../other", message: "unsupported characters"},
	}
	for _, testCase := range testCases {
		t.Run(testCase.name, func(t *testing.T) {
			t.Parallel()
			command := exec.Command(launcher, "chat")
			command.Env = append(os.Environ(),
				"HERMES_OPENSHELL_SANDBOX_ID="+testCase.sandboxID,
			)
			output, err := command.CombinedOutput()
			if err == nil {
				t.Fatalf("launcher accepted untrusted sandbox identity %q", testCase.sandboxID)
			}
			if !strings.Contains(string(output), testCase.message) {
				t.Fatalf("launcher output %q does not contain %q", output, testCase.message)
			}
		})
	}
}

func TestRealDemoRequiresCompleteCorrelatedActivity(t *testing.T) {
	encoded, err := os.ReadFile("../run.sh")
	if err != nil {
		t.Fatal(err)
	}
	runScript := string(encoded)
	for _, required := range []string{
		"/sandbox/showcase-agent-evidence.txt",
		"/usr/bin/rm -f /sandbox/showcase-agent-evidence.txt",
		"/usr/bin/test ! -e /sandbox/showcase-agent-evidence.txt",
		"https://example.com/",
		"/usr/bin/test -s /sandbox/showcase-agent-evidence.txt",
		"/usr/bin/sha256sum /sandbox/showcase-agent-evidence.txt",
		"DEMO_TASK_FILE_SHA256",
		"DEMO_TASK_STARTED_AT",
		"DEMO_TASK_FINISHED_AT",
		"DEMO_AGENT_SESSION_ID",
		"DEMO_RELAY_SESSION_INSTANCE_ID",
		"/sandbox/telemetry/atif/hermes-atif-*.json",
		"could not prove the Hermes-to-Relay session bridge from ATIF",
		"hermes-run.log",
		`2>&1 | tee "$HERMES_RUN_LOG"`,
		"Running the real Hermes/Nemotron task",
		"Hermes/Nemotron task is still running",
		"Hermes/Nemotron task failed or reached the OpenShell 300s timeout",
		`wait "$hermes_task_pid"`,
	} {
		if !strings.Contains(runScript, required) {
			t.Fatalf("real agent task is missing %q", required)
		}
	}

	encoded, err = os.ReadFile("../verify.sh")
	if err != nil {
		t.Fatal(err)
	}
	verifyScript := string(encoded)
	for _, required := range []string{
		`[ "$relay_sandbox_traces" -eq "$traces" ]`,
		`[ "$relay_diagnosed_partial_spans" -eq "$relay_partial_session_spans" ]`,
		`[ "$relay_policy_traces" -eq "$traces" ]`,
		`[ "$relay_sessions" -eq 1 ]`,
		`[ "$relay_prompt_spans" -gt 0 ]`,
		`[ $((relay_complete_prompt_spans + relay_diagnosed_partial_prompt_spans)) -eq "$relay_prompt_spans" ]`,
		`[ "$relay_model_spans" -gt 0 ]`,
		`[ $((relay_complete_model_spans + relay_diagnosed_partial_model_spans)) -eq "$relay_model_spans" ]`,
		`[ "$relay_tool_spans" -gt 0 ]`,
		`[ $((relay_complete_tool_spans + relay_diagnosed_partial_tool_spans)) -eq "$relay_tool_spans" ]`,
		"openshell.correlation.missing",
		`[ "$relay_latency_spans" -gt 0 ]`,
		`[ "$relay_token_status_spans" -eq "$traces" ]`,
		`[ "$policy_denials" -gt 0 ]`,
		`[ "$watchsandbox_records" -gt 0 ]`,
		"process_observation_status=declared_unobserved",
		`.counts.sources.process_status == "declared_unobserved"`,
		`[ "$ocsf_network_records" -gt 0 ]`,
		`[ "$invalid" -eq 0 ]`,
		`(($entry.value | type) == "number")`,
		`startswith("llm.token_count.")`,
		`privacy-denied Relay attribute keys reached the conformance receiver`,
		"task-window.jq",
		"ATIF does not prove exactly one Hermes-to-Relay session bridge",
		`[ "$relay_session_id" = "$DEMO_RELAY_SESSION_INSTANCE_ID" ]`,
		"window_evidence",
		`[ "$correlated_logs" -eq "$logs" ]`,
		`qualification evidence self-validation failed`,
	} {
		if !strings.Contains(verifyScript, required) {
			t.Fatalf("real correlation gate is missing %q", required)
		}
	}
}

func TestElasticDemoCanReuseOnlyVerifiedBaseEvidence(t *testing.T) {
	t.Parallel()
	encoded, err := os.ReadFile("../run-elastic.sh")
	if err != nil {
		t.Fatal(err)
	}
	script := string(encoded)
	reuse := strings.Index(script, "DEMO_REUSE_VERIFIED_BASE")
	verify := strings.Index(script, "./verify.sh")
	elastic := strings.Index(script, "build exporter")
	if reuse < 0 || verify < 0 || elastic < 0 {
		t.Fatal("Elastic demo is missing its verified-base reuse path")
	}
	if reuse >= verify || verify >= elastic {
		t.Fatal("Elastic services can start before reused base evidence is verified")
	}
	if !strings.Contains(script, "DEMO_REUSE_VERIFIED_BASE must be true or false") {
		t.Fatal("Elastic demo does not reject an invalid reuse setting")
	}
	priorRuntime := strings.Index(script, `source "$ELASTIC_ENV"`)
	currentCandidate := strings.Index(script, `DEMO_REPOSITORY_COMMIT=$(git -C "$SCRIPT_DIR" rev-parse HEAD)`)
	if priorRuntime < 0 || currentCandidate < 0 || currentCandidate <= priorRuntime {
		t.Fatal("Elastic demo does not refresh candidate identity after loading prior runtime metadata")
	}
	for _, key := range []string{"DEMO_REPOSITORY_COMMIT", "DEMO_EXPORTER_VERSION", "DEMO_BUILD_CREATED"} {
		if strings.Count(script, `$1 != "`+key+`"`) < 2 {
			t.Fatalf("Elastic demo does not de-duplicate %s in both generated runtime passes", key)
		}
	}
}

func TestElasticSOCUsesLogstashAndAPMWithSeparateRuntimeSecrets(t *testing.T) {
	t.Parallel()
	encoded, err := os.ReadFile("../compose.elastic.yaml")
	if err != nil {
		t.Fatal(err)
	}
	compose := string(encoded)
	for _, required := range []string{
		"  logstash:",
		"openshell-cloudevents.conf:/usr/share/logstash/pipeline/openshell-cloudevents.conf:ro",
		"logstash-data:/usr/share/logstash/data",
		"  apm-server:",
		"elasticsearch-certs:/run/secrets:ro",
		"apm-server-data:/usr/share/apm-server/data",
	} {
		if !strings.Contains(compose, required) {
			t.Fatalf("Elastic runtime boundary is missing %q", required)
		}
	}
	for _, obsolete := range []string{
		"  siembridge:",
		"SIEMBRIDGE_",
		"logs-openshell.security-default-v1",
		"traces-openshell.agent-default-v1",
	} {
		if strings.Contains(compose, obsolete) {
			t.Fatalf("superseded Elastic bridge surface remains: %q", obsolete)
		}
	}
	if !strings.Contains(compose, "soc-saved-objects.ndjson:/soc-saved-objects.ndjson:ro") {
		t.Fatal("Kibana setup does not mount the versioned OpenShell SOC dashboard bundle")
	}
	for _, required := range []string{
		`user: "${DEMO_UID}:${DEMO_GID}"`,
		"elastic-detection-password:/run/secrets/elastic-detection-password:ro",
	} {
		if !strings.Contains(compose, required) {
			t.Fatalf("Kibana setup detection-secret boundary is missing %q", required)
		}
	}
	if strings.Contains(compose, "ELASTIC_DETECTION_PASSWORD:") {
		t.Fatal("detection-service password is exposed in the Compose environment")
	}
	for _, required := range []string{
		`xpack.security.http.ssl.enabled: "true"`,
		`SERVER_SSL_ENABLED: "true"`,
		"ELASTICSEARCH_HOSTS: https://elasticsearch:9200",
		"elasticsearch-certs:/usr/share/elasticsearch/config/certs:ro",
	} {
		if !strings.Contains(compose, required) {
			t.Fatalf("Elastic verified-TLS boundary is missing %q", required)
		}
	}
	for _, forbidden := range []string{
		`xpack.security.http.ssl.enabled: "false"`,
		"ELASTICSEARCH_HOSTS: http://",
		"ELASTICSEARCH_URL: http://",
	} {
		if strings.Contains(compose, forbidden) {
			t.Fatalf("Elastic Compose regressed to plaintext transport: %q", forbidden)
		}
	}

	encoded, err = os.ReadFile("../verify-elastic.sh")
	if err != nil {
		t.Fatal(err)
	}
	verifier := string(encoded)
	for _, required := range []string{
		"manage_index_templates == false",
		"delete_index == false",
		"[ \"$forbidden_status\" -eq 403 ]",
		`canonical_index="logs-openshell.security-default-v2"`,
		`canonical_trace_index="traces-apm*"`,
		"privacy-denied Relay attribute keys reached Elasticsearch",
		"APM Server accepted the wrong bearer token",
		"Logstash accepted a client without the required mTLS identity",
		"no privacy-filtered Relay spans reached Elasticsearch",
		"source+ID replay created a duplicate Elasticsearch document",
		"execution_summary.last_execution.status",
		"timestamp_override",
		"timestamp_override_fallback_disabled",
		".alerts-security.alerts-default/_search",
		"OpenShell SOC dashboard is incomplete",
		"detection_forbidden_status",
		"api_key_owner",
		"openshell_detection_service",

		"openshell_soc_analyst",
		"openshell_soc_detection_engineer",
		"APM intake and Logstash output credentials must be distinct",
	} {
		if !strings.Contains(verifier, required) {
			t.Fatalf("Elastic production-shaped verifier is missing %q", required)
		}
	}

	encoded, err = os.ReadFile("../elastic/provision-kibana.sh")
	if err != nil {
		t.Fatal(err)
	}
	provisioner := string(encoded)
	for _, required := range []string{
		"/api/saved_objects/_import?overwrite=true",
		"\"success\":true",
		"\"successCount\":26",
		"6 SOC dashboards with 11 live visualizations and 7 saved searches",
		"_security/user/openshell_detection_service",
		`--user "$detection_auth"`,
		`--request "$rule_method"`,
		"existing_status",
		"created or updated without destructive replacement",
		"/run/secrets/elastic-detection-password",
		"detection-service password has an invalid format",
	} {
		if !strings.Contains(provisioner, required) {
			t.Fatalf("Kibana SOC provisioner is missing %q", required)
		}
	}
	if strings.Contains(provisioner, `--request DELETE`) {
		t.Fatal("Kibana SOC provisioner destructively deletes rules during an update")
	}

	encoded, err = os.ReadFile("../run-elastic.sh")
	if err != nil {
		t.Fatal(err)
	}
	runElastic := string(encoded)
	for _, required := range []string{
		`DETECTION_PASSWORD_FILE="$TLS_DIR/elastic-detection-password"`,
		`chmod 0600`,
		`"$DETECTION_PASSWORD_FILE"`,
	} {
		if !strings.Contains(runElastic, required) {
			t.Fatalf("Elastic run path is missing detection secret control %q", required)
		}
	}
}
func TestRealDemoExternalFanOutIsExplicitAndIndependent(t *testing.T) {
	runEncoded, err := os.ReadFile("../run.sh")
	if err != nil {
		t.Fatal(err)
	}
	runScript := string(runEncoded)
	for _, required := range []string{
		"DEMO_EXTERNAL_DESTINATIONS",
		"DEMO_EXTERNAL_CLOUDEVENTS_URL must be a shell-safe HTTPS",
		"DEMO_EXTERNAL_OTLP_HTTP_ENDPOINT must be a shell-safe HTTPS",
		"external destination bearer token must contain at least 32 bytes",
		"external-destination-ca.crt",
		"compose.external.yaml",
	} {
		if !strings.Contains(runScript, required) {
			t.Fatalf("external run path is missing %q", required)
		}
	}

	exporterEncoded, err := os.ReadFile("../exporter.external.yaml")
	if err != nil {
		t.Fatal(err)
	}
	externalConfig := string(exporterEncoded)
	for _, required := range []string{
		"bearertokenauth/external_destination:",
		"filename: /run/secrets/external-destination-token",
		"cloudevents/external:",
		"authenticator: bearertokenauth/external_destination",
		"otlphttp/external:",
		"storage: file_storage",
		"cloudevents/dashboard",
		"otlphttp/dashboard",
		"file/recovery_events",
		"file/recovery_traces",
	} {
		if !strings.Contains(externalConfig, required) {
			t.Fatalf("external exporter overlay is missing %q", required)
		}
	}

	verifyEncoded, err := os.ReadFile("../verify-external.sh")
	if err != nil {
		t.Fatal(err)
	}
	verifyScript := string(verifyEncoded)
	for _, required := range []string{
		"CONFORMANCE_VERIFY_CORRELATED_TIMELINE=true",
		"CONFORMANCE_REAL_CORRELATION_EVIDENCE",
		"CONFORMANCE_CORRELATED_BACKEND_EVIDENCE",
		"external-correlated-timeline-verification.json",
	} {
		if !strings.Contains(verifyScript, required) {
			t.Fatalf("external verifier wrapper is missing %q", required)
		}
	}
	if strings.Contains(runScript, "DEMO_EXTERNAL_DESTINATION_TOKEN") {
		t.Fatal("external destination token must remain file-mounted")
	}
	composeEncoded, err := os.ReadFile("../compose.external.yaml")
	if err != nil {
		t.Fatal(err)
	}
	composeConfig := string(composeEncoded)
	if !strings.Contains(composeConfig, "external-destination-token:/run/secrets/external-destination-token:ro") ||
		strings.Contains(composeConfig, "DEMO_EXTERNAL_DESTINATION_TOKEN") {
		t.Fatal("external destination token is not an independent read-only file mount")
	}
	for _, forbidden := range []string{"NVIDIA_API_KEY", "NEMO_RELAY_OTLP_TOKEN", "DEMO_EXTERNAL_DESTINATION_TOKEN"} {
		if strings.Contains(verifyScript, forbidden) {
			t.Fatalf("external verifier wrapper references credential variable %q", forbidden)
		}
	}
}

func TestRealDemoWritesCredentialFreeCandidateBoundEvidence(t *testing.T) {
	runEncoded, err := os.ReadFile("../run.sh")
	if err != nil {
		t.Fatal(err)
	}
	runScript := string(runEncoded)
	for _, required := range []string{
		"DEMO_REPOSITORY_COMMIT=$(git -C \"$REPO_ROOT\" rev-parse HEAD)",
		"DEMO_EXPORTER_VERSION",
		"DEMO_BUILD_CREATED",
	} {
		if !strings.Contains(runScript, required) {
			t.Fatalf("demo build identity is missing %q", required)
		}
	}

	runtimeSource := strings.Index(runScript, `source "$ENV_FILE"`)
	currentRevision := strings.Index(runScript, `DEMO_REPOSITORY_COMMIT=$(git -C "$REPO_ROOT" rev-parse HEAD)`)
	if runtimeSource < 0 || currentRevision <= runtimeSource {
		t.Fatal("current candidate identity must replace persisted runtime metadata")
	}

	composeEncoded, err := os.ReadFile("../compose.yaml")
	if err != nil {
		t.Fatal(err)
	}
	compose := string(composeEncoded)
	for _, required := range []string{
		"VERSION: ${DEMO_EXPORTER_VERSION}",
		"VCS_REF: ${DEMO_REPOSITORY_COMMIT}",
		"CREATED: ${DEMO_BUILD_CREATED}",
	} {
		if !strings.Contains(compose, required) {
			t.Fatalf("exporter build metadata is missing %q", required)
		}
	}

	verifyEncoded, err := os.ReadFile("../verify.sh")
	if err != nil {
		t.Fatal(err)
	}
	verifyScript := string(verifyEncoded)
	for _, required := range []string{
		"real-gateway-correlation.json",
		"repository_commit",
		"repository_branch",
		`.candidate_bound == false`,
		`.candidate.images.exporter.revision == .candidate.repository_commit`,
		"worktree_clean",
		"candidate_bound",
		"exporter_image_revision",
		"gateway_image_ref",
		"gateway_image_id",
		"conformance_receiver_image_id",
		"hermes_image_id",
		"task_evidence_sha256",
		"created_file_sha256",
		"cloud_event_identities",
		"relay_span_identities",
		"--slurpfile cloud_event_identities",
		"--slurpfile relay_span_identities",
		`chmod 0600 "$cloud_event_identities_path" "$relay_span_identities_path"`,
		"trap cleanup_report_inputs EXIT HUP INT TERM",
		"relay_session_instance_id",
		"session_bridge",
		"hermes_atif",
		"unique_cloud_events",
		"unique_relay_spans",
		`(($report.task.cloud_events | length) == $report.counts.unique_cloud_events)`,
		`(($report.task.relay_spans | length) == $report.counts.unique_relay_spans)`,
		"sandbox_response_sha256",
		"sandbox_image_revision",
		"event_recovery_sha256",
		"trace_recovery_sha256",
		"chmod 0600 \"$evidence_path.tmp\"",
		"grep -Fq -- \"$protected_value\" \"$evidence_path\"",
		"status --porcelain --untracked-files=normal",
		"[ \"$exporter_image_revision\" = \"$repository_commit\" ]",
	} {
		if !strings.Contains(verifyScript, required) {
			t.Fatalf("real correlation evidence is missing %q", required)
		}
	}
	if count := strings.Count(verifyScript, "--arg agent_session_id"); count != 1 {
		t.Fatalf("agent session evidence argument count = %d, want 1", count)
	}
	if count := strings.Count(verifyScript, "--arg relay_session_instance_id"); count != 1 {
		t.Fatalf("Relay session evidence argument count = %d, want 1", count)
	}

	reportStart := strings.Index(verifyScript, "report=$(jq -n")
	if reportStart < 0 {
		t.Fatal("real correlation evidence builder is missing")
	}
	reportEnd := strings.Index(verifyScript[reportStart:], "\n\nevidence_dir=")
	if reportEnd < 0 {
		t.Fatal("real correlation evidence builder terminator is missing")
	}
	reportBuilder := verifyScript[reportStart : reportStart+reportEnd]
	for _, forbidden := range []string{
		"NEMO_RELAY_OTLP_TOKEN",
		"WEBAPP_INGEST_TOKEN",
		"NVIDIA_API_KEY",
	} {
		if strings.Contains(reportBuilder, forbidden) {
			t.Fatalf("qualification report builder references credential variable %q", forbidden)
		}
	}
}

func TestRealDemoProtectsAuthenticatedReceiverTransportWithTLS(t *testing.T) {
	t.Parallel()

	runEncoded, err := os.ReadFile("../run.sh")
	if err != nil {
		t.Fatal(err)
	}
	runScript := string(runEncoded)
	for _, required := range []string{
		"generate_server_certificate",
		"conformance-receiver.demo.internal",
		"DNS:conformance-receiver,IP:127.0.0.1",
		`wait_https "development conformance receiver" "https://127.0.0.1:8088/healthz" "$TLS_DIR/ca.crt"`,
	} {
		if !strings.Contains(runScript, required) {
			t.Fatalf("demo TLS bootstrap is missing %q", required)
		}
	}

	composeEncoded, err := os.ReadFile("../compose.yaml")
	if err != nil {
		t.Fatal(err)
	}
	compose := string(composeEncoded)
	for _, required := range []string{
		"WEBAPP_TLS_CERT_FILE: /run/secrets/conformance-receiver.crt",
		"WEBAPP_TLS_KEY_FILE: /run/secrets/conformance-receiver.key",
		"conformance-receiver.crt:/run/secrets/conformance-receiver.crt:ro",
		"conformance-receiver.key:/run/secrets/conformance-receiver.key:ro",
		"ca.crt:/run/secrets/demo-ca.crt:ro",
	} {
		if !strings.Contains(compose, required) {
			t.Fatalf("demo Compose TLS contract is missing %q", required)
		}
	}

	exporterEncoded, err := os.ReadFile("../exporter.yaml")
	if err != nil {
		t.Fatal(err)
	}
	exporterConfig := string(exporterEncoded)
	for _, required := range []string{
		"endpoint: https://conformance-receiver:8080/v1/events",
		"endpoint: https://conformance-receiver:8080",
		"ca_file: /run/secrets/demo-ca.crt",
	} {
		if !strings.Contains(exporterConfig, required) {
			t.Fatalf("demo exporter TLS contract is missing %q", required)
		}
	}
	cloudEventsStart := strings.Index(exporterConfig, "  cloudevents/dashboard:\n")
	if cloudEventsStart < 0 {
		t.Fatal("demo CloudEvents exporter section is missing")
	}
	cloudEventsEnd := strings.Index(exporterConfig[cloudEventsStart:], "\n  otlphttp/dashboard:\n")
	if cloudEventsEnd < 0 {
		t.Fatal("demo CloudEvents exporter section is missing")
	}
	cloudEventsConfig := exporterConfig[cloudEventsStart : cloudEventsStart+cloudEventsEnd]
	if strings.Contains(cloudEventsConfig, "allow_insecure_http") {
		t.Fatal("authenticated demo CloudEvents exporter still permits plaintext HTTP")
	}

	verifyEncoded, err := os.ReadFile("../verify.sh")
	if err != nil {
		t.Fatal(err)
	}
	verifyScript := string(verifyEncoded)
	if strings.Count(verifyScript, `--cacert "$SCRIPT_DIR/runtime/tls/ca.crt"`) < 2 {
		t.Fatal("demo verifier does not authenticate both HTTPS evidence queries")
	}
}

func TestElasticVerifierSelectsStructurallyValidDenial(t *testing.T) {
	encoded, err := os.ReadFile("../verify-elastic.sh")
	if err != nil {
		t.Fatal(err)
	}
	verifier := string(encoded)
	required := `{term:{"openshell.validation.status":"valid"}}`
	if !strings.Contains(verifier, required) {
		t.Fatalf("Elastic verifier does not select a structurally valid denial with %q", required)
	}
	forbidden := `openshell.validation.status.keyword`
	if strings.Contains(verifier, forbidden) {
		t.Fatalf("Elastic verifier uses nonexistent field %q", forbidden)
	}
}
