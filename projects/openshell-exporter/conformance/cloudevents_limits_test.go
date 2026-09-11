// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package conformance

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"path/filepath"
	"sort"
	"strings"
	"testing"
	"time"
)

const (
	cloudEventsLimitsFile             = "external-cloudevents-limits.json"
	cloudEventsLimitMaxEvents         = 500
	cloudEventsLimitMaxEventBytes     = 1024 * 1024
	cloudEventsLimitMaxRequestBytes   = 4 * 1024 * 1024
	cloudEventsCountScenarioEvents    = 501
	cloudEventsByteScenarioEvents     = 7
	cloudEventsLimitsObservationFloor = 2 * time.Minute
)

type cloudEventsLimitEvent struct {
	ID           string `json:"id"`
	Source       string `json:"source"`
	EncodedBytes int    `json:"encoded_bytes"`
}

type cloudEventsLimitRequest struct {
	Sequence     int                     `json:"sequence"`
	ReceivedAt   string                  `json:"received_at"`
	EncodedBytes int                     `json:"encoded_bytes"`
	Events       []cloudEventsLimitEvent `json:"events"`
}

type cloudEventsLimitScenario struct {
	Name        string                    `json:"name"`
	InputEvents int                       `json:"input_events"`
	Requests    []cloudEventsLimitRequest `json:"requests"`
}

type cloudEventsOversizedEvidence struct {
	ID               string `json:"id"`
	Source           string `json:"source"`
	EncodedBytes     int    `json:"encoded_bytes"`
	WebhookAttempts  int    `json:"webhook_attempts"`
	RecoveryObserved bool   `json:"recovery_observed"`
	RecoveryID       string `json:"recovery_id"`
	RecoverySource   string `json:"recovery_source"`
	ObservedAt       string `json:"observed_at"`
}

type cloudEventsLimitsEvidence struct {
	SchemaVersion        string                       `json:"schema_version"`
	RunID                string                       `json:"run_id"`
	CandidateCommit      string                       `json:"candidate_commit"`
	Result               string                       `json:"result"`
	Backend              string                       `json:"backend"`
	ObservationStartedAt string                       `json:"observation_started_at"`
	ObservationEndedAt   string                       `json:"observation_ended_at"`
	QueriedAt            string                       `json:"queried_at"`
	Scenarios            []cloudEventsLimitScenario   `json:"scenarios"`
	Oversized            cloudEventsOversizedEvidence `json:"oversized"`
}

type cloudEventsLimitsSummary struct {
	EvidenceSHA256      string `json:"evidence_sha256"`
	QueriedAt           string `json:"queried_at"`
	CountEvents         int    `json:"count_events"`
	ByteEvents          int    `json:"byte_events"`
	AcceptedRequests    int    `json:"accepted_requests"`
	OversizedRecoveries int    `json:"oversized_recoveries"`
}

func readAndValidateCloudEventsLimitsEvidence(
	directory string,
	runID string,
	probes map[string]probeEvidence,
) (cloudEventsLimitsSummary, error) {
	var evidence cloudEventsLimitsEvidence
	encoded, err := readJSONEvidence(filepath.Join(directory, cloudEventsLimitsFile), &evidence)
	if err != nil {
		return cloudEventsLimitsSummary{}, fmt.Errorf("read CloudEvents limits evidence: %w", err)
	}
	summary, err := validateCloudEventsLimitsEvidence(evidence, runID, probes)
	if err != nil {
		return cloudEventsLimitsSummary{}, err
	}
	summary.EvidenceSHA256 = evidenceDigest(encoded)
	return summary, nil
}

func validateCloudEventsLimitsEvidence(
	evidence cloudEventsLimitsEvidence,
	runID string,
	probes map[string]probeEvidence,
) (cloudEventsLimitsSummary, error) {
	if evidence.SchemaVersion != backendEvidenceSchemaVersion || evidence.RunID != runID {
		return cloudEventsLimitsSummary{}, fmt.Errorf("CloudEvents limits evidence does not match schema or run")
	}
	expectedCommit, err := probeCandidateCommit(probes)
	if err != nil {
		return cloudEventsLimitsSummary{}, err
	}
	if evidence.CandidateCommit != expectedCommit {
		return cloudEventsLimitsSummary{}, fmt.Errorf("CloudEvents limits evidence belongs to candidate %s, expected %s", evidence.CandidateCommit, expectedCommit)
	}
	if evidence.Result != "queried_cloudevents_limits" || strings.TrimSpace(evidence.Backend) == "" {
		return cloudEventsLimitsSummary{}, fmt.Errorf("CloudEvents limits evidence does not identify a successful independent receiver query")
	}
	startedAt, endedAt, _, err := validateCloudEventsLimitsWindow(evidence)
	if err != nil {
		return cloudEventsLimitsSummary{}, err
	}

	expectedCounts := map[string]int{
		"count-split": cloudEventsCountScenarioEvents,
		"byte-split":  cloudEventsByteScenarioEvents,
	}
	if len(evidence.Scenarios) != len(expectedCounts) {
		return cloudEventsLimitsSummary{}, fmt.Errorf("CloudEvents limits evidence must contain exactly count-split and byte-split scenarios")
	}
	summary := cloudEventsLimitsSummary{QueriedAt: evidence.QueriedAt}
	seen := make(map[string]struct{}, len(evidence.Scenarios))
	for _, scenario := range evidence.Scenarios {
		expectedEvents, ok := expectedCounts[scenario.Name]
		if !ok {
			return cloudEventsLimitsSummary{}, fmt.Errorf("CloudEvents limits evidence contains unsupported scenario %q", scenario.Name)
		}
		if _, duplicate := seen[scenario.Name]; duplicate {
			return cloudEventsLimitsSummary{}, fmt.Errorf("CloudEvents limits evidence duplicates scenario %q", scenario.Name)
		}
		seen[scenario.Name] = struct{}{}
		requests, err := validateCloudEventsLimitScenario(
			scenario,
			runID,
			expectedCommit,
			expectedEvents,
			startedAt,
			endedAt,
		)
		if err != nil {
			return cloudEventsLimitsSummary{}, err
		}
		summary.AcceptedRequests += requests
		if scenario.Name == "count-split" {
			summary.CountEvents = scenario.InputEvents
		} else {
			summary.ByteEvents = scenario.InputEvents
		}
	}
	if err := validateCloudEventsOversizedEvidence(
		evidence.Oversized,
		runID,
		expectedCommit,
		startedAt,
		endedAt,
	); err != nil {
		return cloudEventsLimitsSummary{}, err
	}
	summary.OversizedRecoveries = 1
	return summary, nil
}

func validateCloudEventsLimitsWindow(
	evidence cloudEventsLimitsEvidence,
) (time.Time, time.Time, time.Time, error) {
	for label, value := range map[string]string{
		"CloudEvents limits observation_started_at": evidence.ObservationStartedAt,
		"CloudEvents limits observation_ended_at":   evidence.ObservationEndedAt,
		"CloudEvents limits queried_at":             evidence.QueriedAt,
	} {
		if err := validateEvidenceTimestamp(value, label); err != nil {
			return time.Time{}, time.Time{}, time.Time{}, err
		}
	}
	startedAt, _ := time.Parse(time.RFC3339Nano, evidence.ObservationStartedAt)
	endedAt, _ := time.Parse(time.RFC3339Nano, evidence.ObservationEndedAt)
	queriedAt, _ := time.Parse(time.RFC3339Nano, evidence.QueriedAt)
	if !endedAt.After(startedAt) || endedAt.Sub(startedAt) < cloudEventsLimitsObservationFloor {
		return time.Time{}, time.Time{}, time.Time{}, fmt.Errorf("CloudEvents limits observation window must be at least two minutes")
	}
	if endedAt.After(queriedAt) {
		return time.Time{}, time.Time{}, time.Time{}, fmt.Errorf("CloudEvents limits observation ends after queried_at")
	}
	return startedAt, endedAt, queriedAt, nil
}

func validateCloudEventsLimitScenario(
	scenario cloudEventsLimitScenario,
	runID string,
	candidateCommit string,
	expectedEvents int,
	startedAt time.Time,
	endedAt time.Time,
) (int, error) {
	if scenario.InputEvents != expectedEvents {
		return 0, fmt.Errorf("CloudEvents %s input_events=%d, want %d", scenario.Name, scenario.InputEvents, expectedEvents)
	}
	if scenario.Name == "count-split" && len(scenario.Requests) != 2 {
		return 0, fmt.Errorf("CloudEvents count-split produced %d requests, want 2", len(scenario.Requests))
	}
	if scenario.Name == "byte-split" && len(scenario.Requests) < 2 {
		return 0, fmt.Errorf("CloudEvents byte-split must produce at least two requests")
	}

	source := "openshell://conformance/limits/" + scenario.Name
	seenIDs := make(map[string]struct{}, expectedEvents)
	totalEvents := 0
	totalStandaloneBytes := 2
	requestCounts := make([]int, 0, len(scenario.Requests))
	for requestIndex, request := range scenario.Requests {
		if request.Sequence != requestIndex+1 {
			return 0, fmt.Errorf("CloudEvents %s request sequence is not contiguous", scenario.Name)
		}
		receivedAt, err := time.Parse(time.RFC3339Nano, request.ReceivedAt)
		if err != nil || receivedAt.Before(startedAt) || receivedAt.After(endedAt) {
			return 0, fmt.Errorf("CloudEvents %s request %d has invalid received_at", scenario.Name, request.Sequence)
		}
		if len(request.Events) == 0 || len(request.Events) > cloudEventsLimitMaxEvents {
			return 0, fmt.Errorf("CloudEvents %s request %d event count=%d, want 1..500", scenario.Name, request.Sequence, len(request.Events))
		}
		calculatedBytes := 2
		for _, event := range request.Events {
			if event.Source != source || event.EncodedBytes <= 0 || event.EncodedBytes > cloudEventsLimitMaxEventBytes {
				return 0, fmt.Errorf("CloudEvents %s request %d contains invalid source or event size", scenario.Name, request.Sequence)
			}
			if _, duplicate := seenIDs[event.ID]; duplicate {
				return 0, fmt.Errorf("CloudEvents %s duplicates event ID %q", scenario.Name, event.ID)
			}
			seenIDs[event.ID] = struct{}{}
			calculatedBytes += event.EncodedBytes
			totalStandaloneBytes += event.EncodedBytes
		}
		calculatedBytes += len(request.Events) - 1
		if request.EncodedBytes != calculatedBytes || request.EncodedBytes > cloudEventsLimitMaxRequestBytes {
			return 0, fmt.Errorf("CloudEvents %s request %d encoded bytes=%d, calculated=%d", scenario.Name, request.Sequence, request.EncodedBytes, calculatedBytes)
		}
		totalEvents += len(request.Events)
		requestCounts = append(requestCounts, len(request.Events))
	}
	totalStandaloneBytes += totalEvents - 1
	if totalEvents != expectedEvents || len(seenIDs) != expectedEvents {
		return 0, fmt.Errorf("CloudEvents %s delivered %d events and %d unique IDs, want %d", scenario.Name, totalEvents, len(seenIDs), expectedEvents)
	}
	for index := 0; index < expectedEvents; index++ {
		expectedID := cloudEventsLimitIdentity(runID, candidateCommit, scenario.Name, index)
		if _, ok := seenIDs[expectedID]; !ok {
			return 0, fmt.Errorf("CloudEvents %s is missing candidate-bound event %d", scenario.Name, index)
		}
	}
	if scenario.Name == "count-split" {
		sort.Ints(requestCounts)
		if requestCounts[0] != 1 || requestCounts[1] != cloudEventsLimitMaxEvents {
			return 0, fmt.Errorf("CloudEvents count-split request counts=%v, want [1 500]", requestCounts)
		}
	}
	if scenario.Name == "byte-split" && totalStandaloneBytes <= cloudEventsLimitMaxRequestBytes {
		return 0, fmt.Errorf("CloudEvents byte-split input did not exceed 4 MiB")
	}
	return len(scenario.Requests), nil
}

func validateCloudEventsOversizedEvidence(
	evidence cloudEventsOversizedEvidence,
	runID string,
	candidateCommit string,
	startedAt time.Time,
	endedAt time.Time,
) error {
	expectedSource := "openshell://conformance/limits/oversized"
	expectedID := cloudEventsLimitIdentity(runID, candidateCommit, "oversized", 0)
	observedAt, err := time.Parse(time.RFC3339Nano, evidence.ObservedAt)
	if err != nil || observedAt.Before(startedAt) || observedAt.After(endedAt) {
		return fmt.Errorf("CloudEvents oversized recovery has invalid observed_at")
	}
	if evidence.ID != expectedID || evidence.Source != expectedSource ||
		evidence.EncodedBytes <= cloudEventsLimitMaxEventBytes ||
		evidence.WebhookAttempts != 0 || !evidence.RecoveryObserved ||
		evidence.RecoveryID != expectedID || evidence.RecoverySource != expectedSource {
		return fmt.Errorf("CloudEvents oversized evidence does not prove webhook exclusion and identity-preserving recovery")
	}
	return nil
}

func cloudEventsLimitIdentity(
	runID string,
	candidateCommit string,
	scenario string,
	index int,
) string {
	sum := sha256.Sum256([]byte(fmt.Sprintf(
		"openshell-cloudevents-limits:%s:%s:%s:%d",
		runID,
		candidateCommit,
		scenario,
		index,
	)))
	return "sha256:" + hex.EncodeToString(sum[:])
}

func cloudEventsLimitsFixture(
	runID string,
	candidateCommit string,
	queriedAt time.Time,
) cloudEventsLimitsEvidence {
	startedAt := queriedAt.Add(-3 * time.Minute)
	endedAt := queriedAt.Add(-30 * time.Second)
	countEvents := makeLimitEvents(runID, candidateCommit, "count-split", cloudEventsCountScenarioEvents, 700)
	byteEvents := makeLimitEvents(runID, candidateCommit, "byte-split", cloudEventsByteScenarioEvents, 700*1024)
	requestAt := startedAt.Add(time.Minute).Format(time.RFC3339Nano)
	oversizedID := cloudEventsLimitIdentity(runID, candidateCommit, "oversized", 0)
	oversizedSource := "openshell://conformance/limits/oversized"
	return cloudEventsLimitsEvidence{
		SchemaVersion:        backendEvidenceSchemaVersion,
		RunID:                runID,
		CandidateCommit:      candidateCommit,
		Result:               "queried_cloudevents_limits",
		Backend:              "external-cloudevents-limit-receiver",
		ObservationStartedAt: startedAt.Format(time.RFC3339Nano),
		ObservationEndedAt:   endedAt.Format(time.RFC3339Nano),
		QueriedAt:            queriedAt.Format(time.RFC3339Nano),
		Scenarios: []cloudEventsLimitScenario{
			{
				Name:        "count-split",
				InputEvents: cloudEventsCountScenarioEvents,
				Requests: []cloudEventsLimitRequest{
					makeLimitRequest(1, requestAt, countEvents[:500]),
					makeLimitRequest(2, requestAt, countEvents[500:]),
				},
			},
			{
				Name:        "byte-split",
				InputEvents: cloudEventsByteScenarioEvents,
				Requests: []cloudEventsLimitRequest{
					makeLimitRequest(1, requestAt, byteEvents[:5]),
					makeLimitRequest(2, requestAt, byteEvents[5:]),
				},
			},
		},
		Oversized: cloudEventsOversizedEvidence{
			ID:               oversizedID,
			Source:           oversizedSource,
			EncodedBytes:     cloudEventsLimitMaxEventBytes + 1,
			WebhookAttempts:  0,
			RecoveryObserved: true,
			RecoveryID:       oversizedID,
			RecoverySource:   oversizedSource,
			ObservedAt:       requestAt,
		},
	}
}

func makeLimitEvents(
	runID string,
	candidateCommit string,
	scenario string,
	count int,
	encodedBytes int,
) []cloudEventsLimitEvent {
	events := make([]cloudEventsLimitEvent, 0, count)
	source := "openshell://conformance/limits/" + scenario
	for index := 0; index < count; index++ {
		events = append(events, cloudEventsLimitEvent{
			ID:           cloudEventsLimitIdentity(runID, candidateCommit, scenario, index),
			Source:       source,
			EncodedBytes: encodedBytes,
		})
	}
	return events
}

func makeLimitRequest(
	sequence int,
	receivedAt string,
	events []cloudEventsLimitEvent,
) cloudEventsLimitRequest {
	encodedBytes := 2
	for _, event := range events {
		encodedBytes += event.EncodedBytes
	}
	if len(events) > 0 {
		encodedBytes += len(events) - 1
	}
	return cloudEventsLimitRequest{
		Sequence:     sequence,
		ReceivedAt:   receivedAt,
		EncodedBytes: encodedBytes,
		Events:       events,
	}
}

func TestValidateCloudEventsLimitsEvidence(t *testing.T) {
	runID := "candidate-limits-2026-a"
	probes := map[string]probeEvidence{
		"cloudevents-https": {CandidateCommit: fixtureCandidateCommit},
		"otlp-http":         {CandidateCommit: fixtureCandidateCommit},
		"otlp-grpc":         {CandidateCommit: fixtureCandidateCommit},
	}
	evidence := cloudEventsLimitsFixture(runID, fixtureCandidateCommit, time.Now().UTC())
	summary, err := validateCloudEventsLimitsEvidence(evidence, runID, probes)
	if err != nil {
		t.Fatal(err)
	}
	if summary.CountEvents != 501 || summary.ByteEvents != 7 ||
		summary.AcceptedRequests != 4 || summary.OversizedRecoveries != 1 {
		t.Fatalf("unexpected CloudEvents limits summary: %#v", summary)
	}
}

func TestValidateCloudEventsLimitsEvidenceRejectsInvalidProof(t *testing.T) {
	runID := "candidate-limits-2026-b"
	probes := map[string]probeEvidence{
		"cloudevents-https": {CandidateCommit: fixtureCandidateCommit},
		"otlp-http":         {CandidateCommit: fixtureCandidateCommit},
		"otlp-grpc":         {CandidateCommit: fixtureCandidateCommit},
	}
	tests := []struct {
		name   string
		mutate func(*cloudEventsLimitsEvidence)
	}{
		{
			name: "wrong candidate",
			mutate: func(e *cloudEventsLimitsEvidence) {
				e.CandidateCommit = strings.Repeat("b", 40)
			},
		},
		{
			name: "count request too large",
			mutate: func(e *cloudEventsLimitsEvidence) {
				e.Scenarios[0].Requests[0].Events = append(
					e.Scenarios[0].Requests[0].Events,
					e.Scenarios[0].Requests[1].Events[0],
				)
			},
		},
		{
			name: "byte request exceeds limit",
			mutate: func(e *cloudEventsLimitsEvidence) {
				e.Scenarios[1].Requests[0].EncodedBytes = cloudEventsLimitMaxRequestBytes + 1
			},
		},
		{
			name: "oversized webhook attempt",
			mutate: func(e *cloudEventsLimitsEvidence) {
				e.Oversized.WebhookAttempts = 1
			},
		},
		{
			name: "recovery identity changed",
			mutate: func(e *cloudEventsLimitsEvidence) {
				e.Oversized.RecoveryID = "sha256:" + strings.Repeat("0", 64)
			},
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			evidence := cloudEventsLimitsFixture(runID, fixtureCandidateCommit, time.Now().UTC())
			test.mutate(&evidence)
			if _, err := validateCloudEventsLimitsEvidence(evidence, runID, probes); err == nil {
				t.Fatal("CloudEvents limits validation accepted invalid evidence")
			}
		})
	}
}
