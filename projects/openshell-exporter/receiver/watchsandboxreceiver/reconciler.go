// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package watchsandboxreceiver

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"sync"
	"time"

	"go.opentelemetry.io/collector/component"
	"go.opentelemetry.io/collector/extension/xextension/storage"
	"go.opentelemetry.io/collector/pdata/plog"
	"go.uber.org/zap"
	"google.golang.org/grpc/status"
)

type reconciliationRequest struct {
	Sandbox                  sandboxRef
	Trigger                  string
	NotificationDraftVersion uint64
}

type reconciliationAttemptKey struct{}

type reconciliationAttempt struct {
	failed bool
}

func (r *watchReceiver) startPolicyReconciliation(ctx context.Context, host component.Host) error {
	if !r.config.PolicyReconciliation.Enabled {
		r.metrics.policyDisabled(ctx)
		r.settings.Logger.Info("OpenShell source capability disabled", zapString("source", "policy_reconciliation"))
		return nil
	}
	storageID := r.config.PolicyReconciliation.StorageID
	extension, ok := host.GetExtensions()[*storageID]
	if !ok {
		return fmt.Errorf("policy reconciliation storage extension %q not found", storageID.String())
	}
	storageExtension, ok := extension.(storage.Extension)
	if !ok {
		return fmt.Errorf("policy reconciliation extension %q is not storage", storageID.String())
	}
	client, err := storageExtension.GetClient(ctx, component.KindReceiver, r.settings.ID, "policy-reconciliation")
	if err != nil {
		return fmt.Errorf("create policy reconciliation storage client: %w", err)
	}
	r.policyStorage = client
	r.reconcileCh = make(chan reconciliationRequest, r.config.PolicyReconciliation.QueueSize)
	r.metrics.policyEnabled(ctx)
	r.recordPolicyQueueState(ctx)
	r.reconcilePending = make(map[string]struct{})
	for range r.config.PolicyReconciliation.Workers {
		r.reconcileWG.Add(1)
		go r.policyReconciliationWorker(ctx)
	}
	r.reconcileWG.Add(1)
	go r.policyReconciliationTicker(ctx)
	r.settings.Logger.Info(
		"OpenShell source capability enabled",
		zapString("source", "policy_reconciliation"),
		zapString("durability", "checkpointed_api_snapshot"),
		zapBool("draft_snapshots", true),
		zap.Duration("interval", r.config.PolicyReconciliation.Interval),
		zap.Duration("timeout", r.config.PolicyReconciliation.Timeout),
		zap.Int("workers", r.config.PolicyReconciliation.Workers),
		zap.Int("queue_capacity", r.config.PolicyReconciliation.QueueSize),
		zap.Uint32("max_revisions", r.config.PolicyReconciliation.MaxRevisions),
		zapBool("draft_chunks", true),
		zapBool("draft_history", r.config.PolicyReconciliation.IncludeHistory),
		zapBool("policy_status", true),
		zapBool("policy_revisions", true),
		zapBool("effective_policy_bodies", r.config.PolicyReconciliation.IncludeEffectivePolicies),
		zapBool("review_tokens_exported", false),
	)
	return nil
}

// Tiny wrappers keep reconciler tests independent of zap field construction.
func zapString(key, value string) zap.Field    { return zap.String(key, value) }
func zapBool(key string, value bool) zap.Field { return zap.Bool(key, value) }

func (r *watchReceiver) policyReconciliationTicker(ctx context.Context) {
	defer r.reconcileWG.Done()
	ticker := time.NewTicker(r.config.PolicyReconciliation.Interval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			r.recordPolicyQueueState(ctx)
			r.mu.Lock()
			sandboxes := make([]sandboxRef, 0, len(r.sandboxes))
			for _, sandbox := range r.sandboxes {
				sandboxes = append(sandboxes, sandbox)
			}
			r.mu.Unlock()
			for _, sandbox := range sandboxes {
				r.enqueuePolicyReconciliation(ctx, sandbox, "periodic", 0)
			}
		}
	}
}

func (r *watchReceiver) enqueuePolicyReconciliation(ctx context.Context, sandbox sandboxRef, trigger string, notificationVersion uint64) {
	if !r.config.PolicyReconciliation.Enabled || r.reconcileCh == nil {
		return
	}
	r.reconcileMu.Lock()
	if _, exists := r.reconcilePending[sandbox.ID]; exists {
		r.reconcileMu.Unlock()
		return
	}
	r.reconcilePending[sandbox.ID] = struct{}{}
	r.reconcileMu.Unlock()
	request := reconciliationRequest{Sandbox: sandbox, Trigger: trigger, NotificationDraftVersion: notificationVersion}
	select {
	case r.reconcileCh <- request:
	case <-ctx.Done():
		r.clearReconciliationPending(sandbox.ID)
	default:
		r.clearReconciliationPending(sandbox.ID)
		r.metrics.reconciliationGap(ctx, "queue_full")
		r.emitReconciliationWarning(ctx, request, "openshell.exporter/policy-reconciliation", "queue_full", "policy reconciliation queue is full; the periodic read will retry")
	}
	r.recordPolicyQueueState(ctx)
}

func (r *watchReceiver) clearReconciliationPending(sandboxID string) {
	r.reconcileMu.Lock()
	delete(r.reconcilePending, sandboxID)
	r.reconcileMu.Unlock()
}

func (r *watchReceiver) recordPolicyQueueState(ctx context.Context) {
	if r.reconcileCh == nil {
		return
	}
	r.metrics.reconciliationState(ctx, len(r.reconcileCh), cap(r.reconcileCh))
}

func (r *watchReceiver) policyReconciliationWorker(ctx context.Context) {
	defer r.reconcileWG.Done()
	for {
		select {
		case <-ctx.Done():
			return
		case request := <-r.reconcileCh:
			r.recordPolicyQueueState(ctx)
			r.clearReconciliationPending(request.Sandbox.ID)
			lockValue, _ := r.reconcileLocks.LoadOrStore(request.Sandbox.ID, &sync.Mutex{})
			lock := lockValue.(*sync.Mutex)
			lock.Lock()
			r.reconcileSandbox(ctx, request)
			lock.Unlock()
		}
	}
}

func (r *watchReceiver) reconcileSandbox(parent context.Context, request reconciliationRequest) {
	ctx, cancel := context.WithTimeout(parent, r.config.PolicyReconciliation.Timeout)
	defer cancel()
	attempt := &reconciliationAttempt{}
	ctx = context.WithValue(ctx, reconciliationAttemptKey{}, attempt)
	state, err := r.loadPolicyState(ctx, request.Sandbox.ID)
	if err != nil {
		r.recordReconciliationFailure(ctx, "state_read")
		r.emitReconciliationWarning(ctx, request, "file_storage/Get", "state_read_failed", err.Error())
		return
	}
	changed := state.migrated
	if state.migrated {
		if !r.emitReconciliationWarning(ctx, request, "file_storage/Get", "state_migrated", "policy reconciliation checkpoint upgraded; mutable snapshots will replay once") {
			r.recordReconciliationFailure(ctx, "delivery_failed")
			return
		}
	}

	draft, draftErr := r.client.GetDraft(ctx, request.Sandbox.Name)
	metadata := reconciliationMetadata{Trigger: request.Trigger, NotificationDraftVersion: request.NotificationDraftVersion, Consistency: draftConsistency(request.NotificationDraftVersion, draft)}
	if metadata.Consistency == "gateway_ahead" || metadata.Consistency == "notification_ahead" {
		r.metrics.reconciliationConsistencyGap(ctx, metadata.Consistency)
	}
	if draftErr != nil {
		r.recordPolicyReadFailure(ctx, request, "openshell.v1.OpenShell/GetDraftPolicy", draftErr)
	} else {
		logs, convertErr := draftSnapshotLogs(r.config, request.Sandbox, r.gatewayVersion, draft, metadata)
		if convertErr != nil {
			r.recordPolicyConversionFailure(ctx, request, "openshell.v1.OpenShell/GetDraftPolicy", convertErr)
		} else if eventChanged(state.DraftHash, logs) {
			if r.deliverPolicyLogs(ctx, logs) {
				state.DraftHash = policyEventHash(logs)
				changed = true
			}
		}
		chunkEvents := make([]policySequenceEvent, 0, len(draft.GetChunks()))
		chunksComplete := true
		for _, chunk := range draft.GetChunks() {
			logs, convertErr := draftChunkLogs(r.config, request.Sandbox, r.gatewayVersion, draft.GetDraftVersion(), chunk, metadata)
			if convertErr != nil {
				r.recordPolicyConversionFailure(ctx, request, "openshell.v1.OpenShell/GetDraftPolicy", convertErr)
				chunksComplete = false
				continue
			}
			event, eventErr := newPolicySequenceEvent(logs, 0, chunk.GetId())
			if eventErr != nil {
				r.recordPolicyConversionFailure(ctx, request, "openshell.v1.OpenShell/GetDraftPolicy", eventErr)
				chunksComplete = false
				continue
			}
			chunkEvents = append(chunkEvents, event)
		}
		canonicalizePolicySequence(chunkEvents)
		if r.reconcilePolicySequence(ctx, request, &state.Chunks, chunkEvents, chunksComplete, false, "openshell.v1.OpenShell/GetDraftPolicy", "draft chunks") {
			changed = true
		}
	}

	policyStatus, statusErr := r.client.GetPolicyStatus(ctx, request.Sandbox.Name)
	if statusErr != nil {
		r.recordPolicyReadFailure(ctx, request, "openshell.v1.OpenShell/GetSandboxPolicyStatus", statusErr)
	} else {
		logs, convertErr := policyStatusLogs(r.config, request.Sandbox, r.gatewayVersion, policyStatus, metadata)
		if convertErr != nil {
			r.recordPolicyConversionFailure(ctx, request, "openshell.v1.OpenShell/GetSandboxPolicyStatus", convertErr)
		} else if eventChanged(state.StatusHash, logs) && r.deliverPolicyLogs(ctx, logs) {
			state.StatusHash = policyEventHash(logs)
			changed = true
		}
	}

	revisions, revisionsErr := r.client.ListPolicyRevisions(ctx, request.Sandbox.Name)
	revisionsComplete := revisionsErr == nil
	if revisionsErr != nil {
		r.recordPolicyReadFailure(ctx, request, "openshell.v1.OpenShell/ListSandboxPolicies", revisionsErr)
	}
	if len(revisions) > 0 || revisionsComplete {
		revisionEvents := make([]policySequenceEvent, 0, len(revisions))
		for _, revision := range revisions {
			logs, convertErr := policyRevisionLogs(r.config, request.Sandbox, r.gatewayVersion, revision, metadata)
			if convertErr != nil {
				r.recordPolicyConversionFailure(ctx, request, "openshell.v1.OpenShell/ListSandboxPolicies", convertErr)
				revisionsComplete = false
				continue
			}
			event, eventErr := newPolicySequenceEvent(logs, int64(revision.GetVersion()), revision.GetPolicyHash())
			if eventErr != nil {
				r.recordPolicyConversionFailure(ctx, request, "openshell.v1.OpenShell/ListSandboxPolicies", eventErr)
				revisionsComplete = false
				continue
			}
			revisionEvents = append(revisionEvents, event)
		}
		canonicalizePolicySequence(revisionEvents)
		if r.reconcilePolicySequence(ctx, request, &state.Revisions, revisionEvents, revisionsComplete, true, "openshell.v1.OpenShell/ListSandboxPolicies", "policy revisions") {
			changed = true
		}
	}

	if r.config.PolicyReconciliation.IncludeHistory {
		history, historyErr := r.client.GetDraftHistory(ctx, request.Sandbox.Name)
		if historyErr != nil {
			r.recordPolicyReadFailure(ctx, request, "openshell.v1.OpenShell/GetDraftHistory", historyErr)
		} else {
			historyEvents := make([]policySequenceEvent, 0, len(history.GetEntries()))
			historyComplete := true
			for _, entry := range history.GetEntries() {
				logs, convertErr := draftHistoryLogs(r.config, request.Sandbox, r.gatewayVersion, entry, metadata)
				if convertErr != nil {
					r.recordPolicyConversionFailure(ctx, request, "openshell.v1.OpenShell/GetDraftHistory", convertErr)
					historyComplete = false
					continue
				}
				event, eventErr := newPolicySequenceEvent(logs, entry.GetTimestampMs(), entry.GetChunkId()+"\x00"+entry.GetEventType())
				if eventErr != nil {
					r.recordPolicyConversionFailure(ctx, request, "openshell.v1.OpenShell/GetDraftHistory", eventErr)
					historyComplete = false
					continue
				}
				historyEvents = append(historyEvents, event)
			}
			canonicalizePolicySequence(historyEvents)
			if r.reconcilePolicySequence(ctx, request, &state.History, historyEvents, historyComplete, true, "openshell.v1.OpenShell/GetDraftHistory", "draft history") {
				changed = true
			}
		}
	}

	if changed {
		if err := r.savePolicyState(ctx, request.Sandbox.ID, state); err != nil {
			r.recordReconciliationFailure(ctx, "state_write")
			r.emitReconciliationWarning(ctx, request, "file_storage/Set", "state_write_failed", err.Error())
			return
		}
	}
	if !attempt.failed {
		r.recordReconciliationSuccess(ctx)
	}
}

func (r *watchReceiver) recordPolicyReadFailure(ctx context.Context, request reconciliationRequest, operation string, err error) {
	if errors.Is(err, context.Canceled) || (errors.Is(err, context.DeadlineExceeded) && ctx.Err() != nil) {
		if attempt, ok := ctx.Value(reconciliationAttemptKey{}).(*reconciliationAttempt); ok {
			attempt.failed = true
		}
		return
	}
	code := status.Code(err).String()
	if reason, incomplete := policyRevisionGapReason(err); incomplete {
		code = reason
		r.metrics.reconciliationGap(ctx, reason)
	}
	r.recordReconciliationFailure(ctx, code)
	r.emitReconciliationWarning(ctx, request, operation, code, err.Error())
}

func (r *watchReceiver) recordPolicyConversionFailure(ctx context.Context, request reconciliationRequest, operation string, err error) {
	r.recordReconciliationFailure(ctx, "conversion_failed")
	r.emitReconciliationWarning(ctx, request, operation, "conversion_failed", err.Error())
}

func (r *watchReceiver) recordReconciliationFailure(ctx context.Context, reason string) {
	reason = boundedPolicyFailureReason(reason)
	if attempt, ok := ctx.Value(reconciliationAttemptKey{}).(*reconciliationAttempt); ok {
		attempt.failed = true
	}
	r.metrics.reconciliationFailed(ctx, reason)
	if r.policyUnhealthy.CompareAndSwap(false, true) && r.settings.Logger != nil {
		r.settings.Logger.Warn("policy reconciliation degraded", zap.String("reason", reason))
	}
}

func (r *watchReceiver) recordReconciliationSuccess(ctx context.Context) {
	r.metrics.reconciliationSucceeded(ctx)
	if r.policyUnhealthy.Swap(false) && r.settings.Logger != nil {
		r.settings.Logger.Info("policy reconciliation recovered")
	}
}

func (r *watchReceiver) emitReconciliationWarning(ctx context.Context, request reconciliationRequest, operation, code, message string) bool {
	metadata := reconciliationMetadata{Trigger: request.Trigger, NotificationDraftVersion: request.NotificationDraftVersion, Consistency: "unavailable"}
	logs, err := reconciliationWarningLogs(r.config, request.Sandbox, r.gatewayVersion, operation, code, message, metadata)
	if err != nil {
		if r.settings.Logger != nil {
			r.settings.Logger.Error("construct policy reconciliation warning", zap.Error(err))
		}
		return false
	}
	if err := r.consumeWithBackpressure(ctx, logs); err != nil && !errors.Is(err, context.Canceled) {
		if r.settings.Logger != nil {
			r.settings.Logger.Warn("policy reconciliation warning delivery failed", zap.Error(err))
		}
		return false
	}
	return ctx.Err() == nil
}

func (r *watchReceiver) reconcilePolicySequence(
	ctx context.Context,
	request reconciliationRequest,
	checkpoint *policySequenceCheckpoint,
	events []policySequenceEvent,
	complete bool,
	appendOnly bool,
	operation string,
	lane string,
) bool {
	if !complete {
		for _, event := range events {
			if !r.deliverPolicyLogs(ctx, event.logs) {
				return false
			}
		}
		return false
	}
	start := 0
	var next policySequenceCheckpoint
	reset := false
	if appendOnly {
		start, next, reset = policySequencePlan(*checkpoint, events)
	} else {
		start, next = policySnapshotPlan(*checkpoint, events)
	}
	if reset {
		r.metrics.reconciliationGap(ctx, "snapshot_reset")
		message := lane + " no longer matches the persisted prefix; replaying the complete current snapshot"
		if !r.emitReconciliationWarning(ctx, request, operation, "snapshot_reset", message) {
			r.recordReconciliationFailure(ctx, "delivery_failed")
			return false
		}
	}
	for _, event := range events[start:] {
		if !r.deliverPolicyLogs(ctx, event.logs) {
			return false
		}
	}
	if *checkpoint == next {
		return false
	}
	*checkpoint = next
	return true
}

func (r *watchReceiver) deliverPolicyLogs(ctx context.Context, logs plog.Logs) bool {
	if err := r.consumeWithBackpressure(ctx, logs); err != nil {
		if !errors.Is(err, context.Canceled) {
			r.recordReconciliationFailure(ctx, "delivery_failed")
			if r.settings.Logger != nil {
				r.settings.Logger.Warn("policy evidence delivery failed", zap.Error(err))
			}
		}
		return false
	}
	return true
}

func (r *watchReceiver) loadPolicyState(ctx context.Context, sandboxID string) (*policyReconciliationState, error) {
	encoded, err := r.policyStorage.Get(ctx, policyStateKey(sandboxID))
	if err != nil {
		return nil, err
	}
	return decodePolicyState(encoded)
}

func (r *watchReceiver) savePolicyState(ctx context.Context, sandboxID string, state *policyReconciliationState) error {
	encoded, err := encodePolicyState(state)
	if err != nil {
		return err
	}
	return r.policyStorage.Set(ctx, policyStateKey(sandboxID), encoded)
}

func policyStateKey(sandboxID string) string {
	sum := sha256.Sum256([]byte(sandboxID))
	return "sandbox/" + hex.EncodeToString(sum[:])
}

func policyEventHash(logs plog.Logs) string {
	if logs.LogRecordCount() == 0 {
		return ""
	}
	record := logs.ResourceLogs().At(0).ScopeLogs().At(0).LogRecords().At(0)
	encoded, _ := json.Marshal(map[string]any{
		"kind": attributeRaw(record, "openshell.acquisition.kind"),
		"body": record.Body().AsRaw(),
	})
	sum := sha256.Sum256(encoded)
	return hex.EncodeToString(sum[:])
}

func attributeRaw(record plog.LogRecord, key string) any {
	value, ok := record.Attributes().Get(key)
	if !ok {
		return nil
	}
	return value.AsRaw()
}

func eventChanged(previous string, logs plog.Logs) bool {
	return previous != policyEventHash(logs)
}

func draftConsistency(notification uint64, draft interface{ GetDraftVersion() uint64 }) string {
	if draft == nil {
		return "unavailable"
	}
	if notification == 0 {
		return "not_compared"
	}
	retrieved := draft.GetDraftVersion()
	switch {
	case retrieved == notification:
		return "matched"
	case retrieved > notification:
		return "gateway_ahead"
	default:
		return "notification_ahead"
	}
}
