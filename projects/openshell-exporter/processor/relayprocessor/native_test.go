// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package relayprocessor

import (
	"context"
	"testing"

	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/ptrace"
	"go.uber.org/zap"
)

func TestNativeOpenShellTracePreservesNamesAndDoesNotRequireRelayIDs(t *testing.T) {
	config := createDefaultConfig().(*Config)
	config.GatewayID = "gateway-1"
	config.Workspace = "workspace-1"
	config.TelemetrySource = "openshell_native"
	config.CanonicalizeSpanNames = false
	config.RequiredCorrelationAttributes = nil
	config.Privacy.Mode = privacyDeny
	processor, err := newProcessor(config, zap.NewNop(), nil)
	if err != nil {
		t.Fatal(err)
	}

	traces := ptrace.NewTraces()
	resourceSpans := traces.ResourceSpans().AppendEmpty()
	span := resourceSpans.ScopeSpans().AppendEmpty().Spans().AppendEmpty()
	span.SetName("openshell.gateway.request")
	span.SetTraceID(pcommon.TraceID([16]byte{1}))
	span.SetSpanID(pcommon.SpanID([8]byte{1}))
	span.Attributes().PutStr("http.route", "/openshell.v1.OpenShell/WatchSandbox")
	span.Attributes().PutStr("authorization.token", "must-not-leave")

	processed, err := processor.processTraces(context.Background(), traces)
	if err != nil {
		t.Fatal(err)
	}
	gotResource := processed.ResourceSpans().At(0).Resource().Attributes()
	assertString(t, gotResource, "telemetry.source", "openshell_native")
	got := processed.ResourceSpans().At(0).ScopeSpans().At(0).Spans().At(0)
	if got.Name() != "openshell.gateway.request" {
		t.Fatalf("native span name=%q", got.Name())
	}
	assertString(t, got.Attributes(), "openshell.correlation.status", "complete")
	if _, ok := got.Attributes().Get("authorization.token"); ok {
		t.Fatal("native trace credential was not removed")
	}
	if route, ok := got.Attributes().Get("http.route"); !ok || route.Str() == "" {
		t.Fatal("non-sensitive native trace context was lost")
	}
}
