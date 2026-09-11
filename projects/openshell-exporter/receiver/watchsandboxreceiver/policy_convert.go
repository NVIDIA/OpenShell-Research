// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package watchsandboxreceiver

import (
	"errors"
	"fmt"
	"time"

	pb "github.com/NVIDIA/OpenShell/sdk/go/proto/openshellv1"
	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/plog"
	"google.golang.org/protobuf/proto"
)

type reconciliationMetadata struct {
	Trigger                  string
	NotificationDraftVersion uint64
	Consistency              string
}

func draftSnapshotLogs(
	config *Config,
	sandbox sandboxRef,
	gatewayVersion string,
	response *pb.GetDraftPolicyResponse,
	metadata reconciliationMetadata,
) (plog.Logs, error) {
	if response == nil {
		return plog.Logs{}, errors.New("nil GetDraftPolicy response")
	}
	sanitized, removed := sanitizeDraft(response, config.PolicyReconciliation.IncludeEffectivePolicies)
	body := map[string]any{
		"sandbox_id":                  sandbox.ID,
		"draft_version":               sanitized.GetDraftVersion(),
		"rolling_summary":             sanitized.GetRollingSummary(),
		"last_analyzed_at_ms":         sanitized.GetLastAnalyzedAtMs(),
		"chunk_count":                 len(sanitized.GetChunks()),
		"review_tokens_removed":       removed,
		"effective_policies_included": config.PolicyReconciliation.IncludeEffectivePolicies,
	}
	body, payloadType, err := withPublicPayload(body, sanitized)
	if err != nil {
		return plog.Logs{}, err
	}
	logs, record, err := policyReadLogs(config, sandbox, gatewayVersion, "policy.draft.snapshot", "openshell.v1.OpenShell/GetDraftPolicy", payloadType, metadata, body)
	if err != nil {
		return plog.Logs{}, err
	}
	record.Attributes().PutInt("openshell.policy.revision", int64(sanitized.GetDraftVersion()))
	if sanitized.GetLastAnalyzedAtMs() > 0 {
		record.Attributes().PutInt("openshell.event.time_unix_ms", sanitized.GetLastAnalyzedAtMs())
	}
	return logs, nil
}

func draftChunkLogs(
	config *Config,
	sandbox sandboxRef,
	gatewayVersion string,
	draftVersion uint64,
	chunk *pb.PolicyChunk,
	metadata reconciliationMetadata,
) (plog.Logs, error) {
	if chunk == nil {
		return plog.Logs{}, errors.New("nil policy chunk")
	}
	sanitized, removed := sanitizePolicyChunk(chunk, config.PolicyReconciliation.IncludeEffectivePolicies)
	body := map[string]any{
		"sandbox_id":                  sandbox.ID,
		"draft_version":               draftVersion,
		"policy_chunk_id":             sanitized.GetId(),
		"status":                      sanitized.GetStatus(),
		"stage":                       sanitized.GetStage(),
		"review_token_present":        removed > 0,
		"effective_policies_included": config.PolicyReconciliation.IncludeEffectivePolicies,
	}
	body, payloadType, err := withPublicPayload(body, sanitized)
	if err != nil {
		return plog.Logs{}, err
	}
	logs, record, err := policyReadLogs(config, sandbox, gatewayVersion, "policy.draft.chunk", "openshell.v1.OpenShell/GetDraftPolicy", payloadType, metadata, body)
	if err != nil {
		return plog.Logs{}, err
	}
	record.Attributes().PutInt("openshell.policy.revision", int64(draftVersion))
	if sanitized.GetId() != "" {
		record.Attributes().PutStr("openshell.policy.chunk.id", sanitized.GetId())
	}
	if sanitized.GetCreatedAtMs() > 0 {
		record.Attributes().PutInt("openshell.event.time_unix_ms", sanitized.GetCreatedAtMs())
	}
	return logs, nil
}

func policyStatusLogs(
	config *Config,
	sandbox sandboxRef,
	gatewayVersion string,
	response *pb.GetSandboxPolicyStatusResponse,
	metadata reconciliationMetadata,
) (plog.Logs, error) {
	if response == nil {
		return plog.Logs{}, errors.New("nil GetSandboxPolicyStatus response")
	}
	sanitized := proto.Clone(response).(*pb.GetSandboxPolicyStatusResponse)
	if !config.PolicyReconciliation.IncludeEffectivePolicies && sanitized.GetRevision() != nil {
		sanitized.Revision.Policy = nil
	}
	body := map[string]any{
		"sandbox_id":     sandbox.ID,
		"active_version": sanitized.GetActiveVersion(),
	}
	body, payloadType, err := withPublicPayload(body, sanitized)
	if err != nil {
		return plog.Logs{}, err
	}
	logs, record, err := policyReadLogs(config, sandbox, gatewayVersion, "policy.status", "openshell.v1.OpenShell/GetSandboxPolicyStatus", payloadType, metadata, body)
	if err != nil {
		return plog.Logs{}, err
	}
	if sanitized.GetActiveVersion() > 0 {
		record.Attributes().PutInt("openshell.policy.version", int64(sanitized.GetActiveVersion()))
	}
	if revision := sanitized.GetRevision(); revision != nil {
		record.Attributes().PutInt("openshell.policy.revision", int64(revision.GetVersion()))
		if revision.GetCreatedAtMs() > 0 {
			record.Attributes().PutInt("openshell.event.time_unix_ms", revision.GetCreatedAtMs())
		}
	}
	return logs, nil
}

func policyRevisionLogs(
	config *Config,
	sandbox sandboxRef,
	gatewayVersion string,
	revision *pb.SandboxPolicyRevision,
	metadata reconciliationMetadata,
) (plog.Logs, error) {
	if revision == nil {
		return plog.Logs{}, errors.New("nil policy revision")
	}
	sanitized := proto.Clone(revision).(*pb.SandboxPolicyRevision)
	if !config.PolicyReconciliation.IncludeEffectivePolicies {
		sanitized.Policy = nil
	}
	body := map[string]any{
		"sandbox_id":  sandbox.ID,
		"version":     sanitized.GetVersion(),
		"policy_hash": sanitized.GetPolicyHash(),
		"status":      sanitized.GetStatus().String(),
	}
	body, payloadType, err := withPublicPayload(body, sanitized)
	if err != nil {
		return plog.Logs{}, err
	}
	logs, record, err := policyReadLogs(config, sandbox, gatewayVersion, "policy.revision", "openshell.v1.OpenShell/ListSandboxPolicies", payloadType, metadata, body)
	if err != nil {
		return plog.Logs{}, err
	}
	record.Attributes().PutInt("openshell.policy.version", int64(sanitized.GetVersion()))
	record.Attributes().PutInt("openshell.policy.revision", int64(sanitized.GetVersion()))
	if sanitized.GetCreatedAtMs() > 0 {
		record.Attributes().PutInt("openshell.event.time_unix_ms", sanitized.GetCreatedAtMs())
	}
	return logs, nil
}

func draftHistoryLogs(
	config *Config,
	sandbox sandboxRef,
	gatewayVersion string,
	entry *pb.DraftHistoryEntry,
	metadata reconciliationMetadata,
) (plog.Logs, error) {
	if entry == nil {
		return plog.Logs{}, errors.New("nil draft history entry")
	}
	body := map[string]any{
		"sandbox_id":      sandbox.ID,
		"event_type":      entry.GetEventType(),
		"policy_chunk_id": entry.GetChunkId(),
		"timestamp_ms":    entry.GetTimestampMs(),
	}
	body, payloadType, err := withPublicPayload(body, entry)
	if err != nil {
		return plog.Logs{}, err
	}
	logs, record, err := policyReadLogs(config, sandbox, gatewayVersion, "policy.draft.history", "openshell.v1.OpenShell/GetDraftHistory", payloadType, metadata, body)
	if err != nil {
		return plog.Logs{}, err
	}
	if entry.GetChunkId() != "" {
		record.Attributes().PutStr("openshell.policy.chunk.id", entry.GetChunkId())
	}
	if entry.GetTimestampMs() > 0 {
		record.Attributes().PutInt("openshell.event.time_unix_ms", entry.GetTimestampMs())
	}
	return logs, nil
}

func reconciliationWarningLogs(
	config *Config,
	sandbox sandboxRef,
	gatewayVersion string,
	operation string,
	code string,
	message string,
	metadata reconciliationMetadata,
) (plog.Logs, error) {
	body := map[string]any{
		"sandbox_id":    sandbox.ID,
		"code":          code,
		"message":       message,
		"api_operation": operation,
	}
	returnLogs, _, err := policyReadLogs(config, sandbox, gatewayVersion, "policy.reconciliation.warning", operation, "openshell.exporter.policy.Diagnostic", metadata, body)
	return returnLogs, err
}

func policyReadLogs(
	config *Config,
	sandbox sandboxRef,
	gatewayVersion string,
	kind string,
	operation string,
	payloadType string,
	metadata reconciliationMetadata,
	body any,
) (plog.Logs, plog.LogRecord, error) {
	logs := plog.NewLogs()
	resourceLogs := logs.ResourceLogs().AppendEmpty()
	resourceLogs.Resource().Attributes().PutStr("service.name", "openshell")
	resourceLogs.Resource().Attributes().PutStr("openshell.gateway.id", config.GatewayID)
	scopeLogs := resourceLogs.ScopeLogs().AppendEmpty()
	scopeLogs.Scope().SetName("openshell.policy.reconciliation")
	record := scopeLogs.LogRecords().AppendEmpty()
	record.SetObservedTimestamp(pcommon.NewTimestampFromTime(time.Now().UTC()))
	attributes := record.Attributes()
	attributes.PutStr("openshell.acquisition.source_instance", "policy-reconciliation")
	attributes.PutStr("openshell.acquisition.kind", kind)
	attributes.PutStr("openshell.acquisition.transport", "grpc")
	attributes.PutStr("openshell.acquisition.api_operation", operation)
	attributes.PutStr("openshell.acquisition.payload_type", payloadType)
	attributes.PutStr("openshell.acquisition.reconciliation_trigger", metadata.Trigger)
	attributes.PutStr("openshell.acquisition.consistency", metadata.Consistency)
	if metadata.NotificationDraftVersion > 0 {
		attributes.PutInt("openshell.acquisition.notification_draft_version", int64(metadata.NotificationDraftVersion))
	}
	attributes.PutStr("openshell.event.kind", kind)
	attributes.PutStr("openshell.workspace", config.Workspace)
	attributes.PutStr("openshell.sandbox.id", sandbox.ID)
	attributes.PutStr("openshell.sandbox.name", sandbox.Name)
	if gatewayVersion != "" {
		attributes.PutStr("openshell.gateway.version", gatewayVersion)
	}
	if err := record.Body().FromRaw(body); err != nil {
		return plog.Logs{}, plog.LogRecord{}, fmt.Errorf("construct %s event: %w", kind, err)
	}
	return logs, record, nil
}

func sanitizeDraft(response *pb.GetDraftPolicyResponse, includeEffectivePolicies bool) (*pb.GetDraftPolicyResponse, int) {
	sanitized := proto.Clone(response).(*pb.GetDraftPolicyResponse)
	removed := 0
	for index, chunk := range sanitized.GetChunks() {
		clean, count := sanitizePolicyChunk(chunk, includeEffectivePolicies)
		sanitized.Chunks[index] = clean
		removed += count
	}
	return sanitized, removed
}

func sanitizePolicyChunk(chunk *pb.PolicyChunk, includeEffectivePolicies bool) (*pb.PolicyChunk, int) {
	sanitized := proto.Clone(chunk).(*pb.PolicyChunk)
	removed := 0
	if sanitized.GetReviewToken() != "" {
		removed = 1
		sanitized.ReviewToken = ""
	}
	if !includeEffectivePolicies {
		sanitized.CurrentEffectivePolicy = nil
		sanitized.CandidateEffectivePolicy = nil
	}
	return sanitized, removed
}
