// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package conformance

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

func writeCloudEventsLimitsFixture(
	t *testing.T,
	directory string,
	runID string,
	candidateCommit string,
	queriedAt time.Time,
) {
	t.Helper()
	writeJSONFixture(
		t,
		filepath.Join(directory, cloudEventsLimitsFile),
		cloudEventsLimitsFixture(runID, candidateCommit, queriedAt),
	)
}

func TestVerifyExternalBackendEvidenceRejectsMissingCloudEventsLimitsProof(t *testing.T) {
	runID, reportDirectory, backendDirectory := validBackendEvidenceFixture(t)
	if err := os.Remove(filepath.Join(backendDirectory, cloudEventsLimitsFile)); err != nil {
		t.Fatal(err)
	}
	if _, err := verifyExternalBackendEvidence(reportDirectory, backendDirectory, runID); err == nil {
		t.Fatal("verification accepted missing CloudEvents limits evidence")
	}
}

func TestVerifyExternalBackendEvidenceRejectsInvalidCloudEventsLimitsProof(t *testing.T) {
	runID, reportDirectory, backendDirectory := validBackendEvidenceFixture(t)
	mutateJSONFixture(
		t,
		filepath.Join(backendDirectory, cloudEventsLimitsFile),
		func(document map[string]any) {
			document["oversized"].(map[string]any)["webhook_attempts"] = float64(1)
		},
	)
	if _, err := verifyExternalBackendEvidence(reportDirectory, backendDirectory, runID); err == nil {
		t.Fatal("verification accepted invalid CloudEvents limits evidence")
	}
}
