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

func TestOCSFFileLifecycleQualificationSchemaCompiles(t *testing.T) {
	encoded, err := os.ReadFile("qualification/ocsf-file-lifecycle-report-v1.schema.json")
	if err != nil {
		t.Fatal(err)
	}
	var document map[string]any
	if err := json.Unmarshal(encoded, &document); err != nil {
		t.Fatal(err)
	}
	compiler := jsonschema.NewCompiler()
	identifier, ok := document["$id"].(string)
	if !ok || identifier == "" {
		t.Fatal("qualification schema has no $id")
	}
	if err := compiler.AddResource(identifier, document); err != nil {
		t.Fatal(err)
	}
	if _, err := compiler.Compile(identifier); err != nil {
		t.Fatal(err)
	}
}

func TestOCSFFileLifecycleQualificationIsCandidateBoundAndNatural(t *testing.T) {
	encoded, err := os.ReadFile("qualification/qualify-ocsf-file-lifecycle.sh")
	if err != nil {
		t.Fatal(err)
	}
	script := string(encoded)
	for _, required := range []string{
		`git status --porcelain`,
		`git rev-parse HEAD`,
		`@sha256:[0-9a-f]{64}`,
		`openshell-ocsf.*.log`,
		`source-before.json`,
		`source-after.json`,
		`OCSF_LIFECYCLE_DESTINATION_OUTAGE_HOOK`,
		`[ "$status" -eq 503 ]`,
		`otelcol_exporter_queue_size`,
		`openshell_exporter_delivery_retryable_failures`,
		`openshell_exporter_delivery_events`,
		`storage-before-restart.json`,
		`storage-after-restart.json`,
		`[ "$instance_after" != "$instance_before" ]`,
		`findmnt --noheadings --output OPTIONS --target "$SOURCE_DIR"`,
		`--require-complete`,
		`Wait for OpenShell to perform its natural daily rotation.`,
		`Do not rename, truncate, copy, or rewrite any OCSF source file.`,
		`OCSF_LIFECYCLE_EVIDENCE_DIR must have mode 0700`,
	} {
		if !strings.Contains(script, required) {
			t.Fatalf("OCSF file lifecycle qualification is missing %q", required)
		}
	}
	for _, forbidden := range []string{
		"eval ",
		"docker restart",
		"podman restart",
		"kubectl delete",
		"rm -rf",
		"> \"$SOURCE_DIR",
		">\"$SOURCE_DIR",
		"mv \"$SOURCE_DIR",
		"truncate ",
	} {
		if strings.Contains(script, forbidden) {
			t.Fatalf("qualification contains forbidden source/lifecycle mutation %q", forbidden)
		}
	}
}
