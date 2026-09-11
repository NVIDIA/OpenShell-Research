// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package integration

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/santhosh-tekuri/jsonschema/v6"
)

func TestPolicyReconciliationRecoverySchemaCompiles(t *testing.T) {
	encoded, err := os.ReadFile("qualification/policy-reconciliation-recovery-report-v1.schema.json")
	if err != nil {
		t.Fatal(err)
	}
	var document map[string]any
	if err := json.Unmarshal(encoded, &document); err != nil {
		t.Fatal(err)
	}
	compiler := jsonschema.NewCompiler()
	identifier := document["$id"].(string)
	if err := compiler.AddResource(identifier, document); err != nil {
		t.Fatal(err)
	}
	if _, err := compiler.Compile(identifier); err != nil {
		t.Fatal(err)
	}
}

func TestPolicyReconciliationRecoveryHarnessIsCandidateBound(t *testing.T) {
	encoded, err := os.ReadFile("qualification/qualify-policy-reconciliation-recovery.sh")
	if err != nil {
		t.Fatal(err)
	}
	script := string(encoded)
	for _, value := range []string{
		"git status --porcelain",
		"git rev-parse HEAD",
		"POLICY_RECOVERY_EXPORTER_IMAGE",
		"POLICY_RECOVERY_GATEWAY_IMAGE",
		"POLICY_RECOVERY_DISCONNECT_HOOK",
		"POLICY_RECOVERY_MUTATE_HOOK",
		"POLICY_RECOVERY_RESTART_HOOK",
		"POLICY_RECOVERY_API_FAILURE_ON_HOOK",
		"POLICY_RECOVERY_API_FAILURE_OFF_HOOK",
		"changed_snapshot_identity_count",
		"unchanged_snapshot_suppressed",
		"stable_source_id_pairs_after_repeat",
		"checkpoint_storage_reused",
		"review_token_key_leaks",
	} {
		if !strings.Contains(script, value) {
			t.Fatalf("recovery qualification is missing %q", value)
		}
	}
	for _, forbidden := range []string{
		"ApproveDraftChunk", "RejectDraftChunk", "EditDraftChunk",
		"ClearDraftPolicy", "CreateSandboxPolicy", "UpdateSandboxPolicy",
	} {
		if strings.Contains(script, forbidden) {
			t.Fatalf("recovery qualification links forbidden policy mutation %q", forbidden)
		}
	}
}

func TestPolicyReconciliationRecoveryOrdersExternalMutationBeforeRestart(t *testing.T) {
	encoded, err := os.ReadFile("qualification/qualify-policy-reconciliation-recovery.sh")
	if err != nil {
		t.Fatal(err)
	}
	script := string(encoded)
	disconnect := strings.Index(script, "\"$POLICY_RECOVERY_DISCONNECT_HOOK\"")
	mutate := strings.Index(script, "\"$POLICY_RECOVERY_MUTATE_HOOK\"")
	restart := strings.Index(script, "\"$POLICY_RECOVERY_RESTART_HOOK\"")
	if disconnect < 0 || mutate <= disconnect || restart <= mutate {
		t.Fatal("disconnect, external mutation, and restart are not ordered safely")
	}
}

func TestPolicyReconciliationRecoveryHarnessCompletesWithQualifiedSignals(t *testing.T) {
	if _, err := exec.LookPath("jq"); err != nil {
		t.Skip("jq is required to execute the qualification harness")
	}
	root := t.TempDir()
	evidence := filepath.Join(root, "evidence")
	checkpoints := filepath.Join(root, "checkpoints")
	hooks := filepath.Join(root, "hooks")
	fakeBin := filepath.Join(root, "bin")
	for _, directory := range []string{evidence, checkpoints, hooks, fakeBin} {
		if err := os.Mkdir(directory, 0o700); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.WriteFile(filepath.Join(checkpoints, "state.db"), []byte("checkpoint"), 0o600); err != nil {
		t.Fatal(err)
	}
	ledger := filepath.Join(root, "events.jsonl")
	successes := filepath.Join(root, "successes")
	failures := filepath.Join(root, "failures")
	identity := filepath.Join(root, "identity")
	for path, value := range map[string]string{successes: "1\n", failures: "0\n", identity: "instance-a\n"} {
		if err := os.WriteFile(path, []byte(value), 0o600); err != nil {
			t.Fatal(err)
		}
	}

	event := func(eventType, idCharacter, kind, consistency, policyVersion, warningCode string) string {
		original := map[string]any{"summary": eventType}
		if warningCode != "" {
			original["code"] = warningCode
		}
		value := map[string]any{
			"specversion": "1.0",
			"type":        eventType,
			"source":      "openshell://gateway/workspaces/default/sandboxes/sandbox-1/sources/policy.reconciliation",
			"subject":     "sandboxes/sandbox-1",
			"id":          "sha256:" + strings.Repeat(idCharacter, 64),
			"time":        "2026-08-31T00:00:00Z",
			"data": map[string]any{
				"observed_time": "2026-08-31T00:00:01Z",
				"acquisition":   map[string]any{"kind": kind, "consistency": consistency},
				"openshell":     map[string]any{"policy_version": policyVersion},
				"original":      original,
			},
		}
		encoded, err := json.Marshal(value)
		if err != nil {
			t.Fatal(err)
		}
		return string(encoded) + "\n"
	}
	baseline := event("com.nvidia.openshell.policy.draft.snapshot.v1", "a", "policy.draft.snapshot", "matched", "1", "")
	if err := os.WriteFile(ledger, []byte(baseline), 0o600); err != nil {
		t.Fatal(err)
	}
	changed := strings.Join([]string{
		event("com.nvidia.openshell.policy.draft.snapshot.v1", "b", "policy.draft.snapshot", "matched", "2", ""),
		event("com.nvidia.openshell.policy.draft.chunk.v1", "c", "policy.draft.chunk", "matched", "2", ""),
		event("com.nvidia.openshell.policy.draft.history.v1", "d", "policy.draft.history", "matched", "2", ""),
		event("com.nvidia.openshell.policy.status.v1", "e", "policy.status", "matched", "2", ""),
		event("com.nvidia.openshell.policy.revision.v1", "f", "policy.revision", "matched", "2", ""),
	}, "")
	changedFixture := filepath.Join(root, "changed.jsonl")
	warningFixture := filepath.Join(root, "warning.jsonl")
	if err := os.WriteFile(changedFixture, []byte(changed), 0o600); err != nil {
		t.Fatal(err)
	}
	warning := event("com.nvidia.openshell.policy.reconciliation.warning.v1", "9", "policy.reconciliation.warning", "unavailable", "2", "api_unavailable")
	if err := os.WriteFile(warningFixture, []byte(warning), 0o600); err != nil {
		t.Fatal(err)
	}

	writeHook := func(name, body string) string {
		t.Helper()
		path := filepath.Join(hooks, name)
		if err := os.WriteFile(path, []byte("#!/bin/sh\nset -eu\n"+body+"\n"), 0o700); err != nil {
			t.Fatal(err)
		}
		return path
	}
	disconnectHook := writeHook("disconnect", "exit 0")
	mutateHook := writeHook("mutate", "printf '%s\\n' '{\"approved_external_actuator\":true,\"mutation_id\":\"mutation-2\",\"expected_policy_version\":\"2\"}'")
	restartHook := writeHook("restart", fmt.Sprintf("printf 'instance-b\\n' > %q", identity))
	identityHook := writeHook("identity", fmt.Sprintf("cat %q", identity))
	failureOnHook := writeHook("failure-on", "exit 0")
	failureOffHook := writeHook("failure-off", "exit 0")
	increment := func(path string) string {
		return fmt.Sprintf("n=$(cat %q); n=$((n+1)); printf '%%s\\n' \"$n\" > %q", path, path)
	}
	reconcileHook := writeHook("reconcile", fmt.Sprintf(
		"case \"$1\" in\n"+
			"  changed) cat %q >> %q; %s ;;\n"+
			"  unchanged) %s ;;\n"+
			"  failure) cat %q >> %q; %s ;;\n"+
			"  recovery) %s ;;\n"+
			"  *) exit 2 ;;\n"+
			"esac",
		changedFixture, ledger, increment(successes), increment(successes),
		warningFixture, ledger, increment(failures), increment(successes),
	))
	fakeGit := filepath.Join(fakeBin, "git")
	if err := os.WriteFile(fakeGit, []byte("#!/bin/sh\ncase \"$1 $2\" in\n  'status --porcelain') exit 0 ;;\n  'rev-parse HEAD') printf '%s\\n' 1111111111111111111111111111111111111111 ;;\n  *) exit 2 ;;\nesac\n"), 0o700); err != nil {
		t.Fatal(err)
	}
	readCount := func(path string) string {
		encoded, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		return strings.TrimSpace(string(encoded))
	}
	server := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		if request.URL.Path == "/metrics" {
			_, _ = fmt.Fprintf(response,
				"openshell_exporter_policy_reconciliations %s\nopenshell_exporter_policy_reconciliation_failures %s\n",
				readCount(successes), readCount(failures))
			return
		}
		response.WriteHeader(http.StatusOK)
	}))
	defer server.Close()

	environment := []string{
		"PATH=" + fakeBin + string(os.PathListSeparator) + os.Getenv("PATH"),
		"POLICY_RECOVERY_RUN_ID=qualified",
		"POLICY_RECOVERY_EVIDENCE_DIR=" + evidence,
		"POLICY_RECOVERY_SANDBOX_ID=sandbox-1",
		"POLICY_RECOVERY_EXPORTER_IMAGE=registry/exporter@sha256:" + strings.Repeat("1", 64),
		"POLICY_RECOVERY_GATEWAY_IMAGE=registry/gateway@sha256:" + strings.Repeat("2", 64),
		"POLICY_RECOVERY_GATEWAY_VERSION=0.0.113",
		"POLICY_RECOVERY_CLOUDEVENTS_FILE=" + ledger,
		"POLICY_RECOVERY_CHECKPOINT_DIR=" + checkpoints,
		"POLICY_RECOVERY_DISCONNECT_HOOK=" + disconnectHook,
		"POLICY_RECOVERY_MUTATE_HOOK=" + mutateHook,
		"POLICY_RECOVERY_RESTART_HOOK=" + restartHook,
		"POLICY_RECOVERY_RECONCILE_HOOK=" + reconcileHook,
		"POLICY_RECOVERY_API_FAILURE_ON_HOOK=" + failureOnHook,
		"POLICY_RECOVERY_API_FAILURE_OFF_HOOK=" + failureOffHook,
		"POLICY_RECOVERY_EXPORTER_IDENTITY_HOOK=" + identityHook,
		"POLICY_RECOVERY_METRICS_URL=" + server.URL + "/metrics",
		"POLICY_RECOVERY_HEALTH_URL=" + server.URL + "/health",
		"POLICY_RECOVERY_TIMEOUT_SECONDS=5",
	}
	run := func(phase string) {
		t.Helper()
		command := exec.Command("sh", "qualification/qualify-policy-reconciliation-recovery.sh", phase)
		command.Env = append(os.Environ(), environment...)
		output, err := command.CombinedOutput()
		if err != nil {
			t.Fatalf("%s failed: %v\n%s", phase, err, output)
		}
	}
	run("prepare")
	run("execute")

	reportPath := filepath.Join(evidence, "qualified", "report.json")
	encoded, err := os.ReadFile(reportPath)
	if err != nil {
		t.Fatal(err)
	}
	var report map[string]any
	if err := json.Unmarshal(encoded, &report); err != nil {
		t.Fatal(err)
	}
	schemaBytes, err := os.ReadFile("qualification/policy-reconciliation-recovery-report-v1.schema.json")
	if err != nil {
		t.Fatal(err)
	}
	var schemaDocument any
	if err := json.Unmarshal(schemaBytes, &schemaDocument); err != nil {
		t.Fatal(err)
	}
	compiler := jsonschema.NewCompiler()
	compiler.AssertFormat()
	if err := compiler.AddResource("urn:openshell:policy-reconciliation-recovery-report:1.0", schemaDocument); err != nil {
		t.Fatal(err)
	}
	compiled, err := compiler.Compile("urn:openshell:policy-reconciliation-recovery-report:1.0")
	if err != nil {
		t.Fatal(err)
	}
	if err := compiled.Validate(report); err != nil {
		t.Fatalf("qualification report does not satisfy its schema: %v", err)
	}
	if report["complete"] != true {
		t.Fatalf("qualification report is incomplete: %s", encoded)
	}
	reconciliation := report["reconciliation"].(map[string]any)
	if reconciliation["expected_policy_version_observed"] != true || reconciliation["gap_count"].(float64) != 0 {
		t.Fatalf("qualification report lacks version binding or has a gap: %#v", reconciliation)
	}
}
