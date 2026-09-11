// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package integration

import (
	"encoding/json"
	"os"
	"strings"
	"testing"

	"github.com/santhosh-tekuri/jsonschema/v6"
)

func TestPolicyAPIQualificationSchemaCompiles(t *testing.T) {
	encoded, err := os.ReadFile("qualification/policy-api-report-v1.schema.json")
	if err != nil {
		t.Fatal(err)
	}
	var document map[string]any
	if err := json.Unmarshal(encoded, &document); err != nil {
		t.Fatal(err)
	}
	compiler := jsonschema.NewCompiler()
	identifier := document["$id"].(string)
	if err := compiler.AddResource(identifier, document); err != nil {
		t.Fatal(err)
	}
	if _, err := compiler.Compile(identifier); err != nil {
		t.Fatal(err)
	}
}

func TestPolicyAPIQualificationIsReadOnlyAndCandidateBound(t *testing.T) {
	encoded, err := os.ReadFile("qualification/qualify-policy-api.sh")
	if err != nil {
		t.Fatal(err)
	}
	script := string(encoded)
	for _, required := range []string{
		`git status --porcelain`,
		`git rev-parse HEAD`,
		`POLICY_QUALIFICATION_GATEWAY_IMAGE`,
		`POLICY_QUALIFICATION_UNAUTHORIZED_TOKEN_FILE`,
		`--require-complete`,
		`POLICY_QUALIFICATION_EVIDENCE_DIR must have mode 0700`,
	} {
		if !strings.Contains(script, required) {
			t.Fatalf("policy qualification is missing %q", required)
		}
	}
	for _, forbidden := range []string{
		"ApproveDraftChunk", "RejectDraftChunk", "EditDraftChunk", "UndoDraftDecision",
		"ClearDraftPolicy", "Apply", "CreateSandboxPolicy", "UpdateSandboxPolicy", "DeleteSandboxPolicy",
		"rm -rf", "docker restart", "kubectl delete",
	} {
		if strings.Contains(script, forbidden) {
			t.Fatalf("policy qualification contains forbidden mutation %q", forbidden)
		}
	}
}
