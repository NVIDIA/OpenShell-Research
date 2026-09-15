// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package watchsandboxreceiver

import (
	"encoding/json"
	"strings"
	"testing"

	pb "github.com/NVIDIA/OpenShell/sdk/go/proto/openshellv1"
	"github.com/NVIDIA/OpenShell/sdk/go/proto/sandboxv1"
)

func TestPolicyChunkHardExcludesReviewToken(t *testing.T) {
	config := createDefaultConfig().(*Config)
	config.PolicyReconciliation.Enabled = true
	chunk := &pb.PolicyChunk{
		Id:                           "chunk-1",
		Status:                       "pending",
		RuleName:                     "allow-example",
		ReviewToken:                  "opaque-review-authorization-secret",
		CurrentEffectivePolicyHash:   "sha256:current",
		CandidateEffectivePolicyHash: "sha256:candidate",
		CurrentEffectivePolicy:       &sandboxv1.SandboxPolicy{},
		CandidateEffectivePolicy:     &sandboxv1.SandboxPolicy{},
	}
	logs, err := draftChunkLogs(config, sandboxRef{ID: "sandbox-id", Name: "sandbox-name"}, "0.0.113", 7, chunk, reconciliationMetadata{Trigger: "draft_notification", NotificationDraftVersion: 7, Consistency: "matched"})
	if err != nil {
		t.Fatal(err)
	}
	record := logs.ResourceLogs().At(0).ScopeLogs().At(0).LogRecords().At(0)
	encoded, err := json.Marshal(record.Body().AsRaw())
	if err != nil {
		t.Fatal(err)
	}
	body := string(encoded)
	if strings.Contains(body, chunk.ReviewToken) || strings.Contains(body, "review_token\"") {
		t.Fatalf("review token leaked into policy evidence: %s", body)
	}
	if !strings.Contains(body, `"review_token_present":true`) {
		t.Fatalf("review token presence diagnostic is missing: %s", body)
	}
	if strings.Contains(body, "current_effective_policy\"") || strings.Contains(body, "candidate_effective_policy\"") {
		t.Fatalf("effective policy bodies were emitted without opt-in: %s", body)
	}
	for _, hash := range []string{"sha256:current", "sha256:candidate"} {
		if !strings.Contains(body, hash) {
			t.Fatalf("policy hash %q was lost: %s", hash, body)
		}
	}
}

func TestPolicyChunkEffectivePoliciesRequireExplicitOptIn(t *testing.T) {
	config := createDefaultConfig().(*Config)
	config.PolicyReconciliation.Enabled = true
	config.PolicyReconciliation.IncludeEffectivePolicies = true
	chunk := &pb.PolicyChunk{
		Id:                       "chunk-1",
		ReviewToken:              "never-export-me",
		CurrentEffectivePolicy:   &sandboxv1.SandboxPolicy{},
		CandidateEffectivePolicy: &sandboxv1.SandboxPolicy{},
	}
	logs, err := draftChunkLogs(config, sandboxRef{ID: "sandbox-id", Name: "sandbox-name"}, "0.0.113", 3, chunk, reconciliationMetadata{Trigger: "periodic", Consistency: "not_compared"})
	if err != nil {
		t.Fatal(err)
	}
	encoded, _ := json.Marshal(logs.ResourceLogs().At(0).ScopeLogs().At(0).LogRecords().At(0).Body().AsRaw())
	body := string(encoded)
	if !strings.Contains(body, "current_effective_policy") || !strings.Contains(body, "candidate_effective_policy") {
		t.Fatalf("opted-in policy bodies are missing: %s", body)
	}
	if strings.Contains(body, chunk.ReviewToken) {
		t.Fatalf("review token leaked with full policy opt-in: %s", body)
	}
}

func TestPolicyEventHashIgnoresReconciliationTrigger(t *testing.T) {
	config := createDefaultConfig().(*Config)
	response := &pb.GetDraftPolicyResponse{DraftVersion: 8, RollingSummary: "same snapshot"}
	first, err := draftSnapshotLogs(config, sandboxRef{ID: "sandbox-id", Name: "sandbox-name"}, "0.0.113", response, reconciliationMetadata{Trigger: "discovery", Consistency: "not_compared"})
	if err != nil {
		t.Fatal(err)
	}
	second, err := draftSnapshotLogs(config, sandboxRef{ID: "sandbox-id", Name: "sandbox-name"}, "0.0.113", response, reconciliationMetadata{Trigger: "periodic", Consistency: "not_compared"})
	if err != nil {
		t.Fatal(err)
	}
	if policyEventHash(first) != policyEventHash(second) {
		t.Fatal("poll trigger changed stable policy snapshot identity")
	}
}

func TestDraftConsistency(t *testing.T) {
	for _, test := range []struct {
		notification, draft uint64
		want                string
	}{
		{0, 5, "not_compared"}, {5, 5, "matched"}, {4, 5, "gateway_ahead"}, {6, 5, "notification_ahead"},
	} {
		got := draftConsistency(test.notification, &pb.GetDraftPolicyResponse{DraftVersion: test.draft})
		if got != test.want {
			t.Fatalf("draftConsistency(%d,%d)=%q want=%q", test.notification, test.draft, got, test.want)
		}
	}
}
