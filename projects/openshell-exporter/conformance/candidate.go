// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package conformance

import (
	"encoding/hex"
	"fmt"
	"testing"
)

// candidateCommit is injected into the conformance test binary by the pinned
// image build. An external gate must never rely on a runtime-supplied revision.
var candidateCommit string

func requiredCandidateCommit(t *testing.T) string {
	t.Helper()
	if err := validateCandidateCommit(candidateCommit); err != nil {
		t.Fatalf("conformance binary is not bound to a candidate commit: %v", err)
	}
	return candidateCommit
}

func validateCandidateCommit(value string) error {
	if len(value) != 40 {
		return fmt.Errorf("candidate commit must contain 40 lowercase hexadecimal characters")
	}
	decoded, err := hex.DecodeString(value)
	if err != nil || hex.EncodeToString(decoded) != value {
		return fmt.Errorf("candidate commit must contain 40 lowercase hexadecimal characters")
	}
	return nil
}

func requireMatchingCandidateCommit(value string, expected string, evidence string) error {
	if err := validateCandidateCommit(value); err != nil {
		return fmt.Errorf("%s: %w", evidence, err)
	}
	if value != expected {
		return fmt.Errorf("%s belongs to candidate %s, expected %s", evidence, value, expected)
	}
	return nil
}
