// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package openshellprocessor

import (
	"context"
	"strings"
	"testing"

	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/plog"
	"go.uber.org/zap"
)

func TestConfigRejectsUnknownOrEmptySourceProfiles(t *testing.T) {
	config := createDefaultConfig().(*Config)
	config.SourceProfiles = nil
	if err := config.Validate(); err == nil {
		t.Fatal("empty source profiles should fail")
	}
	config.SourceProfiles = []string{"arbitrary.host.logs"}
	if err := config.Validate(); err == nil {
		t.Fatal("unknown source profile should fail")
	}
}

func TestSourceCapabilitySetsDistinguishDisabledAndUnobserved(t *testing.T) {
	disabled, declaredUnobserved := sourceCapabilitySets([]string{"openshell.log"})
	if strings.Join(disabled, ",") !=
		"ocsf.file,ocsf.forwarded,openshell.log.forwarded,watchsandbox,policy.reconciliation,kubernetes.context,nemo_relay.log,nemo_relay.trace,openshell.trace" {
		t.Fatalf("disabled profiles=%v", disabled)
	}
	if len(declaredUnobserved) != 0 {
		t.Fatalf("non-WatchSandbox profile reported stream variants: %v", declaredUnobserved)
	}

	disabled, declaredUnobserved = sourceCapabilitySets([]string{"watchsandbox"})
	if strings.Contains(strings.Join(disabled, ","), "watchsandbox") {
		t.Fatalf("enabled WatchSandbox was reported disabled: %v", disabled)
	}
	if strings.Join(declaredUnobserved, ",") !=
		"policy.draft_updated,stream.warning" {
		t.Fatalf("declared unobserved variants=%v", declaredUnobserved)
	}
}

func TestUnavailableCapabilitiesIncludeUpstreamGatewayOCSFGate(t *testing.T) {
	if strings.Join(unavailableSourceCapabilities, ",") !=
		"gateway_structured_ocsf_export,watch_events" {
		t.Fatalf("unavailable capabilities=%v", unavailableSourceCapabilities)
	}
}

func TestEnvelopeRedactionValidationAndStableIdentity(t *testing.T) {
	config := createDefaultConfig().(*Config)
	processor, err := newProcessor(config, zap.NewNop())
	if err != nil {
		t.Fatal(err)
	}
	original := validOCSF()
	original["authorization"] = "Bearer secret-value"
	original["tokens_in"] = int64(42)
	original["unknown_extension"] = map[string]any{"keep": true}
	logs := testLogs(t, original)
	record := onlyRecord(logs)
	record.Attributes().PutStr("log.file.path", "/var/log/openshell-ocsf.log")
	record.Attributes().PutInt("log.file.record_offset", 128)
	record.Attributes().PutInt("openshell.policy.version", 17)
	if _, err := processor.processLogs(context.Background(), logs); err != nil {
		t.Fatal(err)
	}

	firstID, _ := record.Attributes().Get("event.id")
	if !strings.HasPrefix(firstID.Str(), "sha256:") || len(firstID.Str()) != 71 {
		t.Fatalf("unexpected id %q", firstID.Str())
	}
	envelope := record.Body().Map()
	originalValue, _ := envelope.Get("original")
	redactedValue, _ := originalValue.Map().Get("authorization")
	if redactedValue.Str() != redacted {
		t.Fatalf("authorization=%q", redactedValue.Str())
	}
	tokens, _ := originalValue.Map().Get("tokens_in")
	if tokens.Int() != 42 {
		t.Fatalf("tokens_in=%d", tokens.Int())
	}
	if _, ok := originalValue.Map().Get("unknown_extension"); !ok {
		t.Fatal("unknown source field was lost")
	}
	security, _ := envelope.Get("security")
	validation, _ := security.Map().Get("validation")
	status, _ := validation.Map().Get("status")
	if status.Str() != validationValid {
		t.Fatalf("validation status=%q", status.Str())
	}
	correlationValue, _ := envelope.Get("correlation")
	correlation := correlationValue.Map()
	for key, expected := range map[string]string{
		"openshell.gateway.id": config.GatewayID,
		"openshell.workspace":  config.Workspace,
		"openshell.sandbox.id": sandboxIDFrom(original, config.DefaultSandboxID),
	} {
		value, ok := correlation.Get(key)
		if !ok {
			t.Fatalf("canonical correlation field %q is missing", key)
		}
		if value.Str() != expected {
			t.Fatalf("correlation[%q]=%#v, want %q", key, value.AsRaw(), expected)
		}
	}
	policyVersion, ok := correlation.Get("openshell.policy.version")
	if !ok {
		t.Fatal("canonical policy version is missing")
	}
	if policyVersion.Int() != 17 {
		t.Fatalf("canonical policy version=%#v, want 17", policyVersion.AsRaw())
	}
	if record.Timestamp() == 0 {
		t.Fatal("OCSF source time was not set")
	}

	second := testLogs(t, original)
	secondRecord := onlyRecord(second)
	secondRecord.Attributes().PutStr("log.file.path", "/var/log/openshell-ocsf.log")
	secondRecord.Attributes().PutInt("log.file.record_offset", 128)
	secondRecord.Attributes().PutInt("openshell.policy.version", 18)
	otherConfig := *config
	otherConfig.Redaction = config.Redaction
	otherConfig.Redaction.Patterns = []string{"secret-value"}
	other, err := newProcessor(&otherConfig, zap.NewNop())
	if err != nil {
		t.Fatal(err)
	}
	if _, err := other.processLogs(context.Background(), second); err != nil {
		t.Fatal(err)
	}
	secondID, _ := secondRecord.Attributes().Get("event.id")
	if firstID.Str() != secondID.Str() {
		t.Fatalf("redaction or additive correlation changed identity: %q != %q", firstID.Str(), secondID.Str())
	}
}

func TestSandboxOperationalFileIsCheckpointedRedactedEvidence(t *testing.T) {
	config := createDefaultConfig().(*Config)
	config.SourceProfiles = []string{"openshell.log"}
	config.SourceInstance = "sandbox-operational-log"
	processor, err := newProcessor(config, zap.NewNop())
	if err != nil {
		t.Fatal(err)
	}
	logs := testLogs(t, "2026-08-21T12:00:00Z INFO openshell_sandbox: authorization=Bearer source-secret")
	record := onlyRecord(logs)
	record.Attributes().PutStr("openshell.acquisition.kind", "sandbox.file_log")
	record.Attributes().PutStr(
		"openshell.acquisition.source_instance",
		"sandbox-operational-log",
	)
	record.Attributes().PutStr("log.file.path", "/var/log/openshell.2026-08-21.log")
	record.Attributes().PutStr(
		"log.file.path_resolved",
		"/var/log/openshell.2026-08-21.log",
	)
	record.Attributes().PutInt("log.file.record_offset", 128)
	record.Attributes().PutInt("log.file.record_number", 4)
	if _, err := processor.processLogs(context.Background(), logs); err != nil {
		t.Fatal(err)
	}
	eventType, _ := record.Attributes().Get("cloudevents.type")
	if eventType.Str() != "com.nvidia.openshell.sandbox.file_log.v1" {
		t.Fatalf("type=%q", eventType.Str())
	}
	envelope := record.Body().Map().AsRaw()
	original := envelope["original"].(string)
	if strings.Contains(original, "source-secret") || !strings.Contains(original, redacted) {
		t.Fatalf("sandbox operational log was not redacted: %q", original)
	}
	acquisition := envelope["acquisition"].(map[string]any)
	if acquisition["durability"] != "checkpointed_file" ||
		acquisition["transport"] != "file" ||
		acquisition["payload_type"] != "source_record" {
		t.Fatalf("acquisition=%#v", acquisition)
	}
	coverage := acquisition["coverage"].(map[string]any)
	if coverage["source_profile"] != "openshell.log" ||
		coverage["payload_preservation"] != "redacted_source_record" {
		t.Fatalf("coverage=%#v", coverage)
	}
	security := envelope["security"].(map[string]any)
	validation := security["validation"].(map[string]any)
	if validation["status"] != validationNotApplicable {
		t.Fatalf("validation=%#v", validation)
	}
}

func TestCheckpointedFilePromotesSandboxNameFromQualifiedPVCLayout(t *testing.T) {
	config := createDefaultConfig().(*Config)
	processor, err := newProcessor(config, zap.NewNop())
	if err != nil {
		t.Fatal(err)
	}
	logs := testLogs(t, validOCSF())
	record := onlyRecord(logs)
	record.Attributes().PutStr(
		"log.file.path_resolved",
		"/var/log/openshell/sandboxes/agent-primary/openshell-ocsf.2026-08-23.log",
	)
	processor.normalize(context.Background(), record)

	name, ok := record.Attributes().Get("openshell.sandbox.name")
	if !ok || name.Str() != "agent-primary" {
		t.Fatalf("sandbox name attribute=%v, present=%t", name.AsRaw(), ok)
	}
	envelope := record.Body().AsRaw().(map[string]any)
	openshell := envelope["openshell"].(map[string]any)
	if openshell["sandbox_name"] != "agent-primary" || openshell["sandbox_id"] != "sandbox-1" {
		t.Fatalf("openshell context=%#v", openshell)
	}
}

func TestSandboxNameFromEvidencePathRejectsUnqualifiedSegments(t *testing.T) {
	for _, path := range []string{
		"/var/log/openshell/openshell-ocsf.log",
		"/var/log/openshell/sandboxes/../openshell-ocsf.log",
		"/var/log/openshell/sandboxes/Agent_Primary/openshell-ocsf.log",
	} {
		if name := sandboxNameFromEvidencePath(path); name != "" {
			t.Fatalf("path %q produced sandbox name %q", path, name)
		}
	}
}

func TestFileIdentityGoldenForQualificationReconciliation(t *testing.T) {
	config := createDefaultConfig().(*Config)
	config.GatewayID = "gateway-a"
	config.Workspace = "default"
	config.SourceInstance = "openshell-ocsf"
	record := onlyRecord(plog.NewLogs())
	record.Attributes().PutStr("log.file.path", "/var/log/openshell/openshell-ocsf.jsonl")
	record.Attributes().PutStr("log.file.path_resolved", "/var/log/openshell/openshell-ocsf.jsonl")
	record.Attributes().PutInt("log.file.record_offset", 0)
	record.Attributes().PutInt("log.file.record_number", 1)

	const expected = "sha256:28a1b7abca7378184dba5dbc629107951088c88a23a8b6d99cc43d48e54014db"
	if identifier := stableID(config, record, "ocsf.file", "body-gateway-a-1"); identifier != expected {
		t.Fatalf("production file identity = %q, want qualification golden %q", identifier, expected)
	}
}

func TestPublicPayloadEnrichmentDoesNotRedefineStreamIdentity(t *testing.T) {
	config := createDefaultConfig().(*Config)
	logs := testLogs(t, map[string]any{"message": "sandbox ready"})
	record := onlyRecord(logs)
	record.Attributes().PutStr("openshell.sandbox.id", "sandbox-a")
	record.Attributes().PutInt("openshell.resource.version", 42)
	record.Attributes().PutStr("openshell.event.kind", "sandbox.lifecycle")

	first := map[string]any{
		"event_type":     "updated",
		"sandbox":        map[string]any{"resource_version": int64(42)},
		"source_payload": map[string]any{"future_field": "first"},
	}
	second := map[string]any{
		"event_type":     "updated",
		"sandbox":        map[string]any{"resource_version": int64(42)},
		"source_payload": map[string]any{"future_field": "second", "more": true},
	}
	firstID := stableID(config, record, "sandbox.lifecycle", first)
	secondID := stableID(config, record, "sandbox.lifecycle", second)
	if firstID != secondID {
		t.Fatalf("public payload enrichment changed stable identity: %q != %q", firstID, secondID)
	}
	second["event_type"] = "deleted"
	if changed := stableID(config, record, "sandbox.lifecycle", second); changed == firstID {
		t.Fatal("compatibility identity fields did not change stable identity")
	}
}

func TestMalformedStringIsRetainedInvalidAndRedacted(t *testing.T) {
	processor, err := newProcessor(createDefaultConfig().(*Config), zap.NewNop())
	if err != nil {
		t.Fatal(err)
	}
	logs := testLogs(t, "authorization=Bearer abc.def.ghi")
	record := onlyRecord(logs)
	record.Attributes().PutStr("openshell.acquisition.kind", "ocsf.file")
	if _, err := processor.processLogs(context.Background(), logs); err != nil {
		t.Fatal(err)
	}
	if logs.LogRecordCount() != 1 {
		t.Fatal("malformed evidence was dropped")
	}
	original, _ := record.Body().Map().Get("original")
	if strings.Contains(original.Str(), "abc.def.ghi") {
		t.Fatalf("malformed string was not redacted: %q", original.Str())
	}
	valid, _ := record.Attributes().Get("openshell.ocsf.valid")
	if valid.Bool() {
		t.Fatal("malformed OCSF marked valid")
	}
}

func TestStructuralValidationAcceptsExactJSONNumbers(t *testing.T) {
	event := validOCSF()
	event["class_uid"] = float64(4001)
	event["category_uid"] = float64(4)
	event["activity_id"] = float64(1)
	event["type_uid"] = float64(400101)
	event["severity_id"] = float64(4)
	event["time"] = float64(1_700_000_000_000)
	result := validateSource("ocsf.file", event)
	if result.Status != validationValid {
		t.Fatalf("result=%#v", result)
	}
}

func TestStructuralValidationAcceptsCurrentAndPreviousOCSFVersions(t *testing.T) {
	for _, version := range []string{"1.7.0", currentOCSFVersion} {
		event := validOCSF()
		event["metadata"].(map[string]any)["version"] = version
		result := validateSource("ocsf.file", event)
		if result.Status != validationValid {
			t.Fatalf("version %s: result=%#v", version, result)
		}
		identifiers := ocsfIdentifiers(event)
		if identifiers["version"] != version {
			t.Fatalf("version %s: identifiers=%#v", version, identifiers)
		}
	}
	event := validOCSF()
	event["metadata"].(map[string]any)["version"] = "1.9.0"
	result := validateSource("ocsf.file", event)
	if result.Status != validationInvalid {
		t.Fatalf("unsupported version result=%#v", result)
	}
}

func TestStructuralValidationReportsAllErrors(t *testing.T) {
	result := validateSource("ocsf.file", map[string]any{
		"class_uid":   4001.5,
		"severity_id": "high",
		"metadata":    map[string]any{"version": "1.6.0"},
	})
	if result.Status != validationInvalid || len(result.Errors) < 6 {
		t.Fatalf("result=%#v", result)
	}
}

func TestLifecycleHasNoSyntheticSourceTime(t *testing.T) {
	processor, err := newProcessor(createDefaultConfig().(*Config), zap.NewNop())
	if err != nil {
		t.Fatal(err)
	}
	logs := testLogs(t, map[string]any{
		"event_type": "MODIFIED",
		"sandbox": map[string]any{
			"id":               "sandbox-1",
			"resource_version": int64(9),
		},
	})
	record := onlyRecord(logs)
	record.Attributes().PutStr("openshell.acquisition.kind", "sandbox.lifecycle")
	record.Attributes().PutStr("openshell.sandbox.id", "sandbox-1")
	if _, err := processor.processLogs(context.Background(), logs); err != nil {
		t.Fatal(err)
	}
	if record.Timestamp() != 0 {
		t.Fatal("lifecycle event without source time received a synthetic time")
	}
	eventType, _ := record.Attributes().Get("cloudevents.type")
	if eventType.Str() != "com.nvidia.openshell.sandbox.lifecycle.v1" {
		t.Fatalf("type=%q", eventType.Str())
	}
}

func TestOCSF18AIInferencePreservesSourceVersionAndUnknownFields(t *testing.T) {
	processor, err := newProcessor(createDefaultConfig().(*Config), zap.NewNop())
	if err != nil {
		t.Fatal(err)
	}
	logs := testLogs(t, map[string]any{
		"activity_id":  int64(99),
		"category_uid": int64(6),
		"class_uid":    int64(6003),
		"time":         int64(1_700_000_000_000),
		"type_uid":     int64(600399),
		"metadata": map[string]any{
			"version":  currentOCSFVersion,
			"profiles": []any{"container", "host", "ai_operation"},
		},
		"api": map[string]any{"operation": "chat.completions"},
		"ai_model": map[string]any{
			"name":     "nvidia/nemotron-3-super",
			"provider": "NVIDIA",
		},
		"unmapped": map[string]any{
			"latency_ms":   int64(42),
			"input_tokens": int64(128),
		},
	})
	record := onlyRecord(logs)
	record.Attributes().PutStr("openshell.acquisition.kind", "ocsf.file")
	if _, err := processor.processLogs(context.Background(), logs); err != nil {
		t.Fatal(err)
	}
	eventType, _ := record.Attributes().Get("cloudevents.type")
	if eventType.Str() != "com.nvidia.openshell.ocsf.6003.v1" {
		t.Fatalf("type=%q", eventType.Str())
	}
	envelope := record.Body().Map()
	security, _ := envelope.Get("security")
	ocsf, _ := security.Map().Get("ocsf")
	version, _ := ocsf.Map().Get("version")
	if version.Str() != currentOCSFVersion {
		t.Fatalf("security.ocsf.version=%q", version.Str())
	}
	validation, _ := security.Map().Get("validation")
	status, _ := validation.Map().Get("status")
	if status.Str() != validationValid {
		t.Fatalf("validation status=%q", status.Str())
	}
	original, _ := envelope.Get("original")
	metadata, _ := original.Map().Get("metadata")
	profiles, _ := metadata.Map().Get("profiles")
	if profiles.Slice().Len() != 3 ||
		profiles.Slice().At(2).Str() != "ai_operation" {
		t.Fatalf("profiles=%#v", profiles.AsRaw())
	}
	if _, ok := original.Map().Get("ai_model"); !ok {
		t.Fatal("AI model fields were lost")
	}
}

func TestWatchSandboxEnvelopePreservesMaximumAuthorizedContext(t *testing.T) {
	config := createDefaultConfig().(*Config)
	config.SourceProfiles = []string{"watchsandbox"}
	processor, err := newProcessor(config, zap.NewNop())
	if err != nil {
		t.Fatal(err)
	}
	logs := testLogs(t, map[string]any{
		"event_type": "MODIFIED",
		"sandbox": map[string]any{
			"id":               "sandbox-1",
			"resource_version": int64(11),
		},
		"fields": map[string]any{
			"trace.id":   "trace-from-fields",
			"span.id":    "span-from-fields",
			"request.id": "request-from-fields",
		},
		"source_payload": map[string]any{
			"metadata": map[string]any{
				"annotations": map[string]any{
					"session.id":    "session-from-annotation",
					"invocation.id": "invocation-from-annotation",
				},
			},
			"spec": map[string]any{
				"environment": map[string]any{
					"authorization": "Bearer source-secret",
				},
				"future_public_field": map[string]any{"preserve": true},
			},
			"status": map[string]any{
				"agent_pod": "agent-pod-1",
				"conditions": []any{map[string]any{
					"type":   "Ready",
					"status": "True",
				}},
			},
		},
	})
	record := onlyRecord(logs)
	record.Attributes().PutStr("openshell.acquisition.kind", "sandbox.lifecycle")
	record.Attributes().PutStr("openshell.acquisition.source_instance", "watchsandbox")
	record.Attributes().PutStr("openshell.acquisition.transport", "grpc")
	record.Attributes().PutStr(
		"openshell.acquisition.api_operation",
		"openshell.v1.OpenShell/WatchSandbox",
	)
	record.Attributes().PutStr("openshell.acquisition.payload_type", "openshell.v1.Sandbox")
	record.Attributes().PutBool("openshell.acquisition.stream_resumable", false)
	record.Attributes().PutBool("openshell.acquisition.follow_status", true)
	record.Attributes().PutBool("openshell.acquisition.follow_logs", true)
	record.Attributes().PutBool("openshell.acquisition.follow_events", true)
	record.Attributes().PutInt("openshell.acquisition.log_tail_lines", 200)
	record.Attributes().PutInt("openshell.acquisition.event_tail", 200)
	record.Attributes().PutStr("openshell.sandbox.id", "sandbox-1")
	if _, err := processor.processLogs(context.Background(), logs); err != nil {
		t.Fatal(err)
	}

	envelope := record.Body().Map().AsRaw()
	acquisition := envelope["acquisition"].(map[string]any)
	for key, expected := range map[string]any{
		"transport":        "grpc",
		"api_operation":    "openshell.v1.OpenShell/WatchSandbox",
		"payload_type":     "openshell.v1.Sandbox",
		"stream_resumable": false,
		"follow_status":    true,
		"follow_logs":      true,
		"follow_events":    true,
		"log_tail_lines":   int64(200),
		"event_tail":       int64(200),
	} {
		if acquisition[key] != expected {
			t.Fatalf("acquisition[%q]=%#v, want %#v", key, acquisition[key], expected)
		}
	}
	coverage := acquisition["coverage"].(map[string]any)
	if coverage["status"] != "observed" ||
		coverage["source_profile"] != "watchsandbox" ||
		coverage["payload_preservation"] != "full_public_proto_json" {
		t.Fatalf("coverage=%#v", coverage)
	}
	limitations := coverage["known_limitations"].([]any)
	if len(limitations) != 3 || limitations[2] != "watch_events_unavailable" {
		t.Fatalf("known limitations=%#v", limitations)
	}

	original := envelope["original"].(map[string]any)
	sourcePayload := original["source_payload"].(map[string]any)
	spec := sourcePayload["spec"].(map[string]any)
	environment := spec["environment"].(map[string]any)
	if environment["authorization"] != redacted {
		t.Fatalf("nested authorization=%#v", environment["authorization"])
	}
	if spec["future_public_field"].(map[string]any)["preserve"] != true {
		t.Fatalf("unknown public source fields were lost: %#v", spec)
	}
	status := sourcePayload["status"].(map[string]any)
	if status["agent_pod"] != "agent-pod-1" ||
		len(status["conditions"].([]any)) != 1 {
		t.Fatalf("status=%#v", status)
	}

	correlation := envelope["correlation"].(map[string]any)
	for key, expected := range map[string]any{
		"trace_id":      "trace-from-fields",
		"span_id":       "span-from-fields",
		"request_id":    "request-from-fields",
		"session_id":    "session-from-annotation",
		"invocation_id": "invocation-from-annotation",
	} {
		if correlation[key] != expected {
			t.Fatalf("correlation[%q]=%#v, want %#v", key, correlation[key], expected)
		}
	}
}

func TestCorrelationPrefersCanonicalRootIdentifiers(t *testing.T) {
	got := correlations(map[string]any{
		"trace_id": "root-trace",
		"fields": map[string]any{
			"trace.id": "nested-trace",
			"span.id":  "nested-span",
		},
		"source_payload": map[string]any{
			"metadata": map[string]any{
				"annotations": map[string]any{"trace.id": "annotation-trace"},
			},
		},
	}, nil)
	if got["trace_id"] != "root-trace" || got["span_id"] != "nested-span" {
		t.Fatalf("correlation precedence=%#v", got)
	}
}

func TestStreamWarningCoverageIsExplicitGapEvidence(t *testing.T) {
	attributes := pcommon.NewMap()
	attributes.PutStr(
		"openshell.acquisition.payload_type",
		"openshell.exporter.watchsandbox.Diagnostic",
	)
	got := acquisitionCoverage("stream.warning", attributes)
	if got["status"] != "gap_reported" ||
		got["payload_preservation"] != "exporter_diagnostic" {
		t.Fatalf("coverage=%#v", got)
	}
}

func FuzzMalformedEvidenceIsRetained(f *testing.F) {
	f.Add("authorization=Bearer abc.def.ghi")
	f.Add("{not-json")
	f.Fuzz(func(t *testing.T, body string) {
		if len(body) > 64*1024 {
			t.Skip()
		}
		processor, err := newProcessor(createDefaultConfig().(*Config), zap.NewNop())
		if err != nil {
			t.Fatal(err)
		}
		logs := testLogs(t, body)
		record := onlyRecord(logs)
		record.Attributes().PutStr("openshell.acquisition.kind", "ocsf.file")
		record.Attributes().PutStr("log.file.path", "/var/log/fuzz.jsonl")
		record.Attributes().PutInt("log.file.record_offset", 1)
		if _, err := processor.processLogs(context.Background(), logs); err != nil {
			t.Fatal(err)
		}
		if logs.LogRecordCount() != 1 {
			t.Fatal("evidence was dropped")
		}
		id, ok := record.Attributes().Get("event.id")
		if !ok || !strings.HasPrefix(id.Str(), "sha256:") {
			t.Fatalf("invalid event ID %q", id.Str())
		}
		if _, ok := record.Body().Map().Get("original"); !ok {
			t.Fatal("original evidence is missing")
		}
	})
}

func BenchmarkOCSFNormalization(b *testing.B) {
	processor, err := newProcessor(createDefaultConfig().(*Config), zap.NewNop())
	if err != nil {
		b.Fatal(err)
	}
	b.ReportAllocs()
	for b.Loop() {
		logs := plog.NewLogs()
		record := onlyRecord(logs)
		if err := record.Body().FromRaw(validOCSF()); err != nil {
			b.Fatal(err)
		}
		record.Attributes().PutStr("openshell.acquisition.kind", "ocsf.file")
		record.Attributes().PutStr("log.file.path", "/var/log/openshell-ocsf.jsonl")
		record.Attributes().PutInt("log.file.record_offset", 128)
		if _, err := processor.processLogs(context.Background(), logs); err != nil {
			b.Fatal(err)
		}
	}
}

func validOCSF() map[string]any {
	return map[string]any{
		"class_uid":    int64(4001),
		"category_uid": int64(4),
		"activity_id":  int64(1),
		"type_uid":     int64(400101),
		"severity_id":  int64(4),
		"time":         int64(1_700_000_000_000),
		"metadata": map[string]any{
			"version":            currentOCSFVersion,
			"original_event_uid": "uid-1",
		},
		"unmapped": map[string]any{"sandbox_id": "sandbox-1"},
	}
}

func testLogs(t *testing.T, body any) plog.Logs {
	t.Helper()
	logs := plog.NewLogs()
	record := onlyRecord(logs)
	if err := record.Body().FromRaw(body); err != nil {
		t.Fatal(err)
	}
	return logs
}

func onlyRecord(logs plog.Logs) plog.LogRecord {
	if logs.ResourceLogs().Len() == 0 {
		logs.ResourceLogs().
			AppendEmpty().
			ScopeLogs().
			AppendEmpty().
			LogRecords().
			AppendEmpty()
	}
	return logs.ResourceLogs().At(0).ScopeLogs().At(0).LogRecords().At(0)
}

func TestAgentAndToolCorrelationFieldsAreRetained(t *testing.T) {
	got := correlations(map[string]any{
		"agent_session_id": "session-1",
		"tool_call_id":     "call-1",
	}, nil)
	if got["agent_session_id"] != "session-1" || got["tool_call_id"] != "call-1" {
		t.Fatalf("correlation=%#v", got)
	}
}

func TestCanonicalCorrelationContextAddsTrustedIdentity(t *testing.T) {
	attributes := pcommon.NewMap()
	attributes.PutInt("openshell.policy.version", 17)
	got := canonicalCorrelationContext(
		map[string]any{
			"agent_session_id":     "session-1",
			"openshell.gateway.id": "source-forgery",
			"request_id":           "request-1",
			"trace_id":             "00112233445566778899aabbccddeeff",
		},
		"gateway-1",
		"default",
		"sandbox-1",
		attributes,
	)
	want := map[string]any{
		"agent.session.id":         "session-1",
		"openshell.gateway.id":     "gateway-1",
		"openshell.policy.version": int64(17),
		"openshell.sandbox.id":     "sandbox-1",
		"openshell.workspace":      "default",
		"request_id":               "request-1",
		"trace_id":                 "00112233445566778899aabbccddeeff",
	}
	for key, expected := range want {
		if got[key] != expected {
			t.Fatalf("correlation[%q]=%#v, want %#v", key, got[key], expected)
		}
	}
	if got["agent_session_id"] != "session-1" {
		t.Fatal("source-compatible agent_session_id was removed")
	}
}
