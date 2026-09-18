// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package openshellprocessor

import (
	"context"
	"strings"
	"testing"

	"go.uber.org/zap"
)

func TestForwardedOCSFIsValidatedRedactedAndUpstreamCheckpointed(t *testing.T) {
	config := createDefaultConfig().(*Config)
	config.SourceProfiles = []string{"ocsf.forwarded"}
	processor, err := newProcessor(config, zap.NewNop())
	if err != nil {
		t.Fatal(err)
	}

	original := validOCSF()
	original["authorization"] = "Bearer forwarded-secret"
	logs := testLogs(t, original)
	record := onlyRecord(logs)
	record.Attributes().PutStr("openshell.acquisition.kind", "ocsf.forwarded")
	record.Attributes().PutStr("openshell.acquisition.source_instance", "sandbox-a/fluent-bit")
	record.Attributes().PutStr("log.file.path", "/var/log/openshell/openshell-ocsf.log")
	record.Attributes().PutInt("log.file.record_offset", 512)
	record.Attributes().PutInt("log.file.record_number", 9)
	if _, err := processor.processLogs(context.Background(), logs); err != nil {
		t.Fatal(err)
	}

	typeValue, _ := record.Attributes().Get("cloudevents.type")
	if typeValue.Str() != "com.nvidia.openshell.ocsf.4001.v1" {
		t.Fatalf("type=%q", typeValue.Str())
	}
	envelope := record.Body().Map().AsRaw()
	acquisition := envelope["acquisition"].(map[string]any)
	if acquisition["durability"] != "upstream_checkpointed_file" ||
		acquisition["transport"] != "otlp_http" {
		t.Fatalf("acquisition=%#v", acquisition)
	}
	security := envelope["security"].(map[string]any)
	validation := security["validation"].(map[string]any)
	if validation["status"] != validationValid {
		t.Fatalf("validation=%#v", validation)
	}
	redactedOriginal := envelope["original"].(map[string]any)
	if strings.Contains(redactedOriginal["authorization"].(string), "forwarded-secret") {
		t.Fatal("forwarded credential was not redacted")
	}
	if _, ok := security["ocsf"]; !ok {
		t.Fatal("forwarded OCSF identifiers are missing")
	}
}

func TestForwardedOCSFPromotesBodyProvenanceBeforeIdentity(t *testing.T) {
	config := createDefaultConfig().(*Config)
	config.SourceProfiles = []string{"ocsf.forwarded"}
	processor, err := newProcessor(config, zap.NewNop())
	if err != nil {
		t.Fatal(err)
	}
	body := validOCSF()
	body["log.file.path"] = "/var/log/openshell/openshell-ocsf.log"
	body["log.file.record_offset"] = int64(64)
	body["log.file.record_number"] = int64(2)
	body["openshell.acquisition.source_instance"] = "sandbox-a/fluent-bit"
	body["openshell.sandbox.id"] = "sandbox-a"
	logs := testLogs(t, body)
	record := onlyRecord(logs)
	record.Attributes().PutStr("openshell.acquisition.kind", "ocsf.forwarded")
	if _, err := processor.processLogs(context.Background(), logs); err != nil {
		t.Fatal(err)
	}
	for _, key := range []string{
		"log.file.path",
		"log.file.record_offset",
		"log.file.record_number",
		"openshell.acquisition.source_instance",
		"openshell.sandbox.id",
	} {
		if _, ok := record.Attributes().Get(key); !ok {
			t.Fatalf("promoted provenance %q is missing", key)
		}
	}
}

func TestForwardedOCSFIdentityIncludesForwarderInstanceAndCoordinates(t *testing.T) {
	config := createDefaultConfig().(*Config)
	record := onlyRecord(testLogs(t, validOCSF()))
	record.Attributes().PutStr("openshell.acquisition.source_instance", "sandbox-a/fluent-bit")
	record.Attributes().PutStr("log.file.path", "/var/log/openshell/openshell-ocsf.log")
	record.Attributes().PutInt("log.file.record_offset", 1)
	first := stableID(config, record, "ocsf.forwarded", validOCSF())
	record.Attributes().PutStr("openshell.acquisition.source_instance", "sandbox-b/fluent-bit")
	second := stableID(config, record, "ocsf.forwarded", validOCSF())
	if first == second {
		t.Fatal("forwarder source instance did not distinguish stable identity")
	}
}

func TestKubernetesContextPreservesUnknownObjectFields(t *testing.T) {
	config := createDefaultConfig().(*Config)
	config.SourceProfiles = []string{"kubernetes.context"}
	processor, err := newProcessor(config, zap.NewNop())
	if err != nil {
		t.Fatal(err)
	}
	body := map[string]any{
		"type": "MODIFIED",
		"object": map[string]any{
			"apiVersion": "v1",
			"kind":       "Pod",
			"metadata": map[string]any{
				"name":            "sandbox-a",
				"namespace":       "openshell-sandboxes",
				"uid":             "pod-uid-1",
				"resourceVersion": "42",
				"futureField":     "preserved",
				"annotations": map[string]any{
					"openshell.io/sandbox-id": "sandbox-id-1",
				},
				"labels": map[string]any{
					"openshell.ai/sandbox-workspace": "tenant-a",
				},
			},
		},
	}
	logs := testLogs(t, body)
	record := onlyRecord(logs)
	record.Attributes().PutStr("openshell.acquisition.kind", "kubernetes.context")
	if _, err := processor.processLogs(context.Background(), logs); err != nil {
		t.Fatal(err)
	}
	envelope := record.Body().Map().AsRaw()
	if envelope["original"].(map[string]any)["type"] != "MODIFIED" {
		t.Fatalf("original=%#v", envelope["original"])
	}
	acquisition := envelope["acquisition"].(map[string]any)
	if acquisition["durability"] != "resource_version_checkpointed_api" {
		t.Fatalf("acquisition=%#v", acquisition)
	}
	typeValue, _ := record.Attributes().Get("cloudevents.type")
	if typeValue.Str() != "com.nvidia.openshell.kubernetes.context.v1" {
		t.Fatalf("type=%q", typeValue.Str())
	}
	for key, want := range map[string]string{
		"openshell.sandbox.id":        "sandbox-id-1",
		"openshell.workspace":         "tenant-a",
		"k8s.object.uid":              "pod-uid-1",
		"k8s.object.resource_version": "42",
	} {
		value, ok := record.Attributes().Get(key)
		if !ok || value.Str() != want {
			t.Fatalf("attribute %s=%q, want %q", key, value.Str(), want)
		}
	}
}

func TestForwardedRawOCSFIsDecodedWithoutLosingProvenance(t *testing.T) {
	config := createDefaultConfig().(*Config)
	config.SourceProfiles = []string{"ocsf.forwarded"}
	processor, err := newProcessor(config, zap.NewNop())
	if err != nil {
		t.Fatal(err)
	}
	logs := testLogs(t, `{"class_uid":4001,"category_uid":4,"activity_id":1,"type_uid":400101,"time":1770000000000,"metadata":{"version":"1.8.0"},"future":{"kept":true}}`)
	record := onlyRecord(logs)
	record.Attributes().PutStr("openshell.acquisition.kind", "ocsf.forwarded")
	record.Attributes().PutStr("openshell.acquisition.source_instance", "sandbox-a/fluent-bit")
	record.Attributes().PutStr("openshell.sandbox.id", "sandbox-a")
	record.Attributes().PutStr("log.file.path", "/evidence/agent/openshell-ocsf.1.log")
	record.Attributes().PutInt("log.file.record_offset", 128)
	if _, err := processor.processLogs(context.Background(), logs); err != nil {
		t.Fatal(err)
	}
	envelope := record.Body().Map().AsRaw()
	original := envelope["original"].(map[string]any)
	if original["future"].(map[string]any)["kept"] != true {
		t.Fatalf("original=%#v", original)
	}
	validation := envelope["security"].(map[string]any)["validation"].(map[string]any)
	if validation["status"] != validationValid {
		t.Fatalf("validation=%#v", validation)
	}
}

func TestForwardedOperationalLogKeepsRawLineAndIsNotOCSF(t *testing.T) {
	config := createDefaultConfig().(*Config)
	config.SourceProfiles = []string{"openshell.log.forwarded"}
	processor, err := newProcessor(config, zap.NewNop())
	if err != nil {
		t.Fatal(err)
	}
	logs := testLogs(t, "supervisor connected request_id=req-42 secret=secret-value")
	record := onlyRecord(logs)
	record.Attributes().PutStr("openshell.acquisition.kind", "openshell.log.forwarded")
	record.Attributes().PutStr("openshell.acquisition.source_instance", "sandbox-a/fluent-bit")
	record.Attributes().PutStr("openshell.sandbox.id", "sandbox-a")
	record.Attributes().PutStr("log.file.path", "/evidence/network/openshell.1.log")
	record.Attributes().PutInt("log.file.record_offset", 256)
	record.Attributes().PutStr("trace.id", "00112233445566778899aabbccddeeff")
	record.Attributes().PutStr("future.attribute", "preserved")
	record.Attributes().PutStr("api_key", "attribute-secret")
	if _, err := processor.processLogs(context.Background(), logs); err != nil {
		t.Fatal(err)
	}
	envelope := record.Body().Map().AsRaw()
	if envelope["original"] == "supervisor connected request_id=req-42 secret=secret-value" {
		t.Fatal("operational secret-like value was not redacted")
	}
	sourceAttributes := envelope["source_attributes"].(map[string]any)
	if sourceAttributes["future.attribute"] != "preserved" || sourceAttributes["api_key"] != redacted {
		t.Fatalf("source_attributes=%#v", sourceAttributes)
	}
	correlation := envelope["correlation"].(map[string]any)
	if correlation["trace_id"] != "00112233445566778899aabbccddeeff" {
		t.Fatalf("correlation=%#v", correlation)
	}
	security := envelope["security"].(map[string]any)
	if _, exists := security["ocsf"]; exists {
		t.Fatal("operational log was incorrectly classified as OCSF")
	}
	validation := security["validation"].(map[string]any)
	if validation["status"] != validationNotApplicable {
		t.Fatalf("validation=%#v", validation)
	}
	acquisition := envelope["acquisition"].(map[string]any)
	if acquisition["durability"] != "upstream_checkpointed_file" || acquisition["transport"] != "otlp_http" {
		t.Fatalf("acquisition=%#v", acquisition)
	}
	typeValue, _ := record.Attributes().Get("cloudevents.type")
	if typeValue.Str() != "com.nvidia.openshell.sandbox.file_log.v1" {
		t.Fatalf("type=%q", typeValue.Str())
	}
}

func TestSourceCapabilityReportsUnavailableNetworkLane(t *testing.T) {
	config := createDefaultConfig().(*Config)
	config.SourceProfiles = []string{"ocsf.forwarded", "openshell.log.forwarded"}
	processor, err := newProcessor(config, zap.NewNop())
	if err != nil {
		t.Fatal(err)
	}
	logs := testLogs(t, "network file evidence lane unavailable")
	record := onlyRecord(logs)
	record.Attributes().PutStr("openshell.acquisition.kind", "source.capability")
	record.Attributes().PutStr("openshell.acquisition.source_instance", "sandbox-a/fluent-bit")
	record.Attributes().PutStr("openshell.acquisition.transport", "otlp_http")
	record.Attributes().PutStr("openshell.sandbox.id", "sandbox-a")
	record.Attributes().PutStr("openshell.source.agent_file_lane", "enabled")
	record.Attributes().PutStr("openshell.source.network_file_lane", "unavailable")
	if _, err := processor.processLogs(context.Background(), logs); err != nil {
		t.Fatal(err)
	}
	typeValue, _ := record.Attributes().Get("cloudevents.type")
	if typeValue.Str() != "com.nvidia.openshell.source.capability.v1" {
		t.Fatalf("type=%q", typeValue.Str())
	}
	envelope := record.Body().Map().AsRaw()
	acquisition := envelope["acquisition"].(map[string]any)
	coverage := acquisition["coverage"].(map[string]any)
	if acquisition["durability"] != "upstream_buffered_diagnostic" ||
		acquisition["transport"] != "otlp_http" || coverage["status"] != "gap_reported" ||
		coverage["source_profile"] != "sandbox.forwarder" {
		t.Fatalf("acquisition=%#v", acquisition)
	}
	validation := envelope["security"].(map[string]any)["validation"].(map[string]any)
	if validation["status"] != validationNotApplicable {
		t.Fatalf("validation=%#v", validation)
	}
}
