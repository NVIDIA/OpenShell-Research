// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package conformance

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"
)

const cloudEventsResponseSemanticsFile = "external-cloudevents-response-semantics.json"

type responseSemanticsAttempt struct {
	Sequence   int    `json:"sequence"`
	Source     string `json:"source"`
	ID         string `json:"id"`
	Status     int    `json:"status"`
	ReceivedAt string `json:"received_at"`
}

type responseSemanticsRecovery struct {
	Source     string `json:"source"`
	ID         string `json:"id"`
	ObservedAt string `json:"observed_at"`
}

type responseSemanticsScenario struct {
	Name                   string                     `json:"name"`
	InjectedStatus         int                        `json:"injected_status"`
	ExpectedDisposition    string                     `json:"expected_disposition"`
	PayloadRunID           string                     `json:"payload_run_id"`
	PayloadCandidateCommit string                     `json:"payload_candidate_commit"`
	Attempts               []responseSemanticsAttempt `json:"attempts"`
	Recovery               responseSemanticsRecovery  `json:"recovery"`
}

type cloudEventsResponseSemanticsEvidence struct {
	SchemaVersion        string                      `json:"schema_version"`
	RunID                string                      `json:"run_id"`
	CandidateCommit      string                      `json:"candidate_commit"`
	Result               string                      `json:"result"`
	Receiver             string                      `json:"receiver"`
	ObservationStartedAt string                      `json:"observation_started_at"`
	ObservationEndedAt   string                      `json:"observation_ended_at"`
	QueriedAt            string                      `json:"queried_at"`
	Scenarios            []responseSemanticsScenario `json:"scenarios"`
}

type responseSemanticsSummary struct {
	EvidenceSHA256 string `json:"evidence_sha256"`
	QueriedAt      string `json:"queried_at"`
	ScenarioCount  int    `json:"scenario_count"`
	RetryableCount int    `json:"retryable_count"`
	PermanentCount int    `json:"permanent_count"`
}

func readAndValidateCloudEventsResponseSemanticsEvidence(
	directory string,
	runID string,
	probes map[string]probeEvidence,
) (cloudEventsResponseSemanticsEvidence, []byte, responseSemanticsSummary, error) {
	var evidence cloudEventsResponseSemanticsEvidence
	encoded, err := readJSONEvidence(filepath.Join(directory, cloudEventsResponseSemanticsFile), &evidence)
	if err != nil {
		return cloudEventsResponseSemanticsEvidence{}, nil, responseSemanticsSummary{}, fmt.Errorf("read CloudEvents response-semantics evidence: %w", err)
	}
	summary, err := validateCloudEventsResponseSemanticsEvidence(evidence, runID, probes)
	if err != nil {
		return cloudEventsResponseSemanticsEvidence{}, nil, responseSemanticsSummary{}, err
	}
	summary.EvidenceSHA256 = evidenceDigest(encoded)
	return evidence, encoded, summary, nil
}

func validateCloudEventsResponseSemanticsEvidence(
	evidence cloudEventsResponseSemanticsEvidence,
	runID string,
	probes map[string]probeEvidence,
) (responseSemanticsSummary, error) {
	if evidence.SchemaVersion != backendEvidenceSchemaVersion || evidence.RunID != runID {
		return responseSemanticsSummary{}, fmt.Errorf("CloudEvents response-semantics evidence does not match schema or run")
	}
	expectedCommit, err := probeCandidateCommit(probes)
	if err != nil {
		return responseSemanticsSummary{}, err
	}
	if evidence.CandidateCommit != expectedCommit {
		return responseSemanticsSummary{}, fmt.Errorf(
			"CloudEvents response-semantics evidence belongs to candidate %s, expected %s",
			evidence.CandidateCommit,
			expectedCommit,
		)
	}
	if evidence.Result != "queried_response_semantics" || strings.TrimSpace(evidence.Receiver) == "" {
		return responseSemanticsSummary{}, fmt.Errorf("CloudEvents response-semantics evidence does not identify a successful independent receiver query")
	}
	if err := validateEvidenceTimestamp(evidence.QueriedAt, "CloudEvents response-semantics queried_at"); err != nil {
		return responseSemanticsSummary{}, err
	}
	queriedAt, _ := time.Parse(time.RFC3339Nano, evidence.QueriedAt)
	if err := validateEvidenceTimestamp(evidence.ObservationStartedAt, "CloudEvents response-semantics observation_started_at"); err != nil {
		return responseSemanticsSummary{}, err
	}
	if err := validateEvidenceTimestamp(evidence.ObservationEndedAt, "CloudEvents response-semantics observation_ended_at"); err != nil {
		return responseSemanticsSummary{}, err
	}
	observationStartedAt, _ := time.Parse(time.RFC3339Nano, evidence.ObservationStartedAt)
	observationEndedAt, _ := time.Parse(time.RFC3339Nano, evidence.ObservationEndedAt)
	if !observationEndedAt.After(observationStartedAt) ||
		observationEndedAt.Sub(observationStartedAt) < 2*time.Minute {
		return responseSemanticsSummary{}, fmt.Errorf("CloudEvents response-semantics observation window must be at least two minutes")
	}
	if observationEndedAt.After(queriedAt) {
		return responseSemanticsSummary{}, fmt.Errorf("CloudEvents response-semantics observation window ends after queried_at")
	}
	if len(evidence.Scenarios) != 300 {
		return responseSemanticsSummary{}, fmt.Errorf(
			"CloudEvents response-semantics evidence contains %d scenarios, want 300",
			len(evidence.Scenarios),
		)
	}

	seen := make(map[int]struct{}, len(evidence.Scenarios))
	summary := responseSemanticsSummary{
		QueriedAt:     evidence.QueriedAt,
		ScenarioCount: len(evidence.Scenarios),
	}
	for _, scenario := range evidence.Scenarios {
		if scenario.InjectedStatus < 300 || scenario.InjectedStatus > 599 {
			return responseSemanticsSummary{}, fmt.Errorf("response-semantics scenario has out-of-range status %d", scenario.InjectedStatus)
		}
		if _, duplicate := seen[scenario.InjectedStatus]; duplicate {
			return responseSemanticsSummary{}, fmt.Errorf("response-semantics evidence duplicates status %d", scenario.InjectedStatus)
		}
		seen[scenario.InjectedStatus] = struct{}{}
		expectedName := "http-" + strconv.Itoa(scenario.InjectedStatus)
		if scenario.Name != expectedName ||
			scenario.PayloadRunID != runID ||
			scenario.PayloadCandidateCommit != expectedCommit {
			return responseSemanticsSummary{}, fmt.Errorf("response-semantics status %d payload identity does not match run and candidate", scenario.InjectedStatus)
		}
		expectedDisposition := responseDisposition(scenario.InjectedStatus)
		if scenario.ExpectedDisposition != expectedDisposition {
			return responseSemanticsSummary{}, fmt.Errorf(
				"response-semantics status %d disposition=%q, want %q",
				scenario.InjectedStatus,
				scenario.ExpectedDisposition,
				expectedDisposition,
			)
		}
		if err := validateResponseSemanticsAttempts(scenario, observationStartedAt, observationEndedAt, queriedAt); err != nil {
			return responseSemanticsSummary{}, err
		}
		if expectedDisposition == "retryable" {
			summary.RetryableCount++
		} else {
			summary.PermanentCount++
		}
	}
	for statusCode := 300; statusCode <= 599; statusCode++ {
		if _, ok := seen[statusCode]; !ok {
			return responseSemanticsSummary{}, fmt.Errorf("response-semantics evidence is missing status %d", statusCode)
		}
	}
	if summary.RetryableCount != 103 || summary.PermanentCount != 197 {
		return responseSemanticsSummary{}, fmt.Errorf(
			"response-semantics disposition counts retryable=%d permanent=%d, want 103/197",
			summary.RetryableCount,
			summary.PermanentCount,
		)
	}
	return summary, nil
}

func validateResponseSemanticsAttempts(
	scenario responseSemanticsScenario,
	observationStartedAt time.Time,
	observationEndedAt time.Time,
	queriedAt time.Time,
) error {
	retryable := scenario.ExpectedDisposition == "retryable"
	if retryable && len(scenario.Attempts) < 2 {
		return fmt.Errorf("retryable status %d has fewer than two attempts", scenario.InjectedStatus)
	}
	if !retryable && len(scenario.Attempts) != 1 {
		return fmt.Errorf("permanent status %d has %d attempts, want one", scenario.InjectedStatus, len(scenario.Attempts))
	}
	if len(scenario.Attempts) == 0 {
		return fmt.Errorf("status %d has no attempts", scenario.InjectedStatus)
	}
	expectedSource := responseSemanticsSource(scenario.InjectedStatus)
	expectedID := responseSemanticsID(scenario.PayloadRunID, scenario.InjectedStatus)
	var previous time.Time
	for index, attempt := range scenario.Attempts {
		if attempt.Sequence != index+1 {
			return fmt.Errorf("status %d attempt sequence is not contiguous", scenario.InjectedStatus)
		}
		if attempt.Source != expectedSource || attempt.ID != expectedID {
			return fmt.Errorf("status %d changed CloudEvents source+id across attempts", scenario.InjectedStatus)
		}
		if err := validateCloudEventIdentifier(attempt.ID); err != nil {
			return err
		}
		receivedAt, err := time.Parse(time.RFC3339Nano, attempt.ReceivedAt)
		if err != nil {
			return fmt.Errorf("status %d attempt timestamp must be RFC3339: %w", scenario.InjectedStatus, err)
		}
		if receivedAt.Before(observationStartedAt) || receivedAt.After(observationEndedAt) ||
			(!previous.IsZero() && receivedAt.Before(previous)) {
			return fmt.Errorf("status %d attempt timestamps are out of order", scenario.InjectedStatus)
		}
		previous = receivedAt
		if retryable && index == len(scenario.Attempts)-1 {
			if attempt.Status < 200 || attempt.Status >= 300 {
				return fmt.Errorf("retryable status %d did not recover to 2xx", scenario.InjectedStatus)
			}
		} else if attempt.Status != scenario.InjectedStatus {
			return fmt.Errorf(
				"status %d attempt %d observed HTTP %d",
				scenario.InjectedStatus,
				index+1,
				attempt.Status,
			)
		}
	}
	if scenario.Recovery.Source != expectedSource || scenario.Recovery.ID != expectedID {
		return fmt.Errorf("status %d recovery identity does not match delivery identity", scenario.InjectedStatus)
	}
	recoveryAt, err := time.Parse(time.RFC3339Nano, scenario.Recovery.ObservedAt)
	if err != nil {
		return fmt.Errorf("status %d recovery timestamp must be RFC3339: %w", scenario.InjectedStatus, err)
	}
	if recoveryAt.Before(scenarioAttemptTime(scenario.Attempts[0])) || recoveryAt.After(queriedAt) {
		return fmt.Errorf("status %d recovery timestamp is outside the observed interval", scenario.InjectedStatus)
	}
	return nil
}

func scenarioAttemptTime(attempt responseSemanticsAttempt) time.Time {
	value, _ := time.Parse(time.RFC3339Nano, attempt.ReceivedAt)
	return value
}

func responseDisposition(statusCode int) string {
	if statusCode == 408 || statusCode == 425 || statusCode == 429 || statusCode >= 500 {
		return "retryable"
	}
	return "permanent"
}

func responseSemanticsSource(statusCode int) string {
	return "openshell://conformance/response-semantics/http-" + strconv.Itoa(statusCode)
}

func responseSemanticsID(runID string, statusCode int) string {
	sum := sha256.Sum256([]byte("openshell-response-semantics:" + runID + ":" + strconv.Itoa(statusCode)))
	return "sha256:" + hex.EncodeToString(sum[:])
}

func validateCloudEventIdentifier(value string) error {
	if !strings.HasPrefix(value, "sha256:") {
		return fmt.Errorf("CloudEvents id must use sha256")
	}
	return validateHexIdentifier(strings.TrimPrefix(value, "sha256:"), 64, "CloudEvents id")
}

func responseSemanticsFixture(
	runID string,
	candidateCommit string,
	queriedAt time.Time,
) cloudEventsResponseSemanticsEvidence {
	observationStartedAt := queriedAt.Add(-3 * time.Minute)
	observationEndedAt := queriedAt.Add(-30 * time.Second)
	scenarios := make([]responseSemanticsScenario, 0, 300)
	for statusCode := 300; statusCode <= 599; statusCode++ {
		source := responseSemanticsSource(statusCode)
		id := responseSemanticsID(runID, statusCode)
		firstAt := observationStartedAt.Add(time.Second)
		attempts := []responseSemanticsAttempt{
			{
				Sequence:   1,
				Source:     source,
				ID:         id,
				Status:     statusCode,
				ReceivedAt: firstAt.Format(time.RFC3339Nano),
			},
		}
		if responseDisposition(statusCode) == "retryable" {
			attempts = append(attempts, responseSemanticsAttempt{
				Sequence:   2,
				Source:     source,
				ID:         id,
				Status:     202,
				ReceivedAt: firstAt.Add(time.Second).Format(time.RFC3339Nano),
			})
		}
		scenarios = append(scenarios, responseSemanticsScenario{
			Name:                   "http-" + strconv.Itoa(statusCode),
			InjectedStatus:         statusCode,
			ExpectedDisposition:    responseDisposition(statusCode),
			PayloadRunID:           runID,
			PayloadCandidateCommit: candidateCommit,
			Attempts:               attempts,
			Recovery: responseSemanticsRecovery{
				Source:     source,
				ID:         id,
				ObservedAt: firstAt.Add(2 * time.Second).Format(time.RFC3339Nano),
			},
		})
	}
	return cloudEventsResponseSemanticsEvidence{
		SchemaVersion:        backendEvidenceSchemaVersion,
		RunID:                runID,
		CandidateCommit:      candidateCommit,
		Result:               "queried_response_semantics",
		Receiver:             "external-fault-injection-receiver",
		ObservationStartedAt: observationStartedAt.Format(time.RFC3339Nano),
		ObservationEndedAt:   observationEndedAt.Format(time.RFC3339Nano),
		QueriedAt:            queriedAt.Format(time.RFC3339Nano),
		Scenarios:            scenarios,
	}
}

func writeCloudEventsResponseSemanticsFixture(
	t *testing.T,
	directory string,
	runID string,
	candidateCommit string,
	queriedAt time.Time,
) {
	t.Helper()
	writeJSONFixture(
		t,
		filepath.Join(directory, cloudEventsResponseSemanticsFile),
		responseSemanticsFixture(runID, candidateCommit, queriedAt),
	)
}

func TestValidateCloudEventsResponseSemanticsEvidence(t *testing.T) {
	runID := "candidate-response-semantics-2026-a"
	probes := map[string]probeEvidence{
		"cloudevents-https": {CandidateCommit: fixtureCandidateCommit, Probe: "cloudevents-https", Transport: "cloudevents-batch+json/https", Endpoint: "https://receiver.example.test/v1/events"},
		"otlp-http":         {CandidateCommit: fixtureCandidateCommit, Probe: "otlp-http", Transport: "otlp/protobuf/https", Endpoint: "https://otlp.example.test/v1/traces"},
		"otlp-grpc":         {CandidateCommit: fixtureCandidateCommit, Probe: "otlp-grpc", Transport: "otlp/grpc/tls", Endpoint: "otlp.example.test:4317"},
	}
	valid := responseSemanticsFixture(runID, fixtureCandidateCommit, time.Now().UTC())
	summary, err := validateCloudEventsResponseSemanticsEvidence(valid, runID, probes)
	if err != nil {
		t.Fatal(err)
	}
	if summary.ScenarioCount != 300 || summary.RetryableCount != 103 || summary.PermanentCount != 197 {
		t.Fatalf("unexpected response-semantics summary: %#v", summary)
	}

	tests := []struct {
		name   string
		mutate func(*cloudEventsResponseSemanticsEvidence)
	}{
		{
			name: "observation window too short",
			mutate: func(value *cloudEventsResponseSemanticsEvidence) {
				endedAt, _ := time.Parse(time.RFC3339Nano, value.ObservationEndedAt)
				value.ObservationStartedAt = endedAt.Add(-time.Minute).Format(time.RFC3339Nano)
			},
		},
		{
			name: "missing status",
			mutate: func(value *cloudEventsResponseSemanticsEvidence) {
				value.Scenarios = value.Scenarios[:len(value.Scenarios)-1]
			},
		},
		{
			name: "duplicate status",
			mutate: func(value *cloudEventsResponseSemanticsEvidence) {
				value.Scenarios[1].InjectedStatus = value.Scenarios[0].InjectedStatus
			},
		},
		{
			name: "retryable not retried",
			mutate: func(value *cloudEventsResponseSemanticsEvidence) {
				index := 408 - 300
				value.Scenarios[index].Attempts = value.Scenarios[index].Attempts[:1]
			},
		},
		{
			name: "retry changed identity",
			mutate: func(value *cloudEventsResponseSemanticsEvidence) {
				index := 503 - 300
				value.Scenarios[index].Attempts[1].ID = "sha256:" + strings.Repeat("0", 64)
			},
		},
		{
			name: "retry never recovered",
			mutate: func(value *cloudEventsResponseSemanticsEvidence) {
				index := 429 - 300
				value.Scenarios[index].Attempts[1].Status = 429
			},
		},
		{
			name: "permanent retried",
			mutate: func(value *cloudEventsResponseSemanticsEvidence) {
				value.Scenarios[100].Attempts = append(
					value.Scenarios[100].Attempts,
					value.Scenarios[100].Attempts[0],
				)
			},
		},
		{
			name: "recovery identity mismatch",
			mutate: func(value *cloudEventsResponseSemanticsEvidence) {
				value.Scenarios[0].Recovery.ID = "sha256:" + strings.Repeat("0", 64)
			},
		},
		{
			name: "wrong payload candidate",
			mutate: func(value *cloudEventsResponseSemanticsEvidence) {
				value.Scenarios[0].PayloadCandidateCommit = strings.Repeat("b", 40)
			},
		},
		{
			name: "wrong disposition",
			mutate: func(value *cloudEventsResponseSemanticsEvidence) {
				value.Scenarios[125].ExpectedDisposition = "permanent"
			},
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			invalid := responseSemanticsFixture(runID, fixtureCandidateCommit, time.Now().UTC())
			test.mutate(&invalid)
			if _, err := validateCloudEventsResponseSemanticsEvidence(invalid, runID, probes); err == nil {
				t.Fatal("response-semantics validation accepted invalid evidence")
			}
		})
	}
}
