// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package conformance

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"
	"time"
)

const (
	correlatedTimelineBackendFile      = "external-correlated-timeline.json"
	correlatedTimelineVerificationFile = "external-correlated-timeline-verification.json"
	correlationIdentityContract        = "urn:openshell:correlation-context:1"
)

type canonicalCorrelationContext struct {
	GatewayID     string `json:"openshell.gateway.id"`
	Workspace     string `json:"openshell.workspace"`
	SandboxID     string `json:"openshell.sandbox.id"`
	AgentSession  string `json:"agent.session.id"`
	TraceID       string `json:"trace_id"`
	RequestID     string `json:"request_id"`
	ToolCallID    string `json:"tool_call_id"`
	PolicyVersion string `json:"openshell.policy.version"`
}

type realCloudEventIdentity struct {
	Source      string                      `json:"source"`
	ID          string                      `json:"id"`
	Type        string                      `json:"type"`
	Correlation canonicalCorrelationContext `json:"correlation"`
}

type realRelaySpanIdentity struct {
	TraceID       string                      `json:"trace_id"`
	SpanID        string                      `json:"span_id"`
	Stage         string                      `json:"stage"`
	RequestID     string                      `json:"request_id"`
	ToolCallID    string                      `json:"tool_call_id"`
	PolicyVersion string                      `json:"policy_version"`
	Correlation   canonicalCorrelationContext `json:"correlation"`
}

type realCorrelationReport struct {
	SchemaVersion          string `json:"schema_version"`
	Result                 string `json:"result"`
	CandidateBound         bool   `json:"candidate_bound"`
	ExternalRoutingEnabled bool   `json:"external_routing_enabled"`
	VerifiedAt             string `json:"verified_at"`
	Candidate              struct {
		RepositoryCommit string `json:"repository_commit"`
		WorktreeClean    bool   `json:"worktree_clean"`
	} `json:"candidate"`
	Task struct {
		StartedAt          string `json:"started_at"`
		FinishedAt         string `json:"finished_at"`
		GatewayID          string `json:"gateway_id"`
		Workspace          string `json:"workspace"`
		OpenShellSandboxID string `json:"openshell_sandbox_id"`
		AgentSessionID     string `json:"agent_session_id"`
		CreatedFile        struct {
			Path   string `json:"path"`
			SHA256 string `json:"sha256"`
		} `json:"created_file"`
		RelayTraceIDs []string                 `json:"relay_trace_ids"`
		CloudEvents   []realCloudEventIdentity `json:"cloud_events"`
		RelaySpans    []realRelaySpanIdentity  `json:"relay_spans"`
	} `json:"task"`
	Counts struct {
		UniqueCloudEvents int `json:"unique_cloud_events"`
		UniqueRelaySpans  int `json:"unique_relay_spans"`
	} `json:"counts"`
}

type externalTimelineQuery struct {
	GatewayID      string   `json:"gateway_id"`
	Workspace      string   `json:"workspace"`
	SandboxID      string   `json:"sandbox_id"`
	AgentSessionID string   `json:"agent_session_id"`
	TraceIDs       []string `json:"trace_ids"`
}

type externalCloudEventProjection struct {
	Source      string                      `json:"source"`
	ID          string                      `json:"id"`
	Type        string                      `json:"type"`
	GatewayID   string                      `json:"gateway_id"`
	Workspace   string                      `json:"workspace"`
	SandboxID   string                      `json:"sandbox_id"`
	Correlation canonicalCorrelationContext `json:"correlation"`
}

type externalRelaySpanProjection struct {
	TraceID        string                      `json:"trace_id"`
	SpanID         string                      `json:"span_id"`
	Stage          string                      `json:"stage"`
	GatewayID      string                      `json:"gateway_id"`
	Workspace      string                      `json:"workspace"`
	SandboxID      string                      `json:"sandbox_id"`
	AgentSessionID string                      `json:"agent_session_id"`
	RequestID      string                      `json:"request_id"`
	ToolCallID     string                      `json:"tool_call_id"`
	PolicyVersion  string                      `json:"policy_version"`
	Correlation    canonicalCorrelationContext `json:"correlation"`
}

type externalCorrelatedTimelineEvidence struct {
	SchemaVersion             string                         `json:"schema_version"`
	Result                    string                         `json:"result"`
	Receiver                  string                         `json:"receiver"`
	QueriedAt                 string                         `json:"queried_at"`
	CorrelationEvidenceSHA256 string                         `json:"correlation_evidence_sha256"`
	IdentityContract          string                         `json:"identity_contract"`
	Query                     externalTimelineQuery          `json:"query"`
	CloudEvents               []externalCloudEventProjection `json:"cloud_events"`
	RelaySpans                []externalRelaySpanProjection  `json:"relay_spans"`
}

type externalCorrelatedTimelineVerification struct {
	SchemaVersion             string   `json:"schema_version"`
	Result                    string   `json:"result"`
	VerifiedAt                string   `json:"verified_at"`
	Receiver                  string   `json:"receiver"`
	CandidateCommit           string   `json:"candidate_commit"`
	GatewayID                 string   `json:"gateway_id"`
	Workspace                 string   `json:"workspace"`
	SandboxID                 string   `json:"sandbox_id"`
	AgentSessionID            string   `json:"agent_session_id"`
	TraceIDs                  []string `json:"trace_ids"`
	CloudEvents               int      `json:"cloud_events"`
	RelaySpans                int      `json:"relay_spans"`
	IdentityContract          string   `json:"identity_contract"`
	CorrelationEvidenceSHA256 string   `json:"correlation_evidence_sha256"`
	BackendEvidenceSHA256     string   `json:"backend_evidence_sha256"`
	Limitations               []string `json:"limitations"`
}

func TestVerifyExternalCorrelatedTimeline(t *testing.T) {
	if os.Getenv("CONFORMANCE_VERIFY_CORRELATED_TIMELINE") != "true" {
		t.Skip("set CONFORMANCE_VERIFY_CORRELATED_TIMELINE=true to verify a real external correlated timeline")
	}
	localPath := requiredVerificationEnv(t, "CONFORMANCE_REAL_CORRELATION_EVIDENCE")
	backendPath := requiredVerificationEnv(t, "CONFORMANCE_CORRELATED_BACKEND_EVIDENCE")
	reportDirectory := requiredVerificationEnv(t, "CONFORMANCE_REPORT_DIR")
	boundCommit := requiredCandidateCommit(t)

	verification, err := verifyExternalCorrelatedTimeline(localPath, backendPath)
	if err != nil {
		t.Fatal(err)
	}
	if verification.CandidateCommit != boundCommit {
		t.Fatalf("real timeline belongs to candidate %s, conformance binary is %s", verification.CandidateCommit, boundCommit)
	}
	path := filepath.Join(reportDirectory, correlatedTimelineVerificationFile)
	if err := writeRestrictedEvidence(path, verification); err != nil {
		t.Fatal(err)
	}
	t.Logf("verified real external correlated timeline: %s", path)
}

func verifyExternalCorrelatedTimeline(
	localPath string,
	backendPath string,
) (externalCorrelatedTimelineVerification, error) {
	var local realCorrelationReport
	localEncoded, err := readJSONEvidence(localPath, &local)
	if err != nil {
		return externalCorrelatedTimelineVerification{}, fmt.Errorf("read real correlation evidence: %w", err)
	}
	if err := validateRealCorrelationReport(local); err != nil {
		return externalCorrelatedTimelineVerification{}, err
	}

	var backend externalCorrelatedTimelineEvidence
	backendEncoded, err := readStrictTimelineEvidence(backendPath, &backend)
	if err != nil {
		return externalCorrelatedTimelineVerification{}, fmt.Errorf("read external correlated timeline evidence: %w", err)
	}
	if err := validateExternalCorrelatedTimeline(local, localEncoded, backend); err != nil {
		return externalCorrelatedTimelineVerification{}, err
	}

	traceIDs := append([]string(nil), local.Task.RelayTraceIDs...)
	sort.Strings(traceIDs)
	return externalCorrelatedTimelineVerification{
		SchemaVersion:             backendEvidenceSchemaVersion,
		Result:                    "passed_external_correlated_timeline",
		VerifiedAt:                time.Now().UTC().Format(time.RFC3339Nano),
		Receiver:                  backend.Receiver,
		CandidateCommit:           local.Candidate.RepositoryCommit,
		GatewayID:                 local.Task.GatewayID,
		Workspace:                 local.Task.Workspace,
		SandboxID:                 local.Task.OpenShellSandboxID,
		AgentSessionID:            local.Task.AgentSessionID,
		TraceIDs:                  traceIDs,
		CloudEvents:               len(backend.CloudEvents),
		RelaySpans:                len(backend.RelaySpans),
		IdentityContract:          correlationIdentityContract,
		CorrelationEvidenceSHA256: evidenceDigest(localEncoded),
		BackendEvidenceSHA256:     evidenceDigest(backendEncoded),
		Limitations: []string{
			"This verifies an identity-only query projection exported from an independently operated backend.",
			"It does not prove backend API authenticity, mTLS or certificate rotation, outage recovery, restart, capacity, retention, signing, canary, or pilot gates.",
		},
	}, nil
}

func validateRealCorrelationReport(report realCorrelationReport) error {
	if report.SchemaVersion != backendEvidenceSchemaVersion ||
		report.Result != "passed_real_correlation" {
		return fmt.Errorf("real correlation evidence has an unsupported schema or result")
	}
	if !report.CandidateBound || !report.Candidate.WorktreeClean {
		return fmt.Errorf("real correlation evidence is not bound to a clean candidate")
	}
	if !report.ExternalRoutingEnabled {
		return fmt.Errorf("real correlation evidence did not enable external destination routing")
	}
	if err := validateHexIdentifier(report.Candidate.RepositoryCommit, 40, "candidate repository_commit"); err != nil {
		return err
	}
	if err := validateEvidenceTimestamp(report.VerifiedAt, "real correlation verified_at"); err != nil {
		return err
	}
	startedAt, err := time.Parse(time.RFC3339Nano, report.Task.StartedAt)
	if err != nil {
		return fmt.Errorf("task started_at must be RFC3339: %w", err)
	}
	finishedAt, err := time.Parse(time.RFC3339Nano, report.Task.FinishedAt)
	if err != nil || !finishedAt.After(startedAt) {
		return fmt.Errorf("task finished_at must be RFC3339 and after started_at")
	}
	for field, value := range map[string]string{
		"gateway_id":       report.Task.GatewayID,
		"workspace":        report.Task.Workspace,
		"sandbox_id":       report.Task.OpenShellSandboxID,
		"agent_session_id": report.Task.AgentSessionID,
	} {
		if err := validateSafeEvidenceLabel(value, field); err != nil {
			return err
		}
	}
	if report.Task.CreatedFile.Path != "/sandbox/showcase-agent-evidence.txt" {
		return fmt.Errorf("real correlation evidence does not prove the expected created file")
	}
	if err := validateHexIdentifier(report.Task.CreatedFile.SHA256, 64, "created file sha256"); err != nil {
		return err
	}
	if len(report.Task.CloudEvents) == 0 ||
		len(report.Task.CloudEvents) != report.Counts.UniqueCloudEvents {
		return fmt.Errorf("real correlation evidence has inconsistent unique CloudEvent identities")
	}
	if len(report.Task.RelaySpans) == 0 ||
		len(report.Task.RelaySpans) != report.Counts.UniqueRelaySpans {
		return fmt.Errorf("real correlation evidence has inconsistent unique Relay span identities")
	}

	expectedSourcePrefix := fmt.Sprintf(
		"openshell://%s/workspaces/%s/sandboxes/%s/sources/",
		report.Task.GatewayID,
		report.Task.Workspace,
		report.Task.OpenShellSandboxID,
	)
	cloudKeys := make(map[string]struct{}, len(report.Task.CloudEvents))
	watchSandbox, process, network := false, false, false
	for _, event := range report.Task.CloudEvents {
		if !strings.HasPrefix(event.Source, expectedSourcePrefix) {
			return fmt.Errorf("CloudEvent source is outside the real task identity")
		}
		if err := validateCloudEventID(event.ID); err != nil {
			return err
		}
		if !strings.HasPrefix(event.Type, "com.nvidia.openshell.") {
			return fmt.Errorf("CloudEvent type is outside the OpenShell contract")
		}
		if err := validateCanonicalCorrelationCore(
			event.Correlation,
			report.Task.GatewayID,
			report.Task.Workspace,
			report.Task.OpenShellSandboxID,
		); err != nil {
			return fmt.Errorf("real CloudEvent canonical correlation: %w", err)
		}
		if err := validateOptionalCanonicalCorrelation(event.Correlation, report.Task.AgentSessionID); err != nil {
			return fmt.Errorf("real CloudEvent canonical correlation: %w", err)
		}
		key := cloudEventProjectionKey(event.Source, event.ID)
		if _, duplicate := cloudKeys[key]; duplicate {
			return fmt.Errorf("real correlation evidence contains duplicate CloudEvent identity")
		}
		cloudKeys[key] = struct{}{}
		switch event.Type {
		case "com.nvidia.openshell.ocsf.1007.v1":
			process = true
		case "com.nvidia.openshell.ocsf.4001.v1":
			network = true
		default:
			if !strings.HasPrefix(event.Type, "com.nvidia.openshell.ocsf.") {
				watchSandbox = true
			}
		}
	}
	if !watchSandbox || !process || !network {
		return fmt.Errorf("real correlation evidence must include WatchSandbox, OCSF process, and OCSF network identities")
	}

	spanKeys := make(map[string]struct{}, len(report.Task.RelaySpans))
	traceIDs := make(map[string]struct{})
	stages := make(map[string]bool)
	policyVersions := make(map[string]struct{})
	policyVersionSpans := 0
	for _, span := range report.Task.RelaySpans {
		if err := validateHexIdentifier(span.TraceID, 32, "Relay trace_id"); err != nil {
			return err
		}
		if err := validateHexIdentifier(span.SpanID, 16, "Relay span_id"); err != nil {
			return err
		}
		for field, value := range map[string]string{
			"Relay request_id":     span.RequestID,
			"Relay tool_call_id":   span.ToolCallID,
			"Relay policy_version": span.PolicyVersion,
		} {
			if value != "" {
				if err := validateSafeEvidenceLabel(value, field); err != nil {
					return err
				}
			}
		}
		if err := validateCanonicalCorrelationCore(
			span.Correlation,
			report.Task.GatewayID,
			report.Task.Workspace,
			report.Task.OpenShellSandboxID,
		); err != nil {
			return fmt.Errorf("real Relay canonical correlation: %w", err)
		}
		if span.Correlation.AgentSession != report.Task.AgentSessionID ||
			span.Correlation.TraceID != span.TraceID ||
			span.Correlation.RequestID != span.RequestID ||
			span.Correlation.ToolCallID != span.ToolCallID ||
			span.Correlation.PolicyVersion != span.PolicyVersion {
			return fmt.Errorf("real Relay span does not preserve its canonical identity context")
		}
		if err := validateOptionalCanonicalCorrelation(span.Correlation, report.Task.AgentSessionID); err != nil {
			return fmt.Errorf("real Relay canonical correlation: %w", err)
		}

		switch span.Stage {
		case "prompt", "model", "tool", "agent":
		default:
			return fmt.Errorf("unsupported Relay stage %q", span.Stage)
		}
		if span.PolicyVersion != "" {
			policyVersions[span.PolicyVersion] = struct{}{}
			policyVersionSpans++
		}
		key := relaySpanProjectionKey(span.TraceID, span.SpanID)
		if _, duplicate := spanKeys[key]; duplicate {
			return fmt.Errorf("real correlation evidence contains duplicate Relay span identity")
		}
		spanKeys[key] = struct{}{}
		traceIDs[span.TraceID] = struct{}{}
		stages[span.Stage] = true
	}
	if len(policyVersions) > 0 && (len(policyVersions) != 1 || policyVersionSpans != len(report.Task.RelaySpans)) {
		return fmt.Errorf("gateway-reported Relay policy version must be consistent on every span")
	}
	if !stages["prompt"] || !stages["model"] || !stages["tool"] {
		return fmt.Errorf("real correlation evidence must include prompt, model, and tool stages")
	}
	if !sameStringSet(report.Task.RelayTraceIDs, mapKeys(traceIDs)) {
		return fmt.Errorf("real correlation trace set does not match Relay span identities")
	}
	return nil
}

func validateExternalCorrelatedTimeline(
	local realCorrelationReport,
	localEncoded []byte,
	backend externalCorrelatedTimelineEvidence,
) error {
	if backend.SchemaVersion != backendEvidenceSchemaVersion ||
		backend.Result != "queried_correlated_timeline" {
		return fmt.Errorf("external timeline evidence has an unsupported schema or result")
	}
	if backend.IdentityContract != correlationIdentityContract {
		return fmt.Errorf("external timeline evidence does not declare the canonical identity contract")
	}
	if err := validateSafeEvidenceLabel(backend.Receiver, "external receiver"); err != nil {
		return err
	}
	if err := validateEvidenceTimestamp(backend.QueriedAt, "external timeline queried_at"); err != nil {
		return err
	}
	finishedAt, _ := time.Parse(time.RFC3339Nano, local.Task.FinishedAt)
	queriedAt, _ := time.Parse(time.RFC3339Nano, backend.QueriedAt)
	if queriedAt.Before(finishedAt) {
		return fmt.Errorf("external backend query predates the real task completion")
	}
	if backend.CorrelationEvidenceSHA256 != evidenceDigest(localEncoded) {
		return fmt.Errorf("external timeline evidence is not bound to the real correlation report")
	}
	if backend.Query.GatewayID != local.Task.GatewayID ||
		backend.Query.Workspace != local.Task.Workspace ||
		backend.Query.SandboxID != local.Task.OpenShellSandboxID ||
		backend.Query.AgentSessionID != local.Task.AgentSessionID ||
		!sameStringSet(backend.Query.TraceIDs, local.Task.RelayTraceIDs) {
		return fmt.Errorf("external backend query identity does not match the real task")
	}

	localEvents := make(map[string]realCloudEventIdentity, len(local.Task.CloudEvents))
	for _, event := range local.Task.CloudEvents {
		localEvents[cloudEventProjectionKey(event.Source, event.ID)] = event
	}
	if len(backend.CloudEvents) != len(localEvents) {
		return fmt.Errorf("external backend returned %d unique CloudEvents, want %d", len(backend.CloudEvents), len(localEvents))
	}
	seenEvents := make(map[string]struct{}, len(backend.CloudEvents))
	for _, event := range backend.CloudEvents {
		key := cloudEventProjectionKey(event.Source, event.ID)
		expected, ok := localEvents[key]
		if !ok || event.Type != expected.Type {
			return fmt.Errorf("external backend CloudEvent identity does not match the real task")
		}
		if _, duplicate := seenEvents[key]; duplicate {
			return fmt.Errorf("external backend projection contains duplicate CloudEvent identity")
		}
		seenEvents[key] = struct{}{}
		if event.GatewayID != local.Task.GatewayID ||
			event.Workspace != local.Task.Workspace ||
			event.SandboxID != local.Task.OpenShellSandboxID ||
			event.Correlation != expected.Correlation {
			return fmt.Errorf("external backend CloudEvent correlation does not match the real task")
		}
	}

	localSpans := make(map[string]realRelaySpanIdentity, len(local.Task.RelaySpans))
	for _, span := range local.Task.RelaySpans {
		localSpans[relaySpanProjectionKey(span.TraceID, span.SpanID)] = span
	}
	if len(backend.RelaySpans) != len(localSpans) {
		return fmt.Errorf("external backend returned %d unique Relay spans, want %d", len(backend.RelaySpans), len(localSpans))
	}
	seenSpans := make(map[string]struct{}, len(backend.RelaySpans))
	for _, span := range backend.RelaySpans {
		key := relaySpanProjectionKey(span.TraceID, span.SpanID)
		expected, ok := localSpans[key]
		if !ok ||
			span.Stage != expected.Stage ||
			span.RequestID != expected.RequestID ||
			span.ToolCallID != expected.ToolCallID ||
			span.PolicyVersion != expected.PolicyVersion {
			return fmt.Errorf("external backend Relay span identity does not match the real task")
		}
		if _, duplicate := seenSpans[key]; duplicate {
			return fmt.Errorf("external backend projection contains duplicate Relay span identity")
		}
		seenSpans[key] = struct{}{}
		if span.GatewayID != local.Task.GatewayID ||
			span.Workspace != local.Task.Workspace ||
			span.SandboxID != local.Task.OpenShellSandboxID ||
			span.AgentSessionID != local.Task.AgentSessionID ||
			span.Correlation != expected.Correlation {
			return fmt.Errorf("external backend Relay span correlation does not match the real task")
		}
	}
	return nil
}

func validateCanonicalCorrelationCore(
	correlation canonicalCorrelationContext,
	gatewayID string,
	workspace string,
	sandboxID string,
) error {
	if correlation.GatewayID != gatewayID ||
		correlation.Workspace != workspace ||
		correlation.SandboxID != sandboxID {
		return fmt.Errorf("gateway, workspace, or sandbox identity does not match")
	}
	return nil
}

func validateOptionalCanonicalCorrelation(
	correlation canonicalCorrelationContext,
	agentSessionID string,
) error {
	if correlation.AgentSession != "" && correlation.AgentSession != agentSessionID {
		return fmt.Errorf("agent session identity does not match")
	}
	if correlation.TraceID != "" {
		if err := validateHexIdentifier(correlation.TraceID, 32, "canonical trace_id"); err != nil {
			return err
		}
	}
	for field, value := range map[string]string{
		"canonical agent.session.id":         correlation.AgentSession,
		"canonical request_id":               correlation.RequestID,
		"canonical tool_call_id":             correlation.ToolCallID,
		"canonical openshell.policy.version": correlation.PolicyVersion,
	} {
		if value != "" {
			if err := validateSafeEvidenceLabel(value, field); err != nil {
				return err
			}
		}
	}
	return nil
}

func readStrictTimelineEvidence(path string, target any) ([]byte, error) {
	encoded, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	if len(encoded) > 4*1024*1024 {
		return nil, fmt.Errorf("%s exceeds the 4 MiB evidence limit", filepath.Base(path))
	}
	decoder := json.NewDecoder(bytes.NewReader(encoded))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(target); err != nil {
		return nil, err
	}
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		if err == nil {
			return nil, fmt.Errorf("%s contains multiple JSON values", filepath.Base(path))
		}
		return nil, err
	}
	return encoded, nil
}

func cloudEventProjectionKey(source, id string) string {
	return source + "\x00" + id
}

func relaySpanProjectionKey(traceID, spanID string) string {
	return traceID + "\x00" + spanID
}

func sameStringSet(left, right []string) bool {
	if len(left) != len(right) {
		return false
	}
	leftCopy := append([]string(nil), left...)
	rightCopy := append([]string(nil), right...)
	sort.Strings(leftCopy)
	sort.Strings(rightCopy)
	for index := range leftCopy {
		if leftCopy[index] != rightCopy[index] ||
			(index > 0 && leftCopy[index] == leftCopy[index-1]) {
			return false
		}
	}
	return true
}

func mapKeys(values map[string]struct{}) []string {
	result := make([]string, 0, len(values))
	for value := range values {
		result = append(result, value)
	}
	return result
}

func validExternalTimelineFixture(t *testing.T) (string, string) {
	t.Helper()
	directory := t.TempDir()
	localPath := filepath.Join(directory, "real-gateway-correlation.json")
	backendPath := filepath.Join(directory, correlatedTimelineBackendFile)
	traceID := "00112233445566778899aabbccddeeff"
	sourcePrefix := "openshell://gateway-1/workspaces/default/sandboxes/sandbox-1/sources/"
	coreCorrelation := canonicalCorrelationContext{
		GatewayID: "gateway-1",
		Workspace: "default",
		SandboxID: "sandbox-1",
	}
	relayCorrelation := func(requestID, toolCallID string) canonicalCorrelationContext {
		result := coreCorrelation
		result.AgentSession = "session-1"
		result.TraceID = traceID
		result.RequestID = requestID
		result.ToolCallID = toolCallID
		result.PolicyVersion = "7"
		return result
	}
	startedAt := time.Now().Add(-3 * time.Minute).UTC()
	finishedAt := startedAt.Add(time.Minute)

	local := realCorrelationReport{
		SchemaVersion:          backendEvidenceSchemaVersion,
		Result:                 "passed_real_correlation",
		CandidateBound:         true,
		ExternalRoutingEnabled: true,
		VerifiedAt:             finishedAt.Add(time.Minute).Format(time.RFC3339Nano),
	}
	local.Candidate.RepositoryCommit = strings.Repeat("d", 40)
	local.Candidate.WorktreeClean = true
	local.Task.StartedAt = startedAt.Format(time.RFC3339Nano)
	local.Task.FinishedAt = finishedAt.Format(time.RFC3339Nano)
	local.Task.GatewayID = "gateway-1"
	local.Task.Workspace = "default"
	local.Task.OpenShellSandboxID = "sandbox-1"
	local.Task.AgentSessionID = "session-1"
	local.Task.CreatedFile.Path = "/sandbox/showcase-agent-evidence.txt"
	local.Task.CreatedFile.SHA256 = strings.Repeat("f", 64)
	local.Task.RelayTraceIDs = []string{traceID}
	local.Task.CloudEvents = []realCloudEventIdentity{
		{Source: sourcePrefix + "sandbox.lifecycle", ID: "sha256:" + strings.Repeat("a", 64), Type: "com.nvidia.openshell.sandbox.lifecycle.v1", Correlation: coreCorrelation},
		{Source: sourcePrefix + "ocsf.file", ID: "sha256:" + strings.Repeat("b", 64), Type: "com.nvidia.openshell.ocsf.1007.v1", Correlation: coreCorrelation},
		{Source: sourcePrefix + "ocsf.file", ID: "sha256:" + strings.Repeat("c", 64), Type: "com.nvidia.openshell.ocsf.4001.v1", Correlation: coreCorrelation},
	}
	local.Task.RelaySpans = []realRelaySpanIdentity{
		{TraceID: traceID, SpanID: "0011223344556677", Stage: "prompt", RequestID: "request-1", PolicyVersion: "7", Correlation: relayCorrelation("request-1", "")},
		{TraceID: traceID, SpanID: "1122334455667788", Stage: "model", RequestID: "request-1", PolicyVersion: "7", Correlation: relayCorrelation("request-1", "")},
		{TraceID: traceID, SpanID: "2233445566778899", Stage: "tool", RequestID: "request-1", ToolCallID: "tool-1", PolicyVersion: "7", Correlation: relayCorrelation("request-1", "tool-1")},
	}
	local.Counts.UniqueCloudEvents = len(local.Task.CloudEvents)
	local.Counts.UniqueRelaySpans = len(local.Task.RelaySpans)
	writeJSONFixture(t, localPath, local)
	localEncoded, err := os.ReadFile(localPath)
	if err != nil {
		t.Fatal(err)
	}

	backend := externalCorrelatedTimelineEvidence{
		SchemaVersion:             backendEvidenceSchemaVersion,
		Result:                    "queried_correlated_timeline",
		Receiver:                  "external-staging-observability",
		QueriedAt:                 finishedAt.Add(2 * time.Minute).Format(time.RFC3339Nano),
		CorrelationEvidenceSHA256: evidenceDigest(localEncoded),
		IdentityContract:          correlationIdentityContract,
		Query: externalTimelineQuery{
			GatewayID: "gateway-1", Workspace: "default", SandboxID: "sandbox-1",
			AgentSessionID: "session-1", TraceIDs: []string{traceID},
		},
	}
	for _, event := range local.Task.CloudEvents {
		backend.CloudEvents = append(backend.CloudEvents, externalCloudEventProjection{
			Source: event.Source, ID: event.ID, Type: event.Type,
			GatewayID: "gateway-1", Workspace: "default", SandboxID: "sandbox-1",
			Correlation: event.Correlation,
		})
	}
	for _, span := range local.Task.RelaySpans {
		backend.RelaySpans = append(backend.RelaySpans, externalRelaySpanProjection{
			TraceID: span.TraceID, SpanID: span.SpanID, Stage: span.Stage,
			GatewayID: "gateway-1", Workspace: "default", SandboxID: "sandbox-1",
			AgentSessionID: "session-1", RequestID: span.RequestID,
			ToolCallID: span.ToolCallID, PolicyVersion: span.PolicyVersion,
			Correlation: span.Correlation,
		})
	}
	writeJSONFixture(t, backendPath, backend)
	return localPath, backendPath
}

func TestVerifyExternalCorrelatedTimelineAcceptsExactProjection(t *testing.T) {
	localPath, backendPath := validExternalTimelineFixture(t)
	verification, err := verifyExternalCorrelatedTimeline(localPath, backendPath)
	if err != nil {
		t.Fatal(err)
	}
	if verification.Result != "passed_external_correlated_timeline" ||
		verification.CloudEvents != 3 ||
		verification.RelaySpans != 3 ||
		verification.IdentityContract != correlationIdentityContract ||
		verification.CorrelationEvidenceSHA256 == "" ||
		verification.BackendEvidenceSHA256 == "" {
		t.Fatalf("unexpected verification: %#v", verification)
	}
}

func TestVerifyExternalCorrelatedTimelineRejectsPartialOrMismatchedProjection(t *testing.T) {
	tests := []struct {
		name   string
		mutate func(map[string]any)
	}{
		{
			name: "missing identity contract",
			mutate: func(document map[string]any) {
				document["identity_contract"] = ""
			},
		},
		{
			name: "wrong canonical CloudEvent sandbox",
			mutate: func(document map[string]any) {
				document["cloud_events"].([]any)[0].(map[string]any)["correlation"].(map[string]any)["openshell.sandbox.id"] = "sandbox-other"
			},
		},
		{
			name: "missing CloudEvent",
			mutate: func(document map[string]any) {
				document["cloud_events"] = document["cloud_events"].([]any)[1:]
			},
		},
		{
			name: "wrong sandbox",
			mutate: func(document map[string]any) {
				document["cloud_events"].([]any)[0].(map[string]any)["sandbox_id"] = "sandbox-other"
			},
		},
		{
			name: "missing tool span",
			mutate: func(document map[string]any) {
				document["relay_spans"] = document["relay_spans"].([]any)[:2]
			},
		},
		{
			name: "wrong tool call",
			mutate: func(document map[string]any) {
				document["relay_spans"].([]any)[2].(map[string]any)["tool_call_id"] = "tool-other"
			},
		},
		{
			name: "wrong policy version",
			mutate: func(document map[string]any) {
				document["relay_spans"].([]any)[1].(map[string]any)["policy_version"] = "8"
			},
		},
		{
			name: "wrong canonical Relay session",
			mutate: func(document map[string]any) {
				document["relay_spans"].([]any)[0].(map[string]any)["correlation"].(map[string]any)["agent.session.id"] = "session-other"
			},
		},
		{
			name: "unbound report",
			mutate: func(document map[string]any) {
				document["correlation_evidence_sha256"] = "sha256:" + strings.Repeat("0", 64)
			},
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			localPath, backendPath := validExternalTimelineFixture(t)
			mutateJSONFixture(t, backendPath, test.mutate)
			if _, err := verifyExternalCorrelatedTimeline(localPath, backendPath); err == nil {
				t.Fatal("verification accepted partial or mismatched external projection")
			}
		})
	}
}

func TestVerifyExternalCorrelatedTimelineRejectsDirtyOrUnroutedCandidate(t *testing.T) {
	tests := []struct {
		name   string
		mutate func(map[string]any)
	}{
		{name: "dirty", mutate: func(document map[string]any) {
			document["candidate"].(map[string]any)["worktree_clean"] = false
		}},
		{name: "not candidate bound", mutate: func(document map[string]any) {
			document["candidate_bound"] = false
		}},
		{name: "external routing disabled", mutate: func(document map[string]any) {
			document["external_routing_enabled"] = false
		}},
		{name: "oversized request identity", mutate: func(document map[string]any) {
			document["task"].(map[string]any)["relay_spans"].([]any)[0].(map[string]any)["request_id"] = strings.Repeat("x", 257)
		}},
		{name: "partial policy identity", mutate: func(document map[string]any) {
			document["task"].(map[string]any)["relay_spans"].([]any)[0].(map[string]any)["policy_version"] = ""
		}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			localPath, backendPath := validExternalTimelineFixture(t)
			mutateJSONFixture(t, localPath, test.mutate)
			if _, err := verifyExternalCorrelatedTimeline(localPath, backendPath); err == nil {
				t.Fatal("verification accepted an unqualified real correlation report")
			}
		})
	}
}

func TestExternalCorrelatedTimelineProjectionRejectsUnknownContent(t *testing.T) {
	localPath, backendPath := validExternalTimelineFixture(t)
	mutateJSONFixture(t, backendPath, func(document map[string]any) {
		document["relay_spans"].([]any)[0].(map[string]any)["prompt"] = "must not enter qualification evidence"
	})
	if _, err := verifyExternalCorrelatedTimeline(localPath, backendPath); err == nil {
		t.Fatal("verification accepted content-bearing unknown fields")
	}
}

func TestExternalCorrelatedTimelineVerificationIsRestricted(t *testing.T) {
	localPath, backendPath := validExternalTimelineFixture(t)
	verification, err := verifyExternalCorrelatedTimeline(localPath, backendPath)
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(t.TempDir(), correlatedTimelineVerificationFile)
	if err := writeRestrictedEvidence(path, verification); err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if info.Mode().Perm() != 0o600 {
		t.Fatalf("verification evidence mode = %o, want 600", info.Mode().Perm())
	}
}
