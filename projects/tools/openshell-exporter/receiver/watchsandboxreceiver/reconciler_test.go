// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package watchsandboxreceiver

import (
	"context"
	"encoding/json"
	"errors"
	"reflect"
	"sync"
	"testing"

	pb "github.com/NVIDIA/OpenShell/sdk/go/proto/openshellv1"
	"go.opentelemetry.io/collector/consumer"
	"go.opentelemetry.io/collector/consumer/consumererror"
	"go.opentelemetry.io/collector/extension/xextension/storage"
	"go.opentelemetry.io/collector/pdata/plog"
	"go.opentelemetry.io/collector/receiver"
)

type reconciliationGateway struct {
	mu           sync.Mutex
	draft        *pb.GetDraftPolicyResponse
	draftErr     error
	history      *pb.GetDraftHistoryResponse
	status       *pb.GetSandboxPolicyStatusResponse
	revisions    []*pb.SandboxPolicyRevision
	revisionsErr error
	draftCalls   int
}

func (g *reconciliationGateway) List(context.Context) ([]sandboxRef, error)         { return nil, nil }
func (g *reconciliationGateway) Watch(context.Context, string) (eventStream, error) { return nil, nil }
func (g *reconciliationGateway) GatewayVersion(context.Context) (string, error) {
	return "0.0.113", nil
}
func (g *reconciliationGateway) GetDraft(context.Context, string) (*pb.GetDraftPolicyResponse, error) {
	g.mu.Lock()
	defer g.mu.Unlock()
	g.draftCalls++
	return g.draft, g.draftErr
}
func (g *reconciliationGateway) GetDraftHistory(context.Context, string) (*pb.GetDraftHistoryResponse, error) {
	g.mu.Lock()
	defer g.mu.Unlock()
	if g.history != nil {
		return g.history, nil
	}
	return &pb.GetDraftHistoryResponse{Entries: []*pb.DraftHistoryEntry{{TimestampMs: 1700000000000, EventType: "analysis_cycle", ChunkId: "chunk-1"}}}, nil
}
func (g *reconciliationGateway) GetPolicyStatus(context.Context, string) (*pb.GetSandboxPolicyStatusResponse, error) {
	g.mu.Lock()
	defer g.mu.Unlock()
	if g.status != nil {
		return g.status, nil
	}
	return &pb.GetSandboxPolicyStatusResponse{ActiveVersion: 3, Revision: &pb.SandboxPolicyRevision{Version: 3, PolicyHash: "sha256:active"}}, nil
}
func (g *reconciliationGateway) ListPolicyRevisions(context.Context, string) ([]*pb.SandboxPolicyRevision, error) {
	g.mu.Lock()
	defer g.mu.Unlock()
	if g.revisions != nil {
		return append([]*pb.SandboxPolicyRevision(nil), g.revisions...), g.revisionsErr
	}
	return []*pb.SandboxPolicyRevision{{Version: 3, PolicyHash: "sha256:active"}}, nil
}

func (g *reconciliationGateway) setPolicyRevisionResult(revisions []*pb.SandboxPolicyRevision, err error) {
	g.mu.Lock()
	defer g.mu.Unlock()
	g.revisions = append([]*pb.SandboxPolicyRevision(nil), revisions...)
	g.revisionsErr = err
}
func (g *reconciliationGateway) Close() error { return nil }

func (g *reconciliationGateway) setDraft(response *pb.GetDraftPolicyResponse, err error) {
	g.mu.Lock()
	defer g.mu.Unlock()
	g.draft = response
	g.draftErr = err
}

func (g *reconciliationGateway) getDraftCalls() int {
	g.mu.Lock()
	defer g.mu.Unlock()
	return g.draftCalls
}

func (g *reconciliationGateway) setPolicySnapshots(history []*pb.DraftHistoryEntry, revisions []*pb.SandboxPolicyRevision) {
	g.mu.Lock()
	defer g.mu.Unlock()
	g.history = &pb.GetDraftHistoryResponse{Entries: append([]*pb.DraftHistoryEntry(nil), history...)}
	g.revisions = append([]*pb.SandboxPolicyRevision(nil), revisions...)
}

type memoryPolicyStorage struct {
	mu       sync.Mutex
	values   map[string][]byte
	getErr   error
	setErr   error
	setCalls int
}

func newMemoryPolicyStorage() *memoryPolicyStorage {
	return &memoryPolicyStorage{values: map[string][]byte{}}
}
func (s *memoryPolicyStorage) Get(_ context.Context, key string) ([]byte, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.getErr != nil {
		return nil, s.getErr
	}
	return append([]byte(nil), s.values[key]...), nil
}
func (s *memoryPolicyStorage) Set(_ context.Context, key string, value []byte) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.setCalls++
	if s.setErr != nil {
		return s.setErr
	}
	s.values[key] = append([]byte(nil), value...)
	return nil
}
func (s *memoryPolicyStorage) Delete(_ context.Context, key string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	delete(s.values, key)
	return nil
}
func (s *memoryPolicyStorage) Batch(context.Context, ...*storage.Operation) error { return nil }
func (s *memoryPolicyStorage) Close(context.Context) error                        { return nil }

func (s *memoryPolicyStorage) setFailures(getErr, setErr error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.getErr = getErr
	s.setErr = setErr
}

func (s *memoryPolicyStorage) putRaw(key string, value []byte) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.values[key] = append([]byte(nil), value...)
}

func (s *memoryPolicyStorage) snapshot(key string) ([]byte, int) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return append([]byte(nil), s.values[key]...), s.setCalls
}

type reconciliationRecord struct {
	kind string
	body map[string]any
}

type reconciliationRecorder struct {
	mu      sync.Mutex
	records []reconciliationRecord
	calls   int
	failAt  int
}

func (r *reconciliationRecorder) consume(_ context.Context, logs plog.Logs) error {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.calls++
	if r.failAt > 0 && r.calls == r.failAt {
		return consumererror.NewPermanent(errors.New("injected downstream rejection"))
	}
	for i := 0; i < logs.ResourceLogs().Len(); i++ {
		for j := 0; j < logs.ResourceLogs().At(i).ScopeLogs().Len(); j++ {
			records := logs.ResourceLogs().At(i).ScopeLogs().At(j).LogRecords()
			for k := 0; k < records.Len(); k++ {
				record := records.At(k)
				kind, _ := record.Attributes().Get("openshell.acquisition.kind")
				body, _ := record.Body().AsRaw().(map[string]any)
				r.records = append(r.records, reconciliationRecord{kind: kind.Str(), body: body})
			}
		}
	}
	return nil
}

func (r *reconciliationRecorder) failAfterAdditionalCalls(additional int) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.failAt = r.calls + additional
}

func (r *reconciliationRecorder) clearFailure() {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.failAt = 0
}

func (r *reconciliationRecorder) snapshot() []reconciliationRecord {
	r.mu.Lock()
	defer r.mu.Unlock()
	return append([]reconciliationRecord(nil), r.records...)
}

func (r *reconciliationRecorder) countKind(kind string) int {
	count := 0
	for _, record := range r.snapshot() {
		if record.kind == kind {
			count++
		}
	}
	return count
}

func testReconciliationGateway() *reconciliationGateway {
	return &reconciliationGateway{draft: &pb.GetDraftPolicyResponse{
		DraftVersion:   7,
		RollingSummary: "one proposed endpoint",
		Chunks: []*pb.PolicyChunk{{
			Id: "chunk-1", Status: "pending", ReviewToken: "must-never-leak",
			CurrentEffectivePolicyHash: "sha256:current", CandidateEffectivePolicyHash: "sha256:candidate",
		}},
	}}
}

func newReconciliationTestReceiver(t *testing.T, config *Config, gateway gatewayClient, store storage.Client, recorder *reconciliationRecorder) *watchReceiver {
	t.Helper()
	next, err := consumer.NewLogs(recorder.consume)
	if err != nil {
		t.Fatal(err)
	}
	r := newWatchReceiver(config, receiver.Settings{}, next)
	r.client = gateway
	r.gatewayVersion = "0.0.113"
	r.policyStorage = store
	return r
}

func testReconciliationRequest() reconciliationRequest {
	return reconciliationRequest{
		Sandbox:                  sandboxRef{ID: "sandbox-id", Name: "sandbox-name"},
		Trigger:                  "test",
		NotificationDraftVersion: 7,
	}
}

func TestReconciliationPersistsDeduplicationAcrossReceiverRestart(t *testing.T) {
	config := createDefaultConfig().(*Config)
	config.PolicyReconciliation.Enabled = true
	config.PolicyReconciliation.IncludeHistory = true
	config.PolicyReconciliation.IncludeEffectivePolicies = false
	gateway := &reconciliationGateway{draft: &pb.GetDraftPolicyResponse{
		DraftVersion:   7,
		RollingSummary: "one proposed endpoint",
		Chunks:         []*pb.PolicyChunk{{Id: "chunk-1", Status: "pending", ReviewToken: "must-never-leak", CurrentEffectivePolicyHash: "sha256:current", CandidateEffectivePolicyHash: "sha256:candidate"}},
	}}
	store := newMemoryPolicyStorage()
	var mu sync.Mutex
	bodies := make([]any, 0)
	next, err := consumer.NewLogs(func(_ context.Context, logs plog.Logs) error {
		mu.Lock()
		defer mu.Unlock()
		for i := 0; i < logs.ResourceLogs().Len(); i++ {
			for j := 0; j < logs.ResourceLogs().At(i).ScopeLogs().Len(); j++ {
				records := logs.ResourceLogs().At(i).ScopeLogs().At(j).LogRecords()
				for k := 0; k < records.Len(); k++ {
					bodies = append(bodies, records.At(k).Body().AsRaw())
				}
			}
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	newReceiver := func() *watchReceiver {
		r := newWatchReceiver(config, receiver.Settings{}, next)
		r.client = gateway
		r.gatewayVersion = "0.0.113"
		r.policyStorage = store
		return r
	}
	request := reconciliationRequest{Sandbox: sandboxRef{ID: "sandbox-id", Name: "sandbox-name"}, Trigger: "test", NotificationDraftVersion: 7}
	newReceiver().reconcileSandbox(context.Background(), request)
	if len(bodies) != 5 {
		t.Fatalf("first reconciliation emitted %d records, want 5", len(bodies))
	}
	for _, body := range bodies {
		encoded, _ := json.Marshal(body)
		if string(encoded) == "" || contains(string(encoded), "must-never-leak") {
			t.Fatalf("review token leaked: %s", encoded)
		}
	}
	newReceiver().reconcileSandbox(context.Background(), request)
	if len(bodies) != 5 {
		t.Fatalf("restart replay emitted unchanged records: total=%d want=5", len(bodies))
	}
}

func TestPartialReconciliationDoesNotAdvanceSuccessfulState(t *testing.T) {
	t.Parallel()
	config := createDefaultConfig().(*Config)
	store := newMemoryPolicyStorage()
	gateway := &reconciliationGateway{draftErr: errors.New("draft read unavailable")}
	next, err := consumer.NewLogs(func(context.Context, plog.Logs) error { return nil })
	if err != nil {
		t.Fatal(err)
	}
	r := newWatchReceiver(config, receiver.Settings{}, next)
	r.client = gateway
	r.gatewayVersion = "0.0.113"
	r.policyStorage = store
	request := reconciliationRequest{
		Sandbox:                  sandboxRef{ID: "sandbox-id", Name: "sandbox-name"},
		Trigger:                  "test",
		NotificationDraftVersion: 7,
	}

	r.reconcileSandbox(context.Background(), request)
	if !r.policyUnhealthy.Load() {
		t.Fatal("partial reconciliation was incorrectly recorded as successful")
	}

	gateway.setDraft(&pb.GetDraftPolicyResponse{
		DraftVersion: 7,
		Chunks:       []*pb.PolicyChunk{{Id: "chunk-1", Status: "pending"}},
	}, nil)
	r.reconcileSandbox(context.Background(), request)
	if r.policyUnhealthy.Load() {
		t.Fatal("complete reconciliation did not record recovery")
	}
}

func TestPolicyStateWriteFailureReplaysEvidenceAndRecovers(t *testing.T) {
	t.Parallel()
	config := createDefaultConfig().(*Config)
	config.PolicyReconciliation.Enabled = true
	config.PolicyReconciliation.IncludeHistory = true
	store := newMemoryPolicyStorage()
	store.setFailures(nil, errors.New("injected checkpoint write failure"))
	gateway := testReconciliationGateway()
	recorder := &reconciliationRecorder{}
	r := newReconciliationTestReceiver(t, config, gateway, store, recorder)
	request := testReconciliationRequest()

	r.reconcileSandbox(context.Background(), request)
	if !r.policyUnhealthy.Load() {
		t.Fatal("checkpoint write failure was incorrectly recorded as healthy")
	}
	if got := recorder.countKind("policy.reconciliation.warning"); got != 1 {
		t.Fatalf("checkpoint write failure warnings=%d, want 1", got)
	}
	if encoded, _ := store.snapshot(policyStateKey(request.Sandbox.ID)); len(encoded) != 0 {
		t.Fatalf("failed checkpoint write persisted %d bytes", len(encoded))
	}

	store.setFailures(nil, nil)
	r.reconcileSandbox(context.Background(), request)
	if r.policyUnhealthy.Load() {
		t.Fatal("successful checkpoint retry did not record recovery")
	}
	encoded, setCalls := store.snapshot(policyStateKey(request.Sandbox.ID))
	if len(encoded) == 0 || setCalls != 2 {
		t.Fatalf("checkpoint after recovery bytes=%d set calls=%d, want persisted state after one failed and one successful write", len(encoded), setCalls)
	}
	records := recorder.snapshot()
	if got := len(records); got != 11 {
		t.Fatalf("records after replay=%d, want five evidence records, one warning, and five identity-stable replays", got)
	}
	if records[5].kind != "policy.reconciliation.warning" || records[5].body["code"] != "state_write_failed" {
		t.Fatalf("checkpoint failure diagnostic=%#v", records[5])
	}
	if !reflect.DeepEqual(records[:5], records[6:11]) {
		t.Fatalf("checkpoint retry changed replay evidence: first=%#v replay=%#v", records[:5], records[6:11])
	}

	r.reconcileSandbox(context.Background(), request)
	if got := len(recorder.snapshot()); got != 11 {
		t.Fatalf("successful checkpoint did not deduplicate subsequent reconciliation: records=%d", got)
	}
	for _, record := range recorder.snapshot() {
		encoded, err := json.Marshal(record.body)
		if err != nil {
			t.Fatal(err)
		}
		if contains(string(encoded), "must-never-leak") {
			t.Fatalf("review token leaked during failure or replay: %s", encoded)
		}
	}
}

func TestIncompletePolicyRevisionPaginationExportsPartialEvidenceWithoutAdvancingCheckpoint(t *testing.T) {
	t.Parallel()
	config := createDefaultConfig().(*Config)
	config.PolicyReconciliation.Enabled = true
	config.PolicyReconciliation.IncludeHistory = true
	gateway := testReconciliationGateway()
	partial := testPolicyRevisions(2)
	gateway.setPolicyRevisionResult(partial, &policyRevisionPaginationError{
		Reason:          policyRevisionLimitExceeded,
		Retained:        2,
		ObservedAtLeast: 3,
		Limit:           2,
	})
	store := newMemoryPolicyStorage()
	recorder := &reconciliationRecorder{}
	r := newReconciliationTestReceiver(t, config, gateway, store, recorder)
	request := testReconciliationRequest()

	r.reconcileSandbox(context.Background(), request)
	if !r.policyUnhealthy.Load() {
		t.Fatal("incomplete revision pagination was incorrectly recorded as healthy")
	}
	if got := recorder.countKind("policy.revision"); got != 2 {
		t.Fatalf("partial revision records=%d, want 2", got)
	}
	if !hasReconciliationWarningCode(recorder.snapshot(), policyRevisionLimitExceeded) {
		t.Fatalf("missing %q warning in %#v", policyRevisionLimitExceeded, recorder.snapshot())
	}
	encoded, _ := store.snapshot(policyStateKey(request.Sandbox.ID))
	state, err := decodePolicyState(encoded)
	if err != nil {
		t.Fatal(err)
	}
	if state.Revisions.Count != 0 || state.Revisions.Digest != "" {
		t.Fatalf("incomplete pagination advanced checkpoint: %#v", state.Revisions)
	}
	first := recordsOfKind(recorder.snapshot(), "policy.revision")

	r.reconcileSandbox(context.Background(), request)
	second := recordsOfKind(recorder.snapshot(), "policy.revision")
	if len(second) != 4 || !reflect.DeepEqual(first, second[2:]) {
		t.Fatalf("partial replay identity changed: first=%#v all=%#v", first, second)
	}
}

func recordsOfKind(records []reconciliationRecord, kind string) []reconciliationRecord {
	result := make([]reconciliationRecord, 0)
	for _, record := range records {
		if record.kind == kind {
			result = append(result, record)
		}
	}
	return result
}

func hasReconciliationWarningCode(records []reconciliationRecord, code string) bool {
	for _, record := range records {
		if record.kind == "policy.reconciliation.warning" && record.body["code"] == code {
			return true
		}
	}
	return false
}

func TestPolicyStateReadFailureAndCorruptionRequireRepairThenRecover(t *testing.T) {
	t.Parallel()
	config := createDefaultConfig().(*Config)
	config.PolicyReconciliation.Enabled = true
	config.PolicyReconciliation.IncludeHistory = true
	store := newMemoryPolicyStorage()
	store.setFailures(errors.New("injected checkpoint read failure"), nil)
	gateway := testReconciliationGateway()
	recorder := &reconciliationRecorder{}
	r := newReconciliationTestReceiver(t, config, gateway, store, recorder)
	request := testReconciliationRequest()
	stateKey := policyStateKey(request.Sandbox.ID)

	r.reconcileSandbox(context.Background(), request)
	if gateway.getDraftCalls() != 0 || !r.policyUnhealthy.Load() {
		t.Fatalf("state read failure advanced source reads=%d unhealthy=%v", gateway.getDraftCalls(), r.policyUnhealthy.Load())
	}
	store.setFailures(nil, nil)
	store.putRaw(stateKey, []byte(`{"version":1,"chunk_hashes":`))
	r.reconcileSandbox(context.Background(), request)
	if gateway.getDraftCalls() != 0 {
		t.Fatalf("corrupt state advanced source reads=%d", gateway.getDraftCalls())
	}
	if got := recorder.countKind("policy.reconciliation.warning"); got != 2 {
		t.Fatalf("state failure warnings=%d, want one read-failure and one corruption warning", got)
	}

	if err := store.Delete(context.Background(), stateKey); err != nil {
		t.Fatal(err)
	}
	r.reconcileSandbox(context.Background(), request)
	if gateway.getDraftCalls() != 1 || r.policyUnhealthy.Load() {
		t.Fatalf("repaired state recovery calls=%d unhealthy=%v", gateway.getDraftCalls(), r.policyUnhealthy.Load())
	}
	if encoded, _ := store.snapshot(stateKey); len(encoded) == 0 {
		t.Fatal("recovered reconciliation did not persist a fresh checkpoint")
	}
	if got := len(recorder.snapshot()); got != 7 {
		t.Fatalf("records after repair=%d, want two warnings and five evidence records", got)
	}
}

func TestPolicyReconciliationQueueFullEmitsGapAndRetriesAfterCapacityReturns(t *testing.T) {
	t.Parallel()
	config := createDefaultConfig().(*Config)
	config.PolicyReconciliation.Enabled = true
	recorder := &reconciliationRecorder{}
	next, err := consumer.NewLogs(recorder.consume)
	if err != nil {
		t.Fatal(err)
	}
	r := newWatchReceiver(config, receiver.Settings{}, next)
	r.gatewayVersion = "0.0.113"
	r.reconcileCh = make(chan reconciliationRequest, 1)
	r.reconcilePending = make(map[string]struct{})
	queued := sandboxRef{ID: "queued-sandbox", Name: "queued"}
	rejected := sandboxRef{ID: "rejected-sandbox", Name: "rejected"}

	r.enqueuePolicyReconciliation(context.Background(), queued, "periodic", 0)
	r.enqueuePolicyReconciliation(context.Background(), rejected, "periodic", 0)
	if len(r.reconcileCh) != 1 {
		t.Fatalf("queue length=%d, want bounded capacity 1", len(r.reconcileCh))
	}
	r.reconcileMu.Lock()
	_, queuedPending := r.reconcilePending[queued.ID]
	_, rejectedPending := r.reconcilePending[rejected.ID]
	r.reconcileMu.Unlock()
	if !queuedPending || rejectedPending {
		t.Fatalf("pending state queued=%v rejected=%v, rejected work must be retryable", queuedPending, rejectedPending)
	}
	records := recorder.snapshot()
	if len(records) != 1 || records[0].kind != "policy.reconciliation.warning" || records[0].body["code"] != "queue_full" {
		t.Fatalf("queue saturation evidence=%#v", records)
	}

	dequeued := <-r.reconcileCh
	r.clearReconciliationPending(dequeued.Sandbox.ID)
	r.enqueuePolicyReconciliation(context.Background(), rejected, "periodic", 0)
	if len(r.reconcileCh) != 1 || recorder.countKind("policy.reconciliation.warning") != 1 {
		t.Fatalf("retry after capacity returned queue=%d warnings=%d", len(r.reconcileCh), recorder.countKind("policy.reconciliation.warning"))
	}
	if retried := <-r.reconcileCh; retried.Sandbox.ID != rejected.ID {
		t.Fatalf("retried sandbox=%q, want %q", retried.Sandbox.ID, rejected.ID)
	}
}

func TestCanceledPolicyReconciliationEnqueueClearsPendingWithoutGap(t *testing.T) {
	t.Parallel()
	config := createDefaultConfig().(*Config)
	config.PolicyReconciliation.Enabled = true
	recorder := &reconciliationRecorder{}
	next, err := consumer.NewLogs(recorder.consume)
	if err != nil {
		t.Fatal(err)
	}
	r := newWatchReceiver(config, receiver.Settings{}, next)
	r.reconcileCh = make(chan reconciliationRequest)
	r.reconcilePending = make(map[string]struct{})
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	sandbox := sandboxRef{ID: "canceled-sandbox", Name: "canceled"}

	r.enqueuePolicyReconciliation(ctx, sandbox, "periodic", 0)
	r.reconcileMu.Lock()
	_, pending := r.reconcilePending[sandbox.ID]
	r.reconcileMu.Unlock()
	if pending {
		t.Fatal("canceled enqueue left a permanently pending sandbox")
	}
	if len(recorder.snapshot()) != 0 {
		t.Fatal("normal cancellation was misrepresented as a source gap")
	}
}

func contains(value, fragment string) bool {
	for i := 0; i+len(fragment) <= len(value); i++ {
		if value[i:i+len(fragment)] == fragment {
			return true
		}
	}
	return false
}

var _ gatewayClient = (*reconciliationGateway)(nil)
var _ storage.Client = (*memoryPolicyStorage)(nil)
