// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package openshellprocessor

import (
	"context"
	"strings"
	"testing"

	"go.uber.org/zap"
)

func TestNeMoRelayFileProfileProducesTypedRedactedEnvelope(t *testing.T) {
	config := createDefaultConfig().(*Config)
	processor, err := newProcessor(config, zap.NewNop())
	if err != nil {
		t.Fatal(err)
	}
	logs := testLogs(t, map[string]any{
		"level":   "INFO",
		"message": "authorization=Bearer relay-secret",
		"unknown": map[string]any{"preserved": true},
	})
	record := onlyRecord(logs)
	record.Attributes().PutStr("openshell.acquisition.kind", "nemo_relay.log")
	record.Attributes().PutStr("openshell.acquisition.source_instance", "nemo-relay-file")
	record.Attributes().PutStr("log.file.path", "/var/log/nemo-relay/relay.jsonl")
	record.Attributes().PutStr("log.file.path_resolved", "/srv/relay/relay.jsonl")
	record.Attributes().PutInt("log.file.record_offset", 512)
	record.Attributes().PutInt("log.file.record_number", 9)

	if _, err := processor.processLogs(context.Background(), logs); err != nil {
		t.Fatal(err)
	}
	eventType, _ := record.Attributes().Get("cloudevents.type")
	if eventType.Str() != "com.nvidia.openshell.nemo_relay.log.v1" {
		t.Fatalf("type=%q", eventType.Str())
	}
	envelope := record.Body().Map()
	acquisition, _ := envelope.Get("acquisition")
	kind, _ := acquisition.Map().Get("kind")
	if kind.Str() != "nemo_relay.log" {
		t.Fatalf("kind=%q", kind.Str())
	}
	durability, _ := acquisition.Map().Get("durability")
	if durability.Str() != "checkpointed_file" {
		t.Fatalf("durability=%q", durability.Str())
	}
	security, _ := envelope.Get("security")
	validation, _ := security.Map().Get("validation")
	status, _ := validation.Map().Get("status")
	if status.Str() != validationNotApplicable {
		t.Fatalf("validation=%q", status.Str())
	}
	original, _ := envelope.Get("original")
	message, _ := original.Map().Get("message")
	if strings.Contains(message.Str(), "relay-secret") {
		t.Fatalf("Relay secret was not redacted: %q", message.Str())
	}
	unknown, _ := original.Map().Get("unknown")
	if preserved, ok := unknown.Map().Get("preserved"); !ok || !preserved.Bool() {
		t.Fatal("unknown Relay field was lost")
	}
}

func TestNeMoRelayFileIdentityUsesCoordinatesBeforeRedaction(t *testing.T) {
	body := map[string]any{"message": "authorization=Bearer relay-secret", "unknown": true}
	config := createDefaultConfig().(*Config)
	config.SourceProfiles = []string{"nemo_relay.log"}
	process := func(offset int64, patterns []string) string {
		t.Helper()
		local := *config
		local.Redaction = config.Redaction
		local.Redaction.Patterns = patterns
		processor, err := newProcessor(&local, zap.NewNop())
		if err != nil {
			t.Fatal(err)
		}
		logs := testLogs(t, body)
		record := onlyRecord(logs)
		record.Attributes().PutStr("openshell.acquisition.kind", "nemo_relay.log")
		record.Attributes().PutStr("log.file.path", "/var/log/nemo-relay/relay.jsonl")
		record.Attributes().PutStr("log.file.path_resolved", "/srv/relay/relay.jsonl")
		record.Attributes().PutInt("log.file.record_offset", offset)
		record.Attributes().PutInt("log.file.record_number", 9)
		if _, err := processor.processLogs(context.Background(), logs); err != nil {
			t.Fatal(err)
		}
		id, _ := record.Attributes().Get("event.id")
		return id.Str()
	}
	first := process(512, config.Redaction.Patterns)
	second := process(512, []string{"relay-secret"})
	if first != second {
		t.Fatalf("redaction profile changed Relay identity: %q != %q", first, second)
	}
	if third := process(513, config.Redaction.Patterns); third == first {
		t.Fatal("different Relay file coordinate reused identity")
	}
}
