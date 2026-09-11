// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package conformance

import (
	"strings"
	"testing"
)

const fixtureCandidateCommit = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

func TestValidateCandidateCommit(t *testing.T) {
	if err := validateCandidateCommit(fixtureCandidateCommit); err != nil {
		t.Fatal(err)
	}
	for _, invalid := range []string{
		"",
		strings.Repeat("a", 39),
		strings.Repeat("a", 41),
		strings.Repeat("A", 40),
		strings.Repeat("z", 40),
	} {
		if err := validateCandidateCommit(invalid); err == nil {
			t.Fatalf("accepted invalid candidate commit %q", invalid)
		}
	}
}

func TestRequireMatchingCandidateCommit(t *testing.T) {
	if err := requireMatchingCandidateCommit(fixtureCandidateCommit, fixtureCandidateCommit, "fixture"); err != nil {
		t.Fatal(err)
	}
	if err := requireMatchingCandidateCommit(strings.Repeat("b", 40), fixtureCandidateCommit, "fixture"); err == nil {
		t.Fatal("accepted evidence from a different candidate")
	}
}

func TestConformanceBinaryCandidateBinding(t *testing.T) {
	if candidateCommit == "" {
		t.Skip("candidate commit is injected only into the conformance image binary")
	}
	if err := validateCandidateCommit(candidateCommit); err != nil {
		t.Fatal(err)
	}
}
