// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package main

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	pb "github.com/NVIDIA/OpenShell/sdk/go/proto/openshellv1"
	"google.golang.org/protobuf/proto"
)

func TestSanitizedProtoJSONExcludesReviewTokens(t *testing.T) {
	original := &pb.GetDraftPolicyResponse{Chunks: []*pb.PolicyChunk{{Id: "chunk-1", ReviewToken: "qualification-secret"}}}
	encoded, excluded, secrets, err := sanitizedProtoJSON(original)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(encoded), "review_token") || strings.Contains(string(encoded), "qualification-secret") {
		t.Fatalf("sanitized response leaked a review token: %s", encoded)
	}
	if excluded != 1 || len(secrets) != 1 || secrets[0] != "qualification-secret" {
		t.Fatalf("excluded=%d secrets=%v", excluded, secrets)
	}
	if original.GetChunks()[0].GetReviewToken() != "qualification-secret" {
		t.Fatal("sanitization mutated the source response")
	}
}

func TestInspectCloudEventsCapturesPolicyEvidence(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "events.jsonl")
	event := validPolicyEvent()
	encoded, err := json.Marshal(event)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, append(encoded, '\n'), 0o600); err != nil {
		t.Fatal(err)
	}
	result, err := inspectCloudEvents(path, "sandbox-123", []string{"never-export"}, []string{"com.nvidia.openshell.policy.draft.snapshot.v1"})
	if err != nil {
		t.Fatal(err)
	}
	if result.MatchedSandboxEvents != 1 || len(result.MissingTypes) != 0 || result.SensitiveKeyLeaks != 0 || result.SensitiveValueLeaks != 0 {
		t.Fatalf("unexpected evidence summary: %+v", result)
	}
	if result.Events[0].Consistency != "matched" || result.Events[0].SourceTime == "" || result.Events[0].ObservedTime == "" {
		t.Fatalf("timing or consistency evidence was lost: %+v", result.Events[0])
	}
}

func TestInspectCloudEventsDetectsSensitiveKeyAndValue(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "events.json")
	event := validPolicyEvent()
	event["data"].(map[string]any)["original"] = map[string]any{
		"review_token":         "never-export",
		"review_token_present": true,
	}
	encoded, err := json.Marshal([]any{event})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, encoded, 0o600); err != nil {
		t.Fatal(err)
	}
	result, err := inspectCloudEvents(path, "sandbox-123", []string{"never-export"}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if result.SensitiveKeyLeaks != 1 || result.SensitiveValueLeaks != 1 {
		t.Fatalf("sensitive leaks were not detected: %+v", result)
	}
}

func TestProbeMatchingRequiresEveryOperation(t *testing.T) {
	operations := []operationResult{
		{Operation: "openshell.v1.OpenShell/GetDraftPolicy", GRPCCode: "OK", Authorization: "authorized", Variant: "draft_present"},
		{Operation: "openshell.v1.OpenShell/GetDraftHistory", GRPCCode: "OK", Authorization: "authorized", Variant: "empty"},
		{Operation: "openshell.v1.OpenShell/GetSandboxPolicyStatus", GRPCCode: "OK", Authorization: "authorized", Variant: "policy_present"},
		{Operation: "openshell.v1.OpenShell/ListSandboxPolicies", GRPCCode: "OK", Authorization: "authorized", Variant: "present"},
	}
	probe := probeResult{Name: "draft_present", Expectation: "authorized_draft_present", Configured: true, Operations: operations}
	if !probeMatches(probe) {
		t.Fatal("valid primary probe did not match")
	}
	probe.Operations = probe.Operations[:3]
	if probeMatches(probe) {
		t.Fatal("partial probe matched")
	}
}

func TestValidateOutputPathRequiresSecureNewFile(t *testing.T) {
	root := t.TempDir()
	if err := os.Chmod(root, 0o700); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(root, "report.json")
	if err := validateOutputPath(path); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, nil, 0o600); err != nil {
		t.Fatal(err)
	}
	if err := validateOutputPath(path); err == nil {
		t.Fatal("existing report path was accepted")
	}
}

func TestSafeSummaryAndExpectedEventTypes(t *testing.T) {
	started := time.Now().UTC()
	finished := started.Add(time.Millisecond)
	draft := &pb.GetDraftPolicyResponse{
		DraftVersion:     4,
		LastAnalyzedAtMs: 1234,
		Chunks:           []*pb.PolicyChunk{{Id: "chunk-1"}},
	}
	draftResult, _ := summarizeOperation("openshell.v1.OpenShell/GetDraftPolicy", "default", "agent", started, finished, draft, nil)
	historyResult, _ := summarizeOperation("openshell.v1.OpenShell/GetDraftHistory", "default", "agent", started, finished, &pb.GetDraftHistoryResponse{Entries: []*pb.DraftHistoryEntry{{TimestampMs: 2345}}}, nil)
	statusResult, _ := summarizeOperation("openshell.v1.OpenShell/GetSandboxPolicyStatus", "default", "agent", started, finished, &pb.GetSandboxPolicyStatusResponse{ActiveVersion: 3, Revision: &pb.SandboxPolicyRevision{Version: 3, CreatedAtMs: 3456}}, nil)
	revisionsResult, _ := summarizeOperation("openshell.v1.OpenShell/ListSandboxPolicies", "default", "agent", started, finished, &pb.ListSandboxPoliciesResponse{Revisions: []*pb.SandboxPolicyRevision{{Version: 3}, {Version: 2}}}, nil)
	probe := probeResult{Operations: []operationResult{draftResult, historyResult, statusResult, revisionsResult}}
	want := []string{
		"com.nvidia.openshell.policy.draft.chunk.v1",
		"com.nvidia.openshell.policy.draft.history.v1",
		"com.nvidia.openshell.policy.draft.snapshot.v1",
		"com.nvidia.openshell.policy.revision.v1",
		"com.nvidia.openshell.policy.status.v1",
	}
	if got := expectedEventTypes(probe); !reflect.DeepEqual(got, want) {
		t.Fatalf("expected types=%v want=%v", got, want)
	}
	if draftResult.Variant != "draft_present" || historyResult.Variant != "present" || statusResult.Variant != "policy_present" || revisionsResult.Variant != "present" {
		t.Fatalf("unexpected variants: %q %q %q %q", draftResult.Variant, historyResult.Variant, statusResult.Variant, revisionsResult.Variant)
	}
	if draftResult.ResponseSHA256 == "" || draftResult.GRPCCode != "OK" || draftResult.Authorization != "authorized" {
		t.Fatalf("successful operation metadata is incomplete: %+v", draftResult)
	}
}

func TestSafeSummaryEmptyVariants(t *testing.T) {
	for name, message := range map[string]proto.Message{
		"draft":     &pb.GetDraftPolicyResponse{},
		"history":   &pb.GetDraftHistoryResponse{},
		"status":    &pb.GetSandboxPolicyStatusResponse{},
		"revisions": &pb.ListSandboxPoliciesResponse{},
	} {
		t.Run(name, func(t *testing.T) {
			_, variant := safeSummary(message)
			if variant != "no_draft" && variant != "empty" && variant != "no_policy" {
				t.Fatalf("unexpected empty variant %q", variant)
			}
		})
	}
}

func TestUnobservedVariantsAreExplicitAndSorted(t *testing.T) {
	probes := []probeResult{
		{Name: "unauthorized", Configured: false},
		{Name: "draft_present", Configured: true, Matched: true},
		{Name: "no_draft", Configured: true, Matched: false},
	}
	want := []string{"no_draft:expectation_not_observed", "unauthorized:not_configured"}
	if got := unobservedVariants(probes); !reflect.DeepEqual(got, want) {
		t.Fatalf("unobserved=%v want=%v", got, want)
	}
}

func TestDecodeJSONValuesAndSensitiveNames(t *testing.T) {
	values, err := decodeJSONValues(bytes.NewBufferString(`[{"one":1}] {"two":2}`))
	if err != nil {
		t.Fatal(err)
	}
	if got := len(flattenJSONArrays(values)); got != 2 {
		t.Fatalf("flattened values=%d", got)
	}
	for _, name := range []string{"review_token", "reviewToken", "REVIEW_TOKEN"} {
		if !sensitiveName(name) {
			t.Fatalf("%q was not classified as sensitive", name)
		}
	}
	if sensitiveName("review_token_present") {
		t.Fatal("safe presence diagnostic was classified as a secret")
	}
}

func TestSecretFileAndAtomicReport(t *testing.T) {
	root := t.TempDir()
	if err := os.Chmod(root, 0o700); err != nil {
		t.Fatal(err)
	}
	secretPath := filepath.Join(root, "token")
	if err := os.WriteFile(secretPath, []byte("  token-value\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if token, err := readSecretFile(secretPath); err != nil || token != "token-value" {
		t.Fatalf("token=%q err=%v", token, err)
	}
	reportPath := filepath.Join(root, "report.json")
	value := report{SchemaVersion: reportSchemaVersion, UnobservedVariants: []string{}, Limitations: []string{"test"}}
	if err := writeReport(reportPath, value); err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(reportPath)
	if err != nil {
		t.Fatal(err)
	}
	if info.Mode().Perm() != 0o600 {
		t.Fatalf("report mode=%o", info.Mode().Perm())
	}
}

func validPolicyEvent() map[string]any {
	return map[string]any{
		"specversion": "1.0",
		"id":          "sha256:" + strings.Repeat("a", 64),
		"source":      "openshell://gateway/workspaces/default/sandboxes/sandbox-123/sources/policy.draft.snapshot",
		"subject":     "sandboxes/sandbox-123",
		"type":        "com.nvidia.openshell.policy.draft.snapshot.v1",
		"time":        time.Now().UTC().Format(time.RFC3339Nano),
		"data": map[string]any{
			"observed_time": time.Now().UTC().Format(time.RFC3339Nano),
			"acquisition": map[string]any{
				"kind":        "policy.draft.snapshot",
				"consistency": "matched",
			},
		},
	}
}
