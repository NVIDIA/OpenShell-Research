// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package openshellprocessor

import (
	"context"
	"strings"
	"testing"

	"go.opentelemetry.io/collector/pdata/plog"
	"go.uber.org/zap"
)

func TestFullLogSerializationRedactsSourceAttributes(t *testing.T) {
	logs := testLogs(t, validOCSF())
	resource := logs.ResourceLogs().At(0)
	resource.Resource().Attributes().PutStr("authorization", "resource-placeholder")
	scope := resource.ScopeLogs().At(0)
	scope.Scope().Attributes().PutStr("password", "scope-placeholder")
	record := scope.LogRecords().At(0)
	record.Attributes().PutStr("authorization", "record-placeholder")
	record.Attributes().PutStr("message", "Bearer pattern-placeholder")
	record.Attributes().PutEmptySlice("nested").AppendEmpty().SetEmptyMap().PutStr("secret", "nested-placeholder")
	record.Attributes().PutStr("keep", "useful-context")

	// Redaction settings must not change the identity calculated from input.
	unfiltered := plog.NewLogs()
	logs.CopyTo(unfiltered)
	config := createDefaultConfig().(*Config)
	processor, err := newProcessor(config, zap.NewNop())
	if err != nil {
		t.Fatal(err)
	}
	if _, err := processor.processLogs(context.Background(), logs); err != nil {
		t.Fatal(err)
	}
	config.Redaction.Keys = nil
	config.Redaction.Patterns = nil
	baseline, err := newProcessor(config, zap.NewNop())
	if err != nil {
		t.Fatal(err)
	}
	if _, err := baseline.processLogs(context.Background(), unfiltered); err != nil {
		t.Fatal(err)
	}
	id, _ := record.Attributes().Get("cloudevents.id")
	baselineID, _ := unfiltered.ResourceLogs().At(0).ScopeLogs().At(0).LogRecords().At(0).Attributes().Get("cloudevents.id")
	if id.Str() == "" || id.Str() != baselineID.Str() {
		t.Fatal("redaction changed the source identity")
	}
	encoded, err := (&plog.JSONMarshaler{}).MarshalLogs(logs)
	if err != nil {
		t.Fatal(err)
	}
	for _, secret := range []string{"resource-placeholder", "scope-placeholder", "record-placeholder", "pattern-placeholder", "nested-placeholder"} {
		if strings.Contains(string(encoded), secret) {
			t.Errorf("full log serialization retained %s", secret)
		}
	}
	if !strings.Contains(string(encoded), "useful-context") {
		t.Fatal("redaction removed non-sensitive context")
	}
}

func TestDerivedSourceSerializationRedactsBeforeEncoding(t *testing.T) {
	for _, field := range []string{"gateway", "workspace", "sandbox", "sandbox_body"} {
		t.Run(field, func(t *testing.T) {
			cfg := createDefaultConfig().(*Config)
			logs := testLogs(t, validOCSF())
			rec := logs.ResourceLogs().At(0).ScopeLogs().At(0).LogRecords().At(0)
			switch field {
			case "gateway":
				cfg.GatewayID = "Bearer derived-private-token"
			case "workspace":
				rec.Attributes().PutStr("openshell.workspace", "Bearer derived-private-token")
			case "sandbox":
				rec.Attributes().PutStr("openshell.sandbox.id", "Bearer derived-private-token")
			case "sandbox_body":
				rec.Body().Map().PutStr("sandbox_id", "Bearer derived-private-token")
			}
			original := plog.NewLogs()
			logs.CopyTo(original)
			p, err := newProcessor(cfg, zap.NewNop())
			if err != nil {
				t.Fatal(err)
			}
			if _, err = p.processLogs(context.Background(), logs); err != nil {
				t.Fatal(err)
			}
			encoded, err := (&plog.JSONMarshaler{}).MarshalLogs(logs)
			if err != nil {
				t.Fatal(err)
			}
			if strings.Contains(string(encoded), "derived-private-token") {
				t.Error("credential survived in serialized output")
			}
			cfg.Redaction.Patterns = nil
			baseline, err := newProcessor(cfg, zap.NewNop())
			if err != nil {
				t.Fatal(err)
			}
			if _, err = baseline.processLogs(context.Background(), original); err != nil {
				t.Fatal(err)
			}
			id, _ := rec.Attributes().Get("cloudevents.id")
			prior, _ := original.ResourceLogs().At(0).ScopeLogs().At(0).LogRecords().At(0).Attributes().Get("cloudevents.id")
			if id.Str() == "" || id.Str() != prior.Str() {
				t.Error("redaction changed identity")
			}
		})
	}
}
