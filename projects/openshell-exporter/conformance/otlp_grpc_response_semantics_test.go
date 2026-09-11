// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package conformance

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"google.golang.org/grpc/codes"
)

const otlpGRPCResponseSemanticsFile = "external-otlp-grpc-response-semantics.json"

type otlpGRPCResponseExpectation struct {
	Name        string
	Code        codes.Code
	RetryInfo   bool
	Disposition string
}

var otlpGRPCResponseExpectations = []otlpGRPCResponseExpectation{
	{Name: "canceled", Code: codes.Canceled, Disposition: "retryable"},
	{Name: "unknown", Code: codes.Unknown, Disposition: "permanent"},
	{Name: "invalid-argument", Code: codes.InvalidArgument, Disposition: "permanent"},
	{Name: "deadline-exceeded", Code: codes.DeadlineExceeded, Disposition: "retryable"},
	{Name: "not-found", Code: codes.NotFound, Disposition: "permanent"},
	{Name: "already-exists", Code: codes.AlreadyExists, Disposition: "permanent"},
	{Name: "permission-denied", Code: codes.PermissionDenied, Disposition: "permanent"},
	{Name: "resource-exhausted-without-retry-info", Code: codes.ResourceExhausted, Disposition: "permanent"},
	{Name: "resource-exhausted-with-retry-info", Code: codes.ResourceExhausted, RetryInfo: true, Disposition: "retryable"},
	{Name: "failed-precondition", Code: codes.FailedPrecondition, Disposition: "permanent"},
	{Name: "aborted", Code: codes.Aborted, Disposition: "retryable"},
	{Name: "out-of-range", Code: codes.OutOfRange, Disposition: "retryable"},
	{Name: "unimplemented", Code: codes.Unimplemented, Disposition: "permanent"},
	{Name: "internal", Code: codes.Internal, Disposition: "permanent"},
	{Name: "unavailable", Code: codes.Unavailable, Disposition: "retryable"},
	{Name: "data-loss", Code: codes.DataLoss, Disposition: "retryable"},
	{Name: "unauthenticated", Code: codes.Unauthenticated, Disposition: "permanent"},
}

type otlpGRPCResponseAttempt struct {
	Sequence   int    `json:"sequence"`
	TraceID    string `json:"trace_id"`
	SpanID     string `json:"span_id"`
	Code       string `json:"code"`
	ReceivedAt string `json:"received_at"`
}

type otlpGRPCResponseScenario struct {
	Name                   string                    `json:"name"`
	InjectedCode           string                    `json:"injected_code"`
	RetryInfo              bool                      `json:"retry_info"`
	ExpectedDisposition    string                    `json:"expected_disposition"`
	PayloadRunID           string                    `json:"payload_run_id"`
	PayloadCandidateCommit string                    `json:"payload_candidate_commit"`
	Attempts               []otlpGRPCResponseAttempt `json:"attempts"`
}

type otlpGRPCResponseSemanticsEvidence struct {
	SchemaVersion        string                     `json:"schema_version"`
	RunID                string                     `json:"run_id"`
	CandidateCommit      string                     `json:"candidate_commit"`
	Result               string                     `json:"result"`
	Backend              string                     `json:"backend"`
	ObservationStartedAt string                     `json:"observation_started_at"`
	ObservationEndedAt   string                     `json:"observation_ended_at"`
	QueriedAt            string                     `json:"queried_at"`
	Scenarios            []otlpGRPCResponseScenario `json:"scenarios"`
}

type otlpGRPCResponseSemanticsSummary struct {
	EvidenceSHA256 string `json:"evidence_sha256"`
	QueriedAt      string `json:"queried_at"`
	ScenarioCount  int    `json:"scenario_count"`
	RetryableCount int    `json:"retryable_count"`
	PermanentCount int    `json:"permanent_count"`
}

func readAndValidateOTLPGRPCResponseSemanticsEvidence(
	directory string,
	runID string,
	probes map[string]probeEvidence,
) (otlpGRPCResponseSemanticsSummary, error) {
	var evidence otlpGRPCResponseSemanticsEvidence
	encoded, err := readJSONEvidence(filepath.Join(directory, otlpGRPCResponseSemanticsFile), &evidence)
	if err != nil {
		return otlpGRPCResponseSemanticsSummary{}, fmt.Errorf("read OTLP gRPC response-semantics evidence: %w", err)
	}
	summary, err := validateOTLPGRPCResponseSemanticsEvidence(evidence, runID, probes)
	if err != nil {
		return otlpGRPCResponseSemanticsSummary{}, err
	}
	summary.EvidenceSHA256 = evidenceDigest(encoded)
	return summary, nil
}

func validateOTLPGRPCResponseSemanticsEvidence(
	evidence otlpGRPCResponseSemanticsEvidence,
	runID string,
	probes map[string]probeEvidence,
) (otlpGRPCResponseSemanticsSummary, error) {
	if evidence.SchemaVersion != backendEvidenceSchemaVersion || evidence.RunID != runID {
		return otlpGRPCResponseSemanticsSummary{}, fmt.Errorf("OTLP gRPC response-semantics evidence does not match schema or run")
	}
	expectedCommit, err := probeCandidateCommit(probes)
	if err != nil {
		return otlpGRPCResponseSemanticsSummary{}, err
	}
	if evidence.CandidateCommit != expectedCommit {
		return otlpGRPCResponseSemanticsSummary{}, fmt.Errorf("OTLP gRPC response-semantics evidence belongs to candidate %s, expected %s", evidence.CandidateCommit, expectedCommit)
	}
	if evidence.Result != "queried_otlp_grpc_response_semantics" || strings.TrimSpace(evidence.Backend) == "" {
		return otlpGRPCResponseSemanticsSummary{}, fmt.Errorf("OTLP gRPC response-semantics evidence does not identify a successful independent backend query")
	}
	queriedAt, observationStartedAt, observationEndedAt, err := validateOTLPGRPCObservationWindow(evidence)
	if err != nil {
		return otlpGRPCResponseSemanticsSummary{}, err
	}
	if len(evidence.Scenarios) != len(otlpGRPCResponseExpectations) {
		return otlpGRPCResponseSemanticsSummary{}, fmt.Errorf("OTLP gRPC response-semantics evidence contains %d scenarios, want %d", len(evidence.Scenarios), len(otlpGRPCResponseExpectations))
	}

	expectedByName := make(map[string]otlpGRPCResponseExpectation, len(otlpGRPCResponseExpectations))
	for _, expectation := range otlpGRPCResponseExpectations {
		expectedByName[expectation.Name] = expectation
	}
	seen := make(map[string]struct{}, len(evidence.Scenarios))
	summary := otlpGRPCResponseSemanticsSummary{QueriedAt: evidence.QueriedAt, ScenarioCount: len(evidence.Scenarios)}
	for _, scenario := range evidence.Scenarios {
		expectation, ok := expectedByName[scenario.Name]
		if !ok {
			return otlpGRPCResponseSemanticsSummary{}, fmt.Errorf("OTLP gRPC response-semantics evidence contains unsupported scenario %q", scenario.Name)
		}
		if _, duplicate := seen[scenario.Name]; duplicate {
			return otlpGRPCResponseSemanticsSummary{}, fmt.Errorf("OTLP gRPC response-semantics evidence duplicates scenario %q", scenario.Name)
		}
		seen[scenario.Name] = struct{}{}
		if scenario.InjectedCode != expectation.Code.String() ||
			scenario.RetryInfo != expectation.RetryInfo ||
			scenario.ExpectedDisposition != expectation.Disposition ||
			scenario.PayloadRunID != runID ||
			scenario.PayloadCandidateCommit != expectedCommit {
			return otlpGRPCResponseSemanticsSummary{}, fmt.Errorf("OTLP gRPC response-semantics scenario %q does not match the required code, RetryInfo, disposition, run, and candidate", scenario.Name)
		}
		if err := validateOTLPGRPCResponseAttempts(scenario, observationStartedAt, observationEndedAt, queriedAt); err != nil {
			return otlpGRPCResponseSemanticsSummary{}, err
		}
		if expectation.Disposition == "retryable" {
			summary.RetryableCount++
		} else {
			summary.PermanentCount++
		}
	}
	if summary.RetryableCount != 7 || summary.PermanentCount != 10 {
		return otlpGRPCResponseSemanticsSummary{}, fmt.Errorf("OTLP gRPC disposition counts retryable=%d permanent=%d, want 7/10", summary.RetryableCount, summary.PermanentCount)
	}
	return summary, nil
}

func validateOTLPGRPCObservationWindow(evidence otlpGRPCResponseSemanticsEvidence) (time.Time, time.Time, time.Time, error) {
	if err := validateEvidenceTimestamp(evidence.QueriedAt, "OTLP gRPC response-semantics queried_at"); err != nil {
		return time.Time{}, time.Time{}, time.Time{}, err
	}
	if err := validateEvidenceTimestamp(evidence.ObservationStartedAt, "OTLP gRPC response-semantics observation_started_at"); err != nil {
		return time.Time{}, time.Time{}, time.Time{}, err
	}
	if err := validateEvidenceTimestamp(evidence.ObservationEndedAt, "OTLP gRPC response-semantics observation_ended_at"); err != nil {
		return time.Time{}, time.Time{}, time.Time{}, err
	}
	queriedAt, _ := time.Parse(time.RFC3339Nano, evidence.QueriedAt)
	startedAt, _ := time.Parse(time.RFC3339Nano, evidence.ObservationStartedAt)
	endedAt, _ := time.Parse(time.RFC3339Nano, evidence.ObservationEndedAt)
	if !endedAt.After(startedAt) || endedAt.Sub(startedAt) < 2*time.Minute {
		return time.Time{}, time.Time{}, time.Time{}, fmt.Errorf("OTLP gRPC response-semantics observation window must be at least two minutes")
	}
	if endedAt.After(queriedAt) {
		return time.Time{}, time.Time{}, time.Time{}, fmt.Errorf("OTLP gRPC response-semantics observation window ends after queried_at")
	}
	return queriedAt, startedAt, endedAt, nil
}

func validateOTLPGRPCResponseAttempts(
	scenario otlpGRPCResponseScenario,
	observationStartedAt time.Time,
	observationEndedAt time.Time,
	queriedAt time.Time,
) error {
	expectedAttempts := 1
	if scenario.ExpectedDisposition == "retryable" {
		expectedAttempts = 2
	}
	if len(scenario.Attempts) != expectedAttempts {
		return fmt.Errorf("OTLP gRPC scenario %q has %d attempts, want %d", scenario.Name, len(scenario.Attempts), expectedAttempts)
	}
	expectedTraceID, expectedSpanID := otlpGRPCResponseIdentity(scenario.PayloadRunID, scenario.Name)
	var previous time.Time
	for index, attempt := range scenario.Attempts {
		if attempt.Sequence != index+1 || attempt.TraceID != expectedTraceID || attempt.SpanID != expectedSpanID {
			return fmt.Errorf("OTLP gRPC scenario %q changed trace/span identity or sequence", scenario.Name)
		}
		if err := validateHexIdentifier(attempt.TraceID, 32, "OTLP gRPC trace_id"); err != nil {
			return err
		}
		if err := validateHexIdentifier(attempt.SpanID, 16, "OTLP gRPC span_id"); err != nil {
			return err
		}
		receivedAt, err := time.Parse(time.RFC3339Nano, attempt.ReceivedAt)
		if err != nil || receivedAt.Before(observationStartedAt) || receivedAt.After(observationEndedAt) || (!previous.IsZero() && receivedAt.Before(previous)) {
			return fmt.Errorf("OTLP gRPC scenario %q has an invalid or out-of-window attempt timestamp", scenario.Name)
		}
		previous = receivedAt
		expectedCode := scenario.InjectedCode
		if scenario.ExpectedDisposition == "retryable" && index == 1 {
			expectedCode = codes.OK.String()
		}
		if attempt.Code != expectedCode {
			return fmt.Errorf("OTLP gRPC scenario %q attempt %d code=%q, want %q", scenario.Name, index+1, attempt.Code, expectedCode)
		}
	}
	if observationEndedAt.After(queriedAt) {
		return fmt.Errorf("OTLP gRPC scenario %q was queried before its observation ended", scenario.Name)
	}
	return nil
}

func otlpGRPCResponseIdentity(runID string, name string) (string, string) {
	digest := sha256.Sum256([]byte("openshell-otlp-grpc-response-semantics:" + runID + ":" + name))
	return hex.EncodeToString(digest[:16]), hex.EncodeToString(digest[16:24])
}

func otlpGRPCResponseSemanticsFixture(runID string, candidateCommit string, queriedAt time.Time) otlpGRPCResponseSemanticsEvidence {
	startedAt := queriedAt.Add(-3 * time.Minute)
	endedAt := queriedAt.Add(-30 * time.Second)
	scenarios := make([]otlpGRPCResponseScenario, 0, len(otlpGRPCResponseExpectations))
	for index, expectation := range otlpGRPCResponseExpectations {
		traceID, spanID := otlpGRPCResponseIdentity(runID, expectation.Name)
		firstAt := startedAt.Add(time.Duration(index+1) * time.Second)
		attempts := []otlpGRPCResponseAttempt{{Sequence: 1, TraceID: traceID, SpanID: spanID, Code: expectation.Code.String(), ReceivedAt: firstAt.Format(time.RFC3339Nano)}}
		if expectation.Disposition == "retryable" {
			attempts = append(attempts, otlpGRPCResponseAttempt{Sequence: 2, TraceID: traceID, SpanID: spanID, Code: codes.OK.String(), ReceivedAt: firstAt.Add(time.Second).Format(time.RFC3339Nano)})
		}
		scenarios = append(scenarios, otlpGRPCResponseScenario{
			Name: expectation.Name, InjectedCode: expectation.Code.String(), RetryInfo: expectation.RetryInfo,
			ExpectedDisposition: expectation.Disposition, PayloadRunID: runID,
			PayloadCandidateCommit: candidateCommit, Attempts: attempts,
		})
	}
	return otlpGRPCResponseSemanticsEvidence{
		SchemaVersion: backendEvidenceSchemaVersion, RunID: runID, CandidateCommit: candidateCommit,
		Result: "queried_otlp_grpc_response_semantics", Backend: "external-otlp-fault-injection-backend",
		ObservationStartedAt: startedAt.Format(time.RFC3339Nano), ObservationEndedAt: endedAt.Format(time.RFC3339Nano),
		QueriedAt: queriedAt.Format(time.RFC3339Nano), Scenarios: scenarios,
	}
}

func writeOTLPGRPCResponseSemanticsFixture(t *testing.T, directory string, runID string, candidateCommit string, queriedAt time.Time) {
	t.Helper()
	writeJSONFixture(t, filepath.Join(directory, otlpGRPCResponseSemanticsFile), otlpGRPCResponseSemanticsFixture(runID, candidateCommit, queriedAt))
}

func TestValidateOTLPGRPCResponseSemanticsEvidence(t *testing.T) {
	runID := "candidate-otlp-grpc-response-2026-a"
	probes := map[string]probeEvidence{
		"cloudevents-https": {CandidateCommit: fixtureCandidateCommit},
		"otlp-http":         {CandidateCommit: fixtureCandidateCommit},
		"otlp-grpc":         {CandidateCommit: fixtureCandidateCommit},
	}
	valid := otlpGRPCResponseSemanticsFixture(runID, fixtureCandidateCommit, time.Now().UTC())
	summary, err := validateOTLPGRPCResponseSemanticsEvidence(valid, runID, probes)
	if err != nil {
		t.Fatal(err)
	}
	if summary.ScenarioCount != 17 || summary.RetryableCount != 7 || summary.PermanentCount != 10 {
		t.Fatalf("unexpected OTLP gRPC response summary: %#v", summary)
	}

	tests := []struct {
		name   string
		mutate func(*otlpGRPCResponseSemanticsEvidence)
	}{
		{name: "missing scenario", mutate: func(value *otlpGRPCResponseSemanticsEvidence) { value.Scenarios = value.Scenarios[:16] }},
		{name: "duplicate scenario", mutate: func(value *otlpGRPCResponseSemanticsEvidence) { value.Scenarios[1].Name = value.Scenarios[0].Name }},
		{name: "retryable not retried", mutate: func(value *otlpGRPCResponseSemanticsEvidence) {
			value.Scenarios[0].Attempts = value.Scenarios[0].Attempts[:1]
		}},
		{name: "permanent retried", mutate: func(value *otlpGRPCResponseSemanticsEvidence) {
			value.Scenarios[1].Attempts = append(value.Scenarios[1].Attempts, value.Scenarios[1].Attempts[0])
		}},
		{name: "identity changed", mutate: func(value *otlpGRPCResponseSemanticsEvidence) {
			value.Scenarios[0].Attempts[1].TraceID = strings.Repeat("0", 32)
		}},
		{name: "resource exhausted missing RetryInfo", mutate: func(value *otlpGRPCResponseSemanticsEvidence) { value.Scenarios[8].RetryInfo = false }},
		{name: "wrong candidate", mutate: func(value *otlpGRPCResponseSemanticsEvidence) { value.CandidateCommit = strings.Repeat("b", 40) }},
		{name: "short observation", mutate: func(value *otlpGRPCResponseSemanticsEvidence) {
			endedAt, _ := time.Parse(time.RFC3339Nano, value.ObservationEndedAt)
			value.ObservationStartedAt = endedAt.Add(-time.Minute).Format(time.RFC3339Nano)
		}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			invalid := otlpGRPCResponseSemanticsFixture(runID, fixtureCandidateCommit, time.Now().UTC())
			test.mutate(&invalid)
			if _, err := validateOTLPGRPCResponseSemanticsEvidence(invalid, runID, probes); err == nil {
				t.Fatal("OTLP gRPC response-semantics validation accepted invalid evidence")
			}
		})
	}
}
