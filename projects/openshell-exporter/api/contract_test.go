// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package api_test

import (
	"encoding/json"
	"os"
	"regexp"
	"strings"
	"testing"

	"github.com/santhosh-tekuri/jsonschema/v6"
)

var eventIDPattern = regexp.MustCompile(`^sha256:[0-9a-f]{64}$`)

func TestCheckedEnvelopeSchemaAndGoldenCloudEvents(t *testing.T) {
	schema, err := os.ReadFile("schemas/event-envelope-v1.schema.json")
	if err != nil {
		t.Fatal(err)
	}
	var schemaDocument map[string]any
	if err := json.Unmarshal(schema, &schemaDocument); err != nil {
		t.Fatalf("schema JSON: %v", err)
	}
	if schemaDocument["$id"] != "urn:openshell:event-envelope:1" {
		t.Fatalf("schema id=%#v", schemaDocument["$id"])
	}

	compiler := jsonschema.NewCompiler()
	if err := compiler.AddResource(
		"urn:openshell:event-envelope:1",
		schemaDocument,
	); err != nil {
		t.Fatal(err)
	}
	compiled, err := compiler.Compile("urn:openshell:event-envelope:1")
	if err != nil {
		t.Fatal(err)
	}

	for _, path := range []string{
		"examples/ocsf-network-denial.json",
		"examples/ocsf-ai-inference.json",
		"examples/sandbox-lifecycle.json",
		"examples/nemo-relay-log.json",
		"examples/policy-draft-snapshot.json",
		"examples/policy-draft-chunk.json",
		"examples/policy-draft-history.json",
		"examples/policy-status.json",
		"examples/policy-revision.json",
		"examples/policy-reconciliation-warning.json",
	} {
		t.Run(path, func(t *testing.T) {
			encoded, err := os.ReadFile(path)
			if err != nil {
				t.Fatal(err)
			}
			var event map[string]any
			if err := json.Unmarshal(encoded, &event); err != nil {
				t.Fatalf("CloudEvent JSON: %v", err)
			}
			assertCloudEventV1(t, event)
			assertCanonicalCorrelation(t, event)
			if err := compiled.Validate(event["data"]); err != nil {
				t.Fatalf("validate envelope schema: %v", err)
			}
		})
	}
}

func assertCloudEventV1(t *testing.T, event map[string]any) {
	t.Helper()
	for key, expected := range map[string]string{
		"specversion":     "1.0",
		"dataschema":      "urn:openshell:event-envelope:1",
		"datacontenttype": "application/json",
	} {
		if event[key] != expected {
			t.Fatalf("%s=%#v", key, event[key])
		}
	}
	id, ok := event["id"].(string)
	if !ok || !eventIDPattern.MatchString(id) {
		t.Fatalf("id=%#v", event["id"])
	}
	for _, key := range []string{"source", "type", "subject"} {
		value, ok := event[key].(string)
		if !ok || value == "" {
			t.Fatalf("%s=%#v", key, event[key])
		}
	}
	data, ok := event["data"].(map[string]any)
	if !ok || data["schema_version"] != "1.0" {
		t.Fatalf("data=%#v", event["data"])
	}
}

func assertCanonicalCorrelation(t *testing.T, event map[string]any) {
	t.Helper()
	data, ok := event["data"].(map[string]any)
	if !ok {
		t.Fatalf("data=%#v", event["data"])
	}
	openshell, ok := data["openshell"].(map[string]any)
	if !ok {
		t.Fatalf("openshell=%#v", data["openshell"])
	}
	correlation, ok := data["correlation"].(map[string]any)
	if !ok {
		t.Fatalf("correlation=%#v", data["correlation"])
	}
	for source, canonical := range map[string]string{
		"gateway_id":     "openshell.gateway.id",
		"workspace":      "openshell.workspace",
		"sandbox_id":     "openshell.sandbox.id",
		"policy_version": "openshell.policy.version",
	} {
		expected, exists := openshell[source]
		if !exists {
			continue
		}
		if correlation[canonical] != expected {
			t.Fatalf("correlation[%q]=%#v, want %#v", canonical, correlation[canonical], expected)
		}
	}
	if expected, exists := correlation["agent_session_id"]; exists {
		if correlation["agent.session.id"] != expected {
			t.Fatalf(
				"correlation[agent.session.id]=%#v, want %#v",
				correlation["agent.session.id"],
				expected,
			)
		}
	}
}

func TestCorrelationContextSchema(t *testing.T) {
	encoded, err := os.ReadFile("schemas/correlation-context-v1.schema.json")
	if err != nil {
		t.Fatal(err)
	}
	var document map[string]any
	if err := json.Unmarshal(encoded, &document); err != nil {
		t.Fatal(err)
	}
	compiler := jsonschema.NewCompiler()
	if err := compiler.AddResource("urn:openshell:correlation-context:1", document); err != nil {
		t.Fatal(err)
	}
	compiled, err := compiler.Compile("urn:openshell:correlation-context:1")
	if err != nil {
		t.Fatal(err)
	}
	if err := compiled.Validate(map[string]any{
		"openshell.gateway.id": "gateway-1",
		"openshell.workspace":  "default",
		"openshell.sandbox.id": "sandbox-1",
		"agent.session.id":     "session-1",
		"trace_id":             "00112233445566778899aabbccddeeff",
		"tool_call_id":         "call-1",
	}); err != nil {
		t.Fatal(err)
	}
	for name, invalid := range map[string]map[string]any{
		"content-bearing request": {"request_id": "prompt content must not pass"},
		"uppercase trace":         {"trace_id": "00112233445566778899AABBCCDDEEFF"},
		"negative policy":         {"openshell.policy.version": -1},
		"oversized session":       {"agent.session.id": strings.Repeat("a", 257)},
	} {
		t.Run(name, func(t *testing.T) {
			if err := compiled.Validate(invalid); err == nil {
				t.Fatal("expected unsafe canonical identity value to fail schema validation")
			}
		})
	}
}

func TestPolicyGoldenCloudEventsPreserveContract(t *testing.T) {
	t.Parallel()

	specs := []struct {
		path             string
		kind             string
		id               string
		eventType        string
		operation        string
		trigger          string
		consistency      string
		policyFields     []string
		hasSourcePayload bool
	}{
		{"examples/policy-draft-snapshot.json", "policy.draft.snapshot", "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc", "com.nvidia.openshell.policy.draft.snapshot.v1", "openshell.v1.OpenShell/GetDraftPolicy", "discovery", "not_compared", []string{"policy_revision"}, true},
		{"examples/policy-draft-chunk.json", "policy.draft.chunk", "sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd", "com.nvidia.openshell.policy.draft.chunk.v1", "openshell.v1.OpenShell/GetDraftPolicy", "watch_notification", "matched", []string{"policy_revision"}, true},
		{"examples/policy-draft-history.json", "policy.draft.history", "sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee", "com.nvidia.openshell.policy.draft.history.v1", "openshell.v1.OpenShell/GetDraftHistory", "periodic", "not_compared", nil, true},
		{"examples/policy-status.json", "policy.status", "sha256:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff", "com.nvidia.openshell.policy.status.v1", "openshell.v1.OpenShell/GetSandboxPolicyStatus", "reconnect", "not_compared", []string{"policy_version", "policy_revision"}, true},
		{"examples/policy-revision.json", "policy.revision", "sha256:1111111111111111111111111111111111111111111111111111111111111111", "com.nvidia.openshell.policy.revision.v1", "openshell.v1.OpenShell/ListSandboxPolicies", "periodic", "gateway_ahead", []string{"policy_version", "policy_revision"}, true},
		{"examples/policy-reconciliation-warning.json", "policy.reconciliation.warning", "sha256:2222222222222222222222222222222222222222222222222222222222222222", "com.nvidia.openshell.policy.reconciliation.warning.v1", "openshell.v1.OpenShell/GetDraftPolicy", "periodic", "unavailable", nil, false},
	}

	for _, spec := range specs {
		t.Run(spec.kind, func(t *testing.T) {
			t.Parallel()
			event, encoded := readGoldenCloudEvent(t, spec.path)
			if event["id"] != spec.id {
				t.Fatalf("id=%#v, want %q", event["id"], spec.id)
			}
			if event["type"] != spec.eventType {
				t.Fatalf("type=%#v, want %q", event["type"], spec.eventType)
			}
			if !strings.HasSuffix(event["source"].(string), "/sources/"+spec.kind) {
				t.Fatalf("source=%#v, want kind %q", event["source"], spec.kind)
			}

			data := requiredMap(t, event, "data")
			acquisition := requiredMap(t, data, "acquisition")
			for key, expected := range map[string]any{
				"kind":                   spec.kind,
				"api_operation":          spec.operation,
				"reconciliation_trigger": spec.trigger,
				"consistency":            spec.consistency,
			} {
				if acquisition[key] != expected {
					t.Fatalf("acquisition[%q]=%#v, want %#v", key, acquisition[key], expected)
				}
			}

			openshell := requiredMap(t, data, "openshell")
			for _, key := range spec.policyFields {
				if _, ok := openshell[key]; !ok {
					t.Fatalf("openshell missing %q", key)
				}
			}
			security := requiredMap(t, data, "security")
			if requiredMap(t, security, "validation")["status"] != "not_applicable" {
				t.Fatal("policy example must declare validation not_applicable")
			}
			redaction := requiredMap(t, security, "redaction")
			for _, key := range []string{"profile_id", "profile_version", "applied", "count"} {
				if _, ok := redaction[key]; !ok {
					t.Fatalf("redaction missing %q", key)
				}
			}

			original := requiredMap(t, data, "original")
			if spec.hasSourcePayload {
				payload := requiredMap(t, original, "source_payload")
				if payload["future_policy_field"] != "preserved" {
					t.Fatalf("unknown source field was not preserved: %#v", payload)
				}
			} else if original["future_policy_field"] != "preserved" {
				t.Fatalf("unknown diagnostic field was not preserved: %#v", original)
			}

			var decoded any
			if err := json.Unmarshal(encoded, &decoded); err != nil {
				t.Fatal(err)
			}
			assertNoSensitivePolicyKeys(t, decoded)
		})
	}
}

func TestPolicyGoldenCorrelationExamples(t *testing.T) {
	t.Parallel()

	denial, _ := readGoldenCloudEvent(t, "examples/ocsf-network-denial.json")
	chunk, _ := readGoldenCloudEvent(t, "examples/policy-draft-chunk.json")
	denialCorrelation := requiredMap(t, requiredMap(t, denial, "data"), "correlation")
	chunkData := requiredMap(t, chunk, "data")
	chunkCorrelation := requiredMap(t, chunkData, "correlation")
	if denialCorrelation["request_id"] != chunkCorrelation["request_id"] {
		t.Fatalf("direct denial-to-draft request ID mismatch: denial=%#v chunk=%#v", denialCorrelation, chunkCorrelation)
	}
	join := requiredMap(t, requiredMap(t, chunkData, "original"), "example_join")
	if join["method"] != "direct_identifier" || join["causal_proof"] != false {
		t.Fatalf("direct join metadata=%#v", join)
	}

	status, _ := readGoldenCloudEvent(t, "examples/policy-status.json")
	statusData := requiredMap(t, status, "data")
	statusCorrelation := requiredMap(t, statusData, "correlation")
	for _, key := range []string{"trace_id", "request_id", "session_id", "agent.session.id", "tool_call_id", "policy_chunk_id"} {
		if _, exists := statusCorrelation[key]; exists {
			t.Fatalf("temporal-only example unexpectedly has direct identifier %q", key)
		}
	}
	temporal := requiredMap(t, requiredMap(t, statusData, "original"), "example_join")
	if temporal["method"] != "temporal_context" || temporal["causal_proof"] != false {
		t.Fatalf("temporal join metadata=%#v", temporal)
	}
}

func readGoldenCloudEvent(t *testing.T, path string) (map[string]any, []byte) {
	t.Helper()
	encoded, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var event map[string]any
	if err := json.Unmarshal(encoded, &event); err != nil {
		t.Fatalf("CloudEvent JSON: %v", err)
	}
	return event, encoded
}

func requiredMap(t *testing.T, parent map[string]any, key string) map[string]any {
	t.Helper()
	value, ok := parent[key].(map[string]any)
	if !ok {
		t.Fatalf("%s=%#v, want object", key, parent[key])
	}
	return value
}

func assertNoSensitivePolicyKeys(t *testing.T, value any) {
	t.Helper()
	forbidden := map[string]struct{}{
		"candidate_effective_policy": {},
		"credential":                 {},
		"current_effective_policy":   {},
		"effective_policy":           {},
		"prompt":                     {},
		"response":                   {},
		"review_token":               {},
	}
	var walk func(any)
	walk = func(current any) {
		switch typed := current.(type) {
		case map[string]any:
			for key, child := range typed {
				if _, blocked := forbidden[strings.ToLower(key)]; blocked {
					t.Fatalf("sensitive policy key %q reached golden example", key)
				}
				walk(child)
			}
		case []any:
			for _, child := range typed {
				walk(child)
			}
		}
	}
	walk(value)
}
