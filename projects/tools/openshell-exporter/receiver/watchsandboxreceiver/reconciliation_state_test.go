// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package watchsandboxreceiver

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"math"
	"strings"
	"testing"

	pb "github.com/NVIDIA/OpenShell/sdk/go/proto/openshellv1"
)

func TestPolicySequenceAppendsOnlySuffixAndSurvivesRestart(t *testing.T) {
	t.Parallel()
	config := createDefaultConfig().(*Config)
	config.PolicyReconciliation.Enabled = true
	config.PolicyReconciliation.IncludeHistory = true
	gateway := testReconciliationGateway()
	gateway.setPolicySnapshots(testPolicyHistory(2), testPolicyRevisions(2))
	store := newMemoryPolicyStorage()
	recorder := &reconciliationRecorder{}
	request := testReconciliationRequest()

	newReceiver := func() *watchReceiver {
		return newReconciliationTestReceiver(t, config, gateway, store, recorder)
	}
	newReceiver().reconcileSandbox(context.Background(), request)
	if got := len(recorder.snapshot()); got != 7 {
		t.Fatalf("initial records=%d, want draft, chunk, status, two revisions, and two history entries", got)
	}

	gateway.setPolicySnapshots(testPolicyHistory(3), testPolicyRevisions(3))
	newReceiver().reconcileSandbox(context.Background(), request)
	if got := len(recorder.snapshot()); got != 9 {
		t.Fatalf("append records=%d, want only one new revision and one new history entry", got)
	}
	if recorder.countKind("policy.reconciliation.warning") != 0 {
		t.Fatal("append-only growth was incorrectly reported as a continuity reset")
	}

	newReceiver().reconcileSandbox(context.Background(), request)
	if got := len(recorder.snapshot()); got != 9 {
		t.Fatalf("restart replayed unchanged policy evidence: records=%d", got)
	}
	encoded, _ := store.snapshot(policyStateKey(request.Sandbox.ID))
	state, err := decodePolicyState(encoded)
	if err != nil {
		t.Fatal(err)
	}
	if state.History.Count != 3 || state.Revisions.Count != 3 || len(encoded) > 512 {
		t.Fatalf("bounded checkpoint history=%d revisions=%d bytes=%d", state.History.Count, state.Revisions.Count, len(encoded))
	}
}

func TestPolicySequenceMutationAndTruncationReplayWithDiagnostics(t *testing.T) {
	t.Parallel()
	config := createDefaultConfig().(*Config)
	config.PolicyReconciliation.Enabled = true
	config.PolicyReconciliation.IncludeHistory = true
	gateway := testReconciliationGateway()
	history := testPolicyHistory(3)
	revisions := testPolicyRevisions(3)
	gateway.setPolicySnapshots(history, revisions)
	store := newMemoryPolicyStorage()
	recorder := &reconciliationRecorder{}
	r := newReconciliationTestReceiver(t, config, gateway, store, recorder)
	request := testReconciliationRequest()

	r.reconcileSandbox(context.Background(), request)
	if got := len(recorder.snapshot()); got != 9 {
		t.Fatalf("initial records=%d, want 9", got)
	}

	history[1] = &pb.DraftHistoryEntry{
		TimestampMs: history[1].GetTimestampMs(),
		EventType:   history[1].GetEventType(),
		Description: "corrected source description",
		ChunkId:     history[1].GetChunkId(),
	}
	revisions[1] = &pb.SandboxPolicyRevision{
		Version:     revisions[1].GetVersion(),
		PolicyHash:  "sha256:corrected",
		CreatedAtMs: revisions[1].GetCreatedAtMs(),
	}
	gateway.setPolicySnapshots(history, revisions)
	r.reconcileSandbox(context.Background(), request)
	if got := len(recorder.snapshot()); got != 17 {
		t.Fatalf("mutation records=%d, want two diagnostics plus complete three-entry replays", got)
	}
	if got := recorder.countKind("policy.reconciliation.warning"); got != 2 {
		t.Fatalf("mutation diagnostics=%d, want 2", got)
	}

	gateway.setPolicySnapshots(history[1:], revisions)
	r.reconcileSandbox(context.Background(), request)
	if got := len(recorder.snapshot()); got != 20 {
		t.Fatalf("truncation records=%d, want one diagnostic plus two current history entries", got)
	}
	if got := recorder.countKind("policy.reconciliation.warning"); got != 3 {
		t.Fatalf("truncation diagnostics=%d, want 3 total", got)
	}
	encoded, _ := store.snapshot(policyStateKey(request.Sandbox.ID))
	state, err := decodePolicyState(encoded)
	if err != nil {
		t.Fatal(err)
	}
	if state.History.Count != 2 || state.Revisions.Count != 3 {
		t.Fatalf("checkpoint after truncation history=%d revisions=%d", state.History.Count, state.Revisions.Count)
	}

	r.reconcileSandbox(context.Background(), request)
	if got := len(recorder.snapshot()); got != 20 {
		t.Fatalf("unchanged rebased snapshot replayed records: %d", got)
	}
}

func TestPolicyStateV1MigrationReplaysOnceAndShrinksCheckpoint(t *testing.T) {
	t.Parallel()
	config := createDefaultConfig().(*Config)
	config.PolicyReconciliation.Enabled = true
	config.PolicyReconciliation.IncludeHistory = true
	store := newMemoryPolicyStorage()
	legacyHash := strings.Repeat("a", 64)
	legacy := legacyPolicyReconciliationState{
		Version:        legacyPolicyStateVersion,
		ChunkHashes:    make(map[string]string, 1000),
		HistoryHashes:  make(map[string]string, 1000),
		RevisionHashes: make(map[string]string, 1000),
	}
	for index := range 1000 {
		key := fmt.Sprintf("legacy-%04d", index)
		legacy.ChunkHashes[key] = legacyHash
		legacy.HistoryHashes[key] = legacyHash
		legacy.RevisionHashes[key] = legacyHash
	}
	legacyEncoded, err := json.Marshal(legacy)
	if err != nil {
		t.Fatal(err)
	}
	if len(legacyEncoded) < 100000 || len(legacyEncoded) > maxPolicyStateReadBytes {
		t.Fatalf("legacy fixture bytes=%d, want a large accepted v1 checkpoint", len(legacyEncoded))
	}
	request := testReconciliationRequest()
	stateKey := policyStateKey(request.Sandbox.ID)
	store.putRaw(stateKey, legacyEncoded)
	recorder := &reconciliationRecorder{}
	r := newReconciliationTestReceiver(t, config, testReconciliationGateway(), store, recorder)

	r.reconcileSandbox(context.Background(), request)
	if got := len(recorder.snapshot()); got != 6 {
		t.Fatalf("migration records=%d, want one diagnostic and five replayed records", got)
	}
	if records := recorder.snapshot(); records[0].kind != "policy.reconciliation.warning" || records[0].body["code"] != "state_migrated" {
		t.Fatalf("migration diagnostic=%#v", records[0])
	}
	encoded, _ := store.snapshot(stateKey)
	if len(encoded) > 512 || bytes.Contains(encoded, []byte("_hashes")) {
		t.Fatalf("v2 checkpoint remained unbounded: bytes=%d state=%s", len(encoded), encoded)
	}
	state, err := decodePolicyState(encoded)
	if err != nil {
		t.Fatal(err)
	}
	if state.Version != policyStateVersion || state.migrated {
		t.Fatalf("persisted migration state=%#v", state)
	}

	r.reconcileSandbox(context.Background(), request)
	if got := len(recorder.snapshot()); got != 6 {
		t.Fatalf("completed migration replayed again: records=%d", got)
	}
	for _, record := range recorder.snapshot() {
		encoded, err := json.Marshal(record.body)
		if err != nil {
			t.Fatal(err)
		}
		if contains(string(encoded), "must-never-leak") {
			t.Fatalf("review token leaked during migration: %s", encoded)
		}
	}
}

func TestOversizedPolicyStateFailsClosedBeforeGatewayRead(t *testing.T) {
	t.Parallel()
	config := createDefaultConfig().(*Config)
	config.PolicyReconciliation.Enabled = true
	store := newMemoryPolicyStorage()
	request := testReconciliationRequest()
	oversized := bytes.Repeat([]byte("x"), maxPolicyStateReadBytes+1)
	store.putRaw(policyStateKey(request.Sandbox.ID), oversized)
	gateway := testReconciliationGateway()
	recorder := &reconciliationRecorder{}
	r := newReconciliationTestReceiver(t, config, gateway, store, recorder)

	r.reconcileSandbox(context.Background(), request)
	if gateway.getDraftCalls() != 0 || !r.policyUnhealthy.Load() {
		t.Fatalf("oversized checkpoint advanced gateway reads=%d unhealthy=%v", gateway.getDraftCalls(), r.policyUnhealthy.Load())
	}
	records := recorder.snapshot()
	if len(records) != 1 || records[0].kind != "policy.reconciliation.warning" || records[0].body["code"] != "state_read_failed" {
		t.Fatalf("oversized checkpoint diagnostic=%#v", records)
	}
	stored, _ := store.snapshot(policyStateKey(request.Sandbox.ID))
	if !bytes.Equal(stored, oversized) {
		t.Fatal("failed-closed read changed the operator-owned checkpoint")
	}
}

func TestPartialSequenceDeliveryDoesNotAdvancePastRejectedEvidence(t *testing.T) {
	t.Parallel()
	config := createDefaultConfig().(*Config)
	config.PolicyReconciliation.Enabled = true
	config.PolicyReconciliation.IncludeHistory = true
	gateway := testReconciliationGateway()
	gateway.setPolicySnapshots(testPolicyHistory(2), testPolicyRevisions(2))
	store := newMemoryPolicyStorage()
	recorder := &reconciliationRecorder{}
	r := newReconciliationTestReceiver(t, config, gateway, store, recorder)
	request := testReconciliationRequest()

	r.reconcileSandbox(context.Background(), request)
	gateway.setPolicySnapshots(testPolicyHistory(2), testPolicyRevisions(4))
	recorder.failAfterAdditionalCalls(2)
	r.reconcileSandbox(context.Background(), request)
	if !r.policyUnhealthy.Load() {
		t.Fatal("rejected policy evidence was incorrectly recorded as healthy")
	}
	encoded, _ := store.snapshot(policyStateKey(request.Sandbox.ID))
	state, err := decodePolicyState(encoded)
	if err != nil {
		t.Fatal(err)
	}
	if state.Revisions.Count != 2 {
		t.Fatalf("partial delivery advanced revision checkpoint to %d", state.Revisions.Count)
	}

	recorder.clearFailure()
	r.reconcileSandbox(context.Background(), request)
	if r.policyUnhealthy.Load() {
		t.Fatal("successful retry did not record recovery")
	}
	if got := recorder.countKind("policy.revision"); got != 5 {
		t.Fatalf("revision records=%d, want two initial, one accepted partial, and two replayed suffix records", got)
	}
	encoded, _ = store.snapshot(policyStateKey(request.Sandbox.ID))
	state, err = decodePolicyState(encoded)
	if err != nil {
		t.Fatal(err)
	}
	if state.Revisions.Count != 4 {
		t.Fatalf("successful retry checkpoint count=%d, want 4", state.Revisions.Count)
	}
}

func TestPolicyCheckpointEncodingRemainsBoundedAcrossSourceCounts(t *testing.T) {
	t.Parallel()
	digest := strings.Repeat("b", 64)
	for _, count := range []uint64{0, 1, 1000, 1000000, math.MaxUint64} {
		state := &policyReconciliationState{
			Version:    policyStateVersion,
			DraftHash:  digest,
			StatusHash: digest,
			Chunks:     policySequenceCheckpoint{Count: count, Digest: digest},
			History:    policySequenceCheckpoint{Count: count, Digest: digest},
			Revisions:  policySequenceCheckpoint{Count: count, Digest: digest},
		}
		encoded, err := encodePolicyState(state)
		if err != nil {
			t.Fatalf("count %d: %v", count, err)
		}
		if len(encoded) > 640 {
			t.Fatalf("count %d encoded checkpoint grew to %d bytes", count, len(encoded))
		}
	}
}

func FuzzPolicyStateDecoder(f *testing.F) {
	valid, err := encodePolicyState(&policyReconciliationState{Version: policyStateVersion})
	if err != nil {
		f.Fatal(err)
	}
	f.Add([]byte{})
	f.Add(valid)
	f.Add([]byte(`{"version":1,"chunk_hashes":{}}`))
	f.Add([]byte(`{"version":2,"history":{"count":1}}`))
	f.Add([]byte(`{"version":999}`))
	f.Add([]byte(`{"version":`))
	f.Fuzz(func(t *testing.T, encoded []byte) {
		state, decodeErr := decodePolicyState(encoded)
		if decodeErr != nil {
			return
		}
		roundTrip, encodeErr := encodePolicyState(state)
		if encodeErr != nil {
			t.Fatalf("accepted state could not be encoded: %v", encodeErr)
		}
		if len(roundTrip) > maxPolicyStateEncodedBytes {
			t.Fatalf("accepted state encoded to %d bytes", len(roundTrip))
		}
	})
}

func BenchmarkPolicyCheckpointEncoding(b *testing.B) {
	digest := strings.Repeat("c", 64)
	state := &policyReconciliationState{
		Version:    policyStateVersion,
		DraftHash:  digest,
		StatusHash: digest,
		Chunks:     policySequenceCheckpoint{Count: 1000000, Digest: digest},
		History:    policySequenceCheckpoint{Count: 1000000, Digest: digest},
		Revisions:  policySequenceCheckpoint{Count: 1000000, Digest: digest},
	}
	b.ReportAllocs()
	for range b.N {
		if _, err := encodePolicyState(state); err != nil {
			b.Fatal(err)
		}
	}
}

func testPolicyHistory(count int) []*pb.DraftHistoryEntry {
	entries := make([]*pb.DraftHistoryEntry, 0, count)
	for index := 1; index <= count; index++ {
		entries = append(entries, &pb.DraftHistoryEntry{
			TimestampMs: int64(1700000000000 + index),
			EventType:   "analysis_cycle",
			Description: fmt.Sprintf("history entry %d", index),
			ChunkId:     fmt.Sprintf("chunk-%d", index),
		})
	}
	return entries
}

func testPolicyRevisions(count int) []*pb.SandboxPolicyRevision {
	revisions := make([]*pb.SandboxPolicyRevision, 0, count)
	for version := count; version >= 1; version-- {
		revisions = append(revisions, &pb.SandboxPolicyRevision{
			Version:     uint32(version),
			PolicyHash:  fmt.Sprintf("sha256:policy-%d", version),
			CreatedAtMs: int64(1700000000000 + version),
		})
	}
	return revisions
}
