// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package relayprocessor

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"testing"

	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/ptrace"
	"go.uber.org/zap"
)

func TestNormalizeAndAllowListRelayTrace(t *testing.T) {
	cfg := createDefaultConfig().(*Config)
	cfg.GatewayID = "gateway-1"
	cfg.Workspace = "workspace-1"
	processor, err := newProcessor(cfg, zap.NewNop(), nil)
	if err != nil {
		t.Fatal(err)
	}

	traces := ptrace.NewTraces()
	resourceSpans := traces.ResourceSpans().AppendEmpty()
	resource := resourceSpans.Resource().Attributes()
	resource.PutStr("service.name", "hermes-agent")
	resource.PutStr("openshell.sandbox.id", "sandbox-1")
	resource.PutInt("openshell.policy.version", 7)
	resource.PutStr("credential.value", "must-not-leave")
	spans := resourceSpans.ScopeSpans().AppendEmpty().Spans()
	root := spans.AppendEmpty()
	root.SetTraceID(pcommon.TraceID([16]byte{1}))
	root.SetSpanID(pcommon.SpanID([8]byte{1}))
	root.Attributes().PutStr("nemo_relay.session.instance_id", "session-1")
	root.Attributes().PutStr("input.value", "private prompt")
	root.Attributes().PutInt("llm.token_count.total", 42)
	root.Status().SetMessage("response contained a secret")
	child := spans.AppendEmpty()
	child.SetTraceID(root.TraceID())
	child.SetSpanID(pcommon.SpanID([8]byte{2}))
	child.Attributes().PutStr("openshell.gateway.id", "forged-gateway")
	child.Attributes().PutStr("openshell.workspace", "forged-workspace")
	child.Attributes().PutStr("openshell.sandbox.id", "forged-sandbox")
	child.Attributes().PutStr("trace_id", "forged-trace")
	child.Attributes().PutStr("nemo_relay.tool_call_id", "call-1")
	child.Attributes().PutStr("tool.parameters", `{"password":"secret"}`)

	processed, err := processor.processTraces(context.Background(), traces)
	if err != nil {
		t.Fatal(err)
	}
	gotResource := processed.ResourceSpans().At(0).Resource().Attributes()
	assertString(t, gotResource, "openshell.gateway.id", "gateway-1")
	assertString(t, gotResource, "openshell.workspace", "workspace-1")
	assertString(t, gotResource, "openshell.sandbox.id", "sandbox-1")
	if _, ok := gotResource.Get("credential.value"); ok {
		t.Fatal("credential resource attribute was not removed")
	}
	gotSpans := processed.ResourceSpans().At(0).ScopeSpans().At(0).Spans()
	for index := 0; index < gotSpans.Len(); index++ {
		attributes := gotSpans.At(index).Attributes()
		assertString(t, attributes, "openshell.gateway.id", "gateway-1")
		assertString(t, attributes, "openshell.workspace", "workspace-1")
		assertString(t, attributes, "openshell.sandbox.id", "sandbox-1")
		assertString(t, attributes, "agent.session.id", "session-1")
		assertString(t, attributes, "trace_id", pcommon.TraceID([16]byte{1}).String())
		policyVersion, ok := attributes.Get("openshell.policy.version")
		if !ok || policyVersion.Type() != pcommon.ValueTypeInt || policyVersion.Int() != 7 {
			t.Fatalf("span %d openshell.policy.version=%v, want 7", index, policyVersion.AsRaw())
		}
	}
	assertString(t, gotSpans.At(1).Attributes(), "tool_call_id", "call-1")
	assertString(t, gotSpans.At(1).Attributes(), "openshell.correlation.status", "complete")
	if _, ok := gotSpans.At(0).Attributes().Get("input.value"); ok {
		t.Fatal("prompt content was not removed")
	}
	if _, ok := gotSpans.At(1).Attributes().Get("tool.parameters"); ok {
		t.Fatal("tool arguments were not removed")
	}
	if gotSpans.At(0).Status().Message() != "" {
		t.Fatal("status message was not removed")
	}
}

func TestDenyListPreservesBenignUsageAndRemovesSensitiveContent(t *testing.T) {
	cfg := createDefaultConfig().(*Config)
	cfg.GatewayID = "gateway-1"
	cfg.Workspace = "workspace-1"
	cfg.Privacy.Mode = privacyDeny
	cfg.Privacy.DeniedAttributes = append(cfg.Privacy.DeniedAttributes, "*cost*")
	processor, err := newProcessor(cfg, zap.NewNop(), nil)
	if err != nil {
		t.Fatal(err)
	}

	traces := ptrace.NewTraces()
	resourceSpans := traces.ResourceSpans().AppendEmpty()
	resource := resourceSpans.Resource().Attributes()
	resource.PutStr("openshell.sandbox.id", "sandbox-1")
	resource.PutStr("authorization.token", "must-not-leave")
	span := resourceSpans.ScopeSpans().AppendEmpty().Spans().AppendEmpty()
	span.SetTraceID(pcommon.TraceID([16]byte{1}))
	span.SetSpanID(pcommon.SpanID([8]byte{1}))
	span.Attributes().PutStr("session.id", "session-1")
	span.Attributes().PutStr("input.value", "private prompt")
	span.Attributes().PutStr("output.value", "private response")
	span.Attributes().PutStr("tool.arguments", `{"password":"secret"}`)
	span.Attributes().PutStr("gen_ai.usage.prompt", "usage-shaped secret")
	span.Attributes().PutStr("llm.token_count.raw", "counter-shaped secret")
	span.Attributes().PutInt("gen_ai.usage.input_tokens", 21)
	span.Attributes().PutInt("llm.token_count.total", 34)
	span.Attributes().PutInt("nemo_relay.llm.token_count.output", 13)
	span.Attributes().PutInt("tokens_in", 21)
	span.Attributes().PutInt("tokens_out", 13)
	span.Attributes().PutStr("token_type", "billing")
	span.Attributes().PutDouble("llm.cost.total", 0.25)
	span.Status().SetMessage("response contained a secret")
	event := span.Events().AppendEmpty()
	event.Attributes().PutStr("prompt.content", "event secret")
	event.Attributes().PutInt("gen_ai.usage.output_tokens", 13)
	link := span.Links().AppendEmpty()
	link.Attributes().PutStr("credential.value", "link secret")
	link.Attributes().PutInt("llm.token_count.input", 21)

	processed, err := processor.processTraces(context.Background(), traces)
	if err != nil {
		t.Fatal(err)
	}
	gotResource := processed.ResourceSpans().At(0).Resource().Attributes()
	if _, ok := gotResource.Get("authorization.token"); ok {
		t.Fatal("authorization token was not removed")
	}
	gotSpan := processed.ResourceSpans().At(0).ScopeSpans().At(0).Spans().At(0)
	attributes := gotSpan.Attributes()
	assertString(t, attributes, "agent.session.id", "session-1")
	assertString(t, attributes, "openshell.correlation.status", "complete")
	for _, key := range []string{"input.value", "output.value", "tool.arguments", "gen_ai.usage.prompt", "llm.token_count.raw", "llm.cost.total"} {
		if _, ok := attributes.Get(key); ok {
			t.Fatalf("sensitive attribute %s was not removed", key)
		}
	}
	for key, expected := range map[string]int64{
		"gen_ai.usage.input_tokens":         21,
		"llm.token_count.total":             34,
		"nemo_relay.llm.token_count.output": 13,
		"tokens_in":                         21,
		"tokens_out":                        13,
	} {
		value, ok := attributes.Get(key)
		if !ok || value.Type() != pcommon.ValueTypeInt || value.Int() != expected {
			t.Fatalf("%s=%v, want %d", key, value.AsRaw(), expected)
		}
	}
	assertString(t, attributes, "token_type", "billing")
	if gotSpan.Status().Message() != "" {
		t.Fatal("status message was not removed")
	}
	if _, ok := gotSpan.Events().At(0).Attributes().Get("prompt.content"); ok {
		t.Fatal("event prompt was not removed")
	}
	if _, ok := gotSpan.Events().At(0).Attributes().Get("gen_ai.usage.output_tokens"); !ok {
		t.Fatal("event usage counter was removed")
	}
	if _, ok := gotSpan.Links().At(0).Attributes().Get("credential.value"); ok {
		t.Fatal("link credential was not removed")
	}
	if _, ok := gotSpan.Links().At(0).Attributes().Get("llm.token_count.input"); !ok {
		t.Fatal("link usage counter was removed")
	}
}

func TestDefaultAllowListDoesNotTrustBroadPrefixesOrUsageShapes(t *testing.T) {
	cfg := createDefaultConfig().(*Config)
	cfg.GatewayID = "gateway-1"
	cfg.Workspace = "workspace-1"
	processor, err := newProcessor(cfg, zap.NewNop(), nil)
	if err != nil {
		t.Fatal(err)
	}

	traces := ptrace.NewTraces()
	resourceSpans := traces.ResourceSpans().AppendEmpty()
	resource := resourceSpans.Resource().Attributes()
	resource.PutStr("service.name", "hermes-agent")
	resource.PutStr("service.password", "must-not-leave")
	resource.PutStr("openshell.sandbox.id", "sandbox-1")
	resource.PutStr("openshell.sandbox.name", "sandbox-name")
	resource.PutStr("openshell.prompt", "must-not-leave")
	span := resourceSpans.ScopeSpans().AppendEmpty().Spans().AppendEmpty()
	span.SetTraceID(pcommon.TraceID([16]byte{1}))
	span.SetSpanID(pcommon.SpanID([8]byte{1}))
	span.Attributes().PutStr("session.id", "session-1")
	span.Attributes().PutStr("error.type", "TimeoutError")
	span.Attributes().PutStr("error.message", "credential-shaped failure detail")
	span.Attributes().PutStr("nemo_relay.mark.uuid", "mark-1")
	span.Attributes().PutStr("nemo_relay.mark.data.result", "tool result")
	span.Attributes().PutStr("gen_ai.usage.prompt", "private prompt")
	span.Attributes().PutInt("gen_ai.usage.input_tokens", 21)
	span.Attributes().PutStr("llm.cost.explanation", "private billing context")
	span.Attributes().PutDouble("llm.cost.total", 0.25)
	span.Attributes().PutInt("tokens_in", 21)
	span.Attributes().PutStr("token_type", "billing")

	processed, err := processor.processTraces(context.Background(), traces)
	if err != nil {
		t.Fatal(err)
	}
	gotResource := processed.ResourceSpans().At(0).Resource().Attributes()
	assertString(t, gotResource, "service.name", "hermes-agent")
	assertString(t, gotResource, "openshell.sandbox.id", "sandbox-1")
	assertString(t, gotResource, "openshell.sandbox.name", "sandbox-name")
	for _, key := range []string{"service.password", "openshell.prompt"} {
		if _, ok := gotResource.Get(key); ok {
			t.Fatalf("content-bearing resource attribute %s survived the default allow-list", key)
		}
	}

	attributes := processed.ResourceSpans().At(0).ScopeSpans().At(0).Spans().At(0).Attributes()
	for key, expected := range map[string]string{
		"agent.session.id":     "session-1",
		"error.type":           "TimeoutError",
		"nemo_relay.mark.uuid": "mark-1",
		"token_type":           "billing",
	} {
		assertString(t, attributes, key, expected)
	}
	for _, key := range []string{
		"error.message",
		"gen_ai.usage.prompt",
		"llm.cost.explanation",
		"nemo_relay.mark.data.result",
	} {
		if _, ok := attributes.Get(key); ok {
			t.Fatalf("content-bearing span attribute %s survived the default allow-list", key)
		}
	}
	for key, expected := range map[string]float64{
		"gen_ai.usage.input_tokens": 21,
		"llm.cost.total":            0.25,
		"tokens_in":                 21,
	} {
		value, ok := attributes.Get(key)
		if !ok {
			t.Fatalf("safe quantitative attribute %s was removed", key)
		}
		if value.Type() == pcommon.ValueTypeInt {
			if float64(value.Int()) != expected {
				t.Fatalf("%s=%v, want %v", key, value.AsRaw(), expected)
			}
			continue
		}
		if value.Type() != pcommon.ValueTypeDouble || value.Double() != expected {
			t.Fatalf("%s=%v, want %v", key, value.AsRaw(), expected)
		}
	}
}

func TestResourceAliasesAreNormalizedBeforePrivacyFiltering(t *testing.T) {
	cfg := createDefaultConfig().(*Config)
	cfg.GatewayID = "gateway-1"
	cfg.Workspace = "workspace-1"
	processor, err := newProcessor(cfg, zap.NewNop(), nil)
	if err != nil {
		t.Fatal(err)
	}

	traces := ptrace.NewTraces()
	resourceSpans := traces.ResourceSpans().AppendEmpty()
	resource := resourceSpans.Resource().Attributes()
	resource.PutStr("openshell.sandbox.id", "sandbox-resource")
	resource.PutStr("nemo_relay.session_id", "session-resource")
	resource.PutStr("nemo_relay.request_id", "request-resource")
	resource.PutStr("gen_ai.tool.call.id", "call-resource")
	resource.PutInt("policy.version", 42)
	span := resourceSpans.ScopeSpans().AppendEmpty().Spans().AppendEmpty()
	span.SetTraceID(pcommon.TraceID([16]byte{1}))
	span.SetSpanID(pcommon.SpanID([8]byte{1}))
	span.Attributes().PutStr("agent.session.id", "")
	span.Attributes().PutStr("request_id", "")
	span.Attributes().PutStr("tool_call_id", "")
	span.Attributes().PutStr("session.id", "")
	span.Attributes().PutStr("nemo_relay.session_id", "")
	span.Attributes().PutStr("gen_ai.request.id", "")
	span.Attributes().PutStr("nemo_relay.request_id", "")
	span.Attributes().PutStr("nemo_relay.tool_call_id", "")
	span.Attributes().PutStr("gen_ai.tool.call.id", "")

	processed, err := processor.processTraces(context.Background(), traces)
	if err != nil {
		t.Fatal(err)
	}
	gotResource := processed.ResourceSpans().At(0).Resource().Attributes()
	for _, removedAlias := range []string{"nemo_relay.session_id", "nemo_relay.request_id", "policy.version"} {
		if _, ok := gotResource.Get(removedAlias); ok {
			t.Fatalf("resource alias %s bypassed the allow-list", removedAlias)
		}
	}
	attributes := processed.ResourceSpans().At(0).ScopeSpans().At(0).Spans().At(0).Attributes()
	assertString(t, attributes, "agent.session.id", "session-resource")
	assertString(t, attributes, "request_id", "request-resource")
	assertString(t, attributes, "tool_call_id", "call-resource")
	assertString(t, attributes, "openshell.gateway.id", "gateway-1")
	assertString(t, attributes, "openshell.workspace", "workspace-1")
	assertString(t, attributes, "openshell.sandbox.id", "sandbox-resource")
	assertString(t, attributes, "trace_id", pcommon.TraceID([16]byte{1}).String())
	assertString(t, attributes, "openshell.correlation.status", "complete")
	policyVersion, ok := attributes.Get("openshell.policy.version")
	if !ok || policyVersion.Type() != pcommon.ValueTypeInt || policyVersion.Int() != 42 {
		t.Fatalf("openshell.policy.version=%v, want 42", policyVersion.AsRaw())
	}
}

func TestMissingNativeTraceIDRemovesSourceClaim(t *testing.T) {
	cfg := createDefaultConfig().(*Config)
	processor, err := newProcessor(cfg, zap.NewNop(), nil)
	if err != nil {
		t.Fatal(err)
	}
	traces := ptrace.NewTraces()
	span := traces.ResourceSpans().AppendEmpty().ScopeSpans().AppendEmpty().Spans().AppendEmpty()
	span.Attributes().PutStr("trace_id", "source-invented-trace")

	processed, err := processor.processTraces(context.Background(), traces)
	if err != nil {
		t.Fatal(err)
	}
	attributes := processed.ResourceSpans().At(0).ScopeSpans().At(0).Spans().At(0).Attributes()
	if _, ok := attributes.Get("trace_id"); ok {
		t.Fatal("source-supplied trace_id survived without a native OTLP trace identity")
	}
}

func TestMissingSandboxIsVisible(t *testing.T) {
	cfg := createDefaultConfig().(*Config)
	processor, err := newProcessor(cfg, zap.NewNop(), nil)
	if err != nil {
		t.Fatal(err)
	}
	traces := ptrace.NewTraces()
	span := traces.ResourceSpans().AppendEmpty().ScopeSpans().AppendEmpty().Spans().AppendEmpty()
	span.SetTraceID(pcommon.TraceID([16]byte{1}))
	span.Attributes().PutStr("session.id", "session-1")

	processed, err := processor.processTraces(context.Background(), traces)
	if err != nil {
		t.Fatal(err)
	}
	attributes := processed.ResourceSpans().At(0).ScopeSpans().At(0).Spans().At(0).Attributes()
	assertString(t, attributes, "openshell.correlation.status", "partial")
	assertString(t, attributes, "openshell.correlation.missing", "openshell.sandbox.id")
}

func TestInvalidProtectedIdentifierValuesCannotBypassPrivacy(t *testing.T) {
	cfg := createDefaultConfig().(*Config)
	processor, err := newProcessor(cfg, zap.NewNop(), nil)
	if err != nil {
		t.Fatal(err)
	}
	traces := ptrace.NewTraces()
	resourceSpans := traces.ResourceSpans().AppendEmpty()
	resource := resourceSpans.Resource().Attributes()
	resource.PutStr("openshell.sandbox.id", "sandbox secret=must-not-leave")
	resource.PutStr("openshell.policy.version", "Bearer must-not-leave")
	resource.PutStr("agent.session.id", "credential=must-not-leave")
	span := resourceSpans.ScopeSpans().AppendEmpty().Spans().AppendEmpty()
	span.SetTraceID(pcommon.TraceID([16]byte{1}))
	span.Attributes().PutStr("openshell.sandbox.id", "forged-sandbox")
	span.Attributes().PutStr("agent.session.id", "prompt content must-not-leave")
	span.Attributes().PutStr("request_id", "response content must-not-leave")
	span.Attributes().PutStr("tool_call_id", `{"arguments":"must-not-leave"}`)

	processed, err := processor.processTraces(context.Background(), traces)
	if err != nil {
		t.Fatal(err)
	}
	gotResource := processed.ResourceSpans().At(0).Resource().Attributes()
	for _, key := range []string{"openshell.sandbox.id", "openshell.policy.version", "agent.session.id"} {
		if _, ok := gotResource.Get(key); ok {
			t.Fatalf("invalid protected resource attribute %s survived", key)
		}
	}
	attributes := processed.ResourceSpans().At(0).ScopeSpans().At(0).Spans().At(0).Attributes()
	for _, key := range []string{"openshell.sandbox.id", "openshell.policy.version", "agent.session.id", "request_id", "tool_call_id"} {
		if _, ok := attributes.Get(key); ok {
			t.Fatalf("invalid protected span attribute %s survived", key)
		}
	}
	assertString(t, attributes, "openshell.correlation.status", "partial")
	assertString(t, attributes, "openshell.correlation.missing", "openshell.sandbox.id,agent.session.id")
	assertString(t, attributes, "openshell.correlation.invalid", "agent.session.id,openshell.policy.version,openshell.sandbox.id,request_id,tool_call_id")
	var marshaler ptrace.ProtoMarshaler
	encoded, err := marshaler.MarshalTraces(processed)
	if err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(encoded, []byte("must-not-leave")) {
		t.Fatal("invalid protected identifier content survived serialized telemetry")
	}
}

func TestPrivacyConfigurationValidation(t *testing.T) {
	cfg := createDefaultConfig().(*Config)
	if err := cfg.Validate(); err != nil {
		t.Fatal(err)
	}
	cfg.Privacy.Mode = "unknown"
	if err := cfg.Validate(); err == nil {
		t.Fatal("expected invalid privacy mode to fail")
	}
	cfg = createDefaultConfig().(*Config)
	cfg.GatewayID = "gateway secret"
	if err := cfg.Validate(); err == nil {
		t.Fatal("expected unsafe gateway identity to fail")
	}
	cfg = createDefaultConfig().(*Config)
	cfg.Workspace = "workspace=secret"
	if err := cfg.Validate(); err == nil {
		t.Fatal("expected unsafe workspace identity to fail")
	}
}

func assertString(t *testing.T, attributes pcommon.Map, key, expected string) {
	t.Helper()
	value, ok := attributes.Get(key)
	if !ok || value.Type() != pcommon.ValueTypeStr || value.Str() != expected {
		t.Fatalf("%s=%v, want %q", key, value.AsRaw(), expected)
	}
}

func TestAllowListNormalizesUnboundedOTLPNamesAndScopeMetadata(t *testing.T) {
	cfg := createDefaultConfig().(*Config)
	processor, err := newProcessor(cfg, zap.NewNop(), nil)
	if err != nil {
		t.Fatal(err)
	}
	traces := ptrace.NewTraces()
	scopeSpans := traces.ResourceSpans().AppendEmpty().ScopeSpans().AppendEmpty()
	scopeSpans.Scope().Attributes().PutStr("prompt.content", "must-not-leave")
	span := scopeSpans.Spans().AppendEmpty()
	span.SetName("private prompt must-not-leave")
	span.SetTraceID(pcommon.TraceID([16]byte{1}))
	span.SetSpanID(pcommon.SpanID([8]byte{1}))
	span.TraceState().FromRaw("vendor=must-not-leave")
	span.Attributes().PutStr("openinference.span.kind", "TOOL")
	event := span.Events().AppendEmpty()
	event.SetName("private tool result must-not-leave")
	event.Attributes().PutStr("tool.result", "must-not-leave")
	processed, err := processor.processTraces(context.Background(), traces)
	if err != nil {
		t.Fatal(err)
	}
	gotScope := processed.ResourceSpans().At(0).ScopeSpans().At(0)
	if _, ok := gotScope.Scope().Attributes().Get("prompt.content"); ok {
		t.Fatal("scope prompt attribute was not removed")
	}
	gotSpan := gotScope.Spans().At(0)
	if gotSpan.Name() != "relay.tool" {
		t.Fatalf("span name = %q, want relay.tool", gotSpan.Name())
	}
	if gotSpan.TraceState().AsRaw() != "" {
		t.Fatal("trace state was not removed")
	}
	if gotSpan.Events().At(0).Name() != "relay.event" {
		t.Fatalf("event name = %q, want relay.event", gotSpan.Events().At(0).Name())
	}
	var marshaler ptrace.ProtoMarshaler
	encoded, err := marshaler.MarshalTraces(processed)
	if err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(encoded, []byte("must-not-leave")) {
		t.Fatal("unbounded OTLP metadata survived serialized telemetry")
	}
}

func FuzzAllowListNeverSerializesUntrustedContent(f *testing.F) {
	f.Add([]byte("private prompt and tool result"))
	f.Add([]byte{0, 1, 2, 3, 255})
	f.Fuzz(func(t *testing.T, input []byte) {
		if len(input) > 64*1024 {
			t.Skip()
		}
		digest := sha256.Sum256(input)
		key := "untrusted." + hex.EncodeToString(digest[:8])
		sentinel := "private-" + hex.EncodeToString(digest[:])

		processor, err := newProcessor(createDefaultConfig().(*Config), zap.NewNop(), nil)
		if err != nil {
			t.Fatal(err)
		}
		traces := ptrace.NewTraces()
		resourceSpans := traces.ResourceSpans().AppendEmpty()
		resourceSpans.Resource().Attributes().PutStr(key, sentinel)
		scopeSpans := resourceSpans.ScopeSpans().AppendEmpty()
		scopeSpans.Scope().Attributes().PutStr(key, sentinel)
		span := scopeSpans.Spans().AppendEmpty()
		span.SetTraceID(pcommon.TraceID([16]byte{1}))
		span.SetSpanID(pcommon.SpanID([8]byte{1}))
		span.Attributes().PutStr(key, sentinel)
		span.Events().AppendEmpty().Attributes().PutStr(key, sentinel)
		span.Links().AppendEmpty().Attributes().PutStr(key, sentinel)

		processed, err := processor.processTraces(context.Background(), traces)
		if err != nil {
			t.Fatal(err)
		}
		var marshaler ptrace.ProtoMarshaler
		encoded, err := marshaler.MarshalTraces(processed)
		if err != nil {
			t.Fatal(err)
		}
		if bytes.Contains(encoded, []byte(sentinel)) {
			t.Fatal("privacy allow list serialized untrusted content")
		}
	})
}

func BenchmarkRelayAllowListProcessing(b *testing.B) {
	processor, err := newProcessor(createDefaultConfig().(*Config), zap.NewNop(), nil)
	if err != nil {
		b.Fatal(err)
	}
	b.ReportAllocs()
	for b.Loop() {
		traces := ptrace.NewTraces()
		resourceSpans := traces.ResourceSpans().AppendEmpty()
		resourceSpans.Resource().Attributes().PutStr("openshell.sandbox.id", "sandbox-1")
		span := resourceSpans.ScopeSpans().AppendEmpty().Spans().AppendEmpty()
		span.SetTraceID(pcommon.TraceID([16]byte{1}))
		span.SetSpanID(pcommon.SpanID([8]byte{1}))
		span.Attributes().PutStr("nemo_relay.session.instance_id", "session-1")
		span.Attributes().PutStr("input.value", "private prompt")
		span.Attributes().PutStr("tool.arguments", `{"password":"secret"}`)
		span.Attributes().PutInt("gen_ai.usage.input_tokens", 128)
		if _, err := processor.processTraces(context.Background(), traces); err != nil {
			b.Fatal(err)
		}
	}
}
