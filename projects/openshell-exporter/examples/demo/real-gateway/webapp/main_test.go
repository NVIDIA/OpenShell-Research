// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package main

import (
	"bytes"
	"compress/gzip"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"

	"go.opentelemetry.io/collector/pdata/ptrace"
)

var testToken = strings.Repeat("test-token-", 4)

func TestCloudEventCorrelationConsumesCanonicalIdentityContext(t *testing.T) {
	got := cloudEventCorrelation(map[string]any{
		"correlation": map[string]any{
			"agent.session.id":         "session-1",
			"agent_session_id":         "legacy-session-must-not-win",
			"openshell.gateway.id":     "gateway-1",
			"openshell.policy.version": int64(17),
			"openshell.sandbox.id":     "sandbox-1",
			"openshell.workspace":      "default",
			"request_id":               "request-1",
			"tool_call_id":             "call-1",
			"trace_id":                 "00112233445566778899aabbccddeeff",
		},
	})
	want := map[string]string{
		"agent.session.id":         "session-1",
		"openshell.gateway.id":     "gateway-1",
		"openshell.policy.version": "17",
		"openshell.sandbox.id":     "sandbox-1",
		"openshell.workspace":      "default",
		"request_id":               "request-1",
		"tool_call_id":             "call-1",
		"trace_id":                 "00112233445566778899aabbccddeeff",
	}
	for key, expected := range want {
		if got[key] != expected {
			t.Fatalf("correlation[%q]=%q, want %q", key, got[key], expected)
		}
	}
}

func TestCloudEventsBatchAuthenticationAndAtomicValidation(t *testing.T) {
	server, err := newDashboardServer(testToken, 10)
	if err != nil {
		t.Fatal(err)
	}
	valid := map[string]any{
		"specversion": "1.0",
		"id":          "sha256:one",
		"source":      "openshell://demo/workspaces/default/sandboxes/one/sources/ocsf_jsonl",
		"type":        "com.nvidia.openshell.sandbox.log.v1",
		"data": map[string]any{
			"acquisition": map[string]any{"kind": "sandbox.log"},
			"openshell": map[string]any{
				"gateway_id": "gateway-1",
				"workspace":  "default",
				"sandbox_id": "sandbox-1",
			},
			"correlation": map[string]any{"session_id": "session-1"},
			"security": map[string]any{
				"validation": map[string]any{"status": "not_applicable"},
			},
		},
	}
	invalid := map[string]any{"specversion": "1.0"}
	body, _ := json.Marshal([]any{valid, invalid})
	request := httptest.NewRequest(http.MethodPost, "/v1/events", bytes.NewReader(body))
	request.Header.Set("Content-Type", "application/cloudevents-batch+json")
	request.Header.Set("Authorization", "Bearer "+testToken)
	response := httptest.NewRecorder()
	server.routes().ServeHTTP(response, request)
	if response.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d", response.Code)
	}
	if got := len(server.store.snapshot(10)); got != 0 {
		t.Fatalf("atomic rejection stored %d events", got)
	}

	body, _ = json.Marshal([]any{valid})
	request = httptest.NewRequest(http.MethodPost, "/v1/events", bytes.NewReader(body))
	request.Header.Set("Content-Type", "application/cloudevents-batch+json; charset=utf-8")
	response = httptest.NewRecorder()
	server.routes().ServeHTTP(response, request)
	if response.Code != http.StatusUnauthorized {
		t.Fatalf("expected unauthenticated request to return 401, got %d", response.Code)
	}

	request = httptest.NewRequest(http.MethodPost, "/v1/events", bytes.NewReader(body))
	request.Header.Set("Content-Type", "application/cloudevents-batch+json; charset=utf-8")
	request.Header.Set("Authorization", "Bearer "+testToken)
	response = httptest.NewRecorder()
	server.routes().ServeHTTP(response, request)
	if response.Code != http.StatusNoContent {
		t.Fatalf("expected 204, got %d: %s", response.Code, response.Body.String())
	}
	items := server.store.snapshot(10)
	if len(items) != 1 || items[0].Type != "com.nvidia.openshell.sandbox.log.v1" {
		t.Fatalf("unexpected stored items: %#v", items)
	}
	if items[0].Valid != nil {
		t.Fatalf("not_applicable evidence classified as valid=%v", *items[0].Valid)
	}
}

func TestOTLPTraceIngestion(t *testing.T) {
	server, err := newDashboardServer(testToken, 10)
	if err != nil {
		t.Fatal(err)
	}
	traces := ptrace.NewTraces()
	resourceSpans := traces.ResourceSpans().AppendEmpty()
	resourceSpans.Resource().Attributes().PutStr("service.name", "hermes-agent")
	resourceSpans.Resource().Attributes().PutStr("openshell.sandbox.id", "sandbox-1")
	span := resourceSpans.ScopeSpans().AppendEmpty().Spans().AppendEmpty()
	span.SetName("invoke_agent")
	span.SetStartTimestamp(1_000_000_000)
	span.SetEndTimestamp(2_500_000_000)
	span.Attributes().PutStr("openinference.span.kind", "AGENT")
	span.Attributes().PutStr("agent.session.id", "session-1")
	span.Attributes().PutStr("tool_call_id", "call-1")
	span.Attributes().PutInt("gen_ai.usage.input_tokens", 12)
	span.Attributes().PutInt("gen_ai.usage.output_tokens", 5)
	span.Status().SetCode(ptrace.StatusCodeError)
	var marshaler ptrace.ProtoMarshaler
	body, err := marshaler.MarshalTraces(traces)
	if err != nil {
		t.Fatal(err)
	}
	request := httptest.NewRequest(http.MethodPost, "/v1/traces", bytes.NewReader(body))
	request.Header.Set("Content-Type", "application/x-protobuf")
	request.Header.Set("Authorization", "Bearer "+testToken)
	response := httptest.NewRecorder()
	server.routes().ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", response.Code, response.Body.String())
	}
	items := server.store.snapshot(10)
	if len(items) != 1 || items[0].Kind != "nemo_relay_trace" || items[0].Type != "AGENT" {
		t.Fatalf("unexpected stored trace: %#v", items)
	}
	if items[0].DurationMS != 1500 || items[0].InputTokens != 12 || items[0].OutputTokens != 5 || items[0].TotalTokens != 17 || items[0].TokenUsage != "observed" || items[0].Outcome != "error" {
		t.Fatalf("unexpected trace showcase metrics: %#v", items[0])
	}
}

func TestTraceTokensDistinguishesNotEmittedFromZero(t *testing.T) {
	input, output, total, status := traceTokens(map[string]any{})
	if input != 0 || output != 0 || total != 0 || status != "not_emitted" {
		t.Fatalf("unexpected missing token telemetry: input=%d output=%d total=%d status=%q", input, output, total, status)
	}
	input, output, total, status = traceTokens(map[string]any{"gen_ai.usage.total_tokens": int64(0)})
	if input != 0 || output != 0 || total != 0 || status != "observed" {
		t.Fatalf("unexpected observed zero-token telemetry: input=%d output=%d total=%d status=%q", input, output, total, status)
	}
}

func TestOTLPTraceGzipIngestion(t *testing.T) {
	server, err := newDashboardServer(testToken, 10)
	if err != nil {
		t.Fatal(err)
	}
	traces := ptrace.NewTraces()
	span := traces.ResourceSpans().AppendEmpty().ScopeSpans().AppendEmpty().Spans().AppendEmpty()
	span.SetName("compressed_relay_span")
	span.SetStartTimestamp(1)
	span.SetEndTimestamp(2)
	var marshaler ptrace.ProtoMarshaler
	body, err := marshaler.MarshalTraces(traces)
	if err != nil {
		t.Fatal(err)
	}
	var compressed bytes.Buffer
	gzipWriter := gzip.NewWriter(&compressed)
	if _, err := gzipWriter.Write(body); err != nil {
		t.Fatal(err)
	}
	if err := gzipWriter.Close(); err != nil {
		t.Fatal(err)
	}
	request := httptest.NewRequest(http.MethodPost, "/v1/traces", &compressed)
	request.Header.Set("Content-Type", "application/x-protobuf")
	request.Header.Set("Content-Encoding", "gzip")
	request.Header.Set("Authorization", "Bearer "+testToken)
	response := httptest.NewRecorder()
	server.routes().ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", response.Code, response.Body.String())
	}
	items := server.store.snapshot(10)
	if len(items) != 1 || items[0].Summary != "compressed_relay_span" {
		t.Fatalf("unexpected compressed trace: %#v", items)
	}
}

func TestOTLPTraceRejectsUnsupportedContentEncoding(t *testing.T) {
	server, err := newDashboardServer(testToken, 10)
	if err != nil {
		t.Fatal(err)
	}
	request := httptest.NewRequest(http.MethodPost, "/v1/traces", bytes.NewReader([]byte("payload")))
	request.Header.Set("Content-Type", "application/x-protobuf")
	request.Header.Set("Content-Encoding", "br")
	request.Header.Set("Authorization", "Bearer "+testToken)
	response := httptest.NewRecorder()
	server.routes().ServeHTTP(response, request)
	if response.Code != http.StatusUnsupportedMediaType {
		t.Fatalf("expected 415, got %d: %s", response.Code, response.Body.String())
	}
}

func TestEventStoreNewestFirstAndCapacity(t *testing.T) {
	store := newEventStore(2)
	store.add([]eventItem{{ID: "one", ReceivedAt: time.Now()}, {ID: "two", ReceivedAt: time.Now()}, {ID: "three", ReceivedAt: time.Now()}})
	items := store.snapshot(10)
	if len(items) != 2 || items[0].ID != "three" || items[1].ID != "two" {
		t.Fatalf("unexpected snapshot: %#v", items)
	}
}

func TestCrossSignalCorrelationMetadata(t *testing.T) {
	cloudEvent, err := json.Marshal(map[string]any{
		"specversion": "1.0",
		"id":          "sha256:correlated",
		"source":      "openshell://gateway-1/workspaces/default/sandboxes/sandbox-1/sources/ocsf.file",
		"type":        "com.nvidia.openshell.ocsf.4001.v1",
		"data": map[string]any{
			"acquisition": map[string]any{"kind": "ocsf.file"},
			"openshell": map[string]any{
				"gateway_id": "gateway-1",
				"workspace":  "default",
				"sandbox_id": "sandbox-1",
			},
			"correlation": map[string]any{"session_id": "session-1"},
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	cloudItem, err := cloudEventItem(cloudEvent)
	if err != nil {
		t.Fatal(err)
	}
	if cloudItem.Stage != "network" || cloudItem.Correlation["openshell.sandbox.id"] != "sandbox-1" || cloudItem.Correlation["agent.session.id"] != "session-1" {
		t.Fatalf("unexpected CloudEvent correlation: %#v", cloudItem)
	}

	traces := ptrace.NewTraces()
	resourceSpans := traces.ResourceSpans().AppendEmpty()
	resourceSpans.Resource().Attributes().PutStr("service.name", "hermes-agent")
	resourceSpans.Resource().Attributes().PutStr("openshell.sandbox.id", "sandbox-1")
	span := resourceSpans.ScopeSpans().AppendEmpty().Spans().AppendEmpty()
	span.SetName("invoke_agent")
	span.SetStartTimestamp(1)
	span.Attributes().PutStr("openinference.span.kind", "AGENT")
	span.Attributes().PutStr("agent.session.id", "session-1")
	span.Attributes().PutStr("tool_call_id", "call-1")
	traceItem := traceItems(traces)[0]
	if traceItem.Stage != "prompt" || traceItem.Correlation["openshell.sandbox.id"] != "sandbox-1" || traceItem.Correlation["agent.session.id"] != "session-1" || traceItem.Correlation["tool_call_id"] != "call-1" {
		t.Fatalf("unexpected trace correlation: %#v", traceItem)
	}
}

func TestCloudEventOutcomeClassification(t *testing.T) {
	valid, invalid := true, false
	if got := cloudEventOutcome(map[string]any{"original": map[string]any{"message": "CONNECT denied example.com:443"}}, "com.nvidia.openshell.ocsf.4001.v1", &valid); got != "denied" {
		t.Fatalf("denied outcome=%q", got)
	}
	if got := cloudEventOutcome(map[string]any{}, "com.nvidia.openshell.stream.warning.v1", nil); got != "warning" {
		t.Fatalf("warning outcome=%q", got)
	}
	if got := cloudEventOutcome(map[string]any{}, "com.nvidia.openshell.ocsf.4001.v1", &invalid); got != "invalid" {
		t.Fatalf("invalid outcome=%q", got)
	}
}

func TestShowcaseRendersOneSessionMetricsAndIdentity(t *testing.T) {
	encoded, err := os.ReadFile("index.html")
	if err != nil {
		t.Fatal(err)
	}
	html := string(encoded)
	for _, required := range []string{
		"selectedSessionView",
		"renderPitchStory",
		"Agent intent to security outcome",
		"Security and policy-engine handoff",
		"Real path observed",
		"Connection denied by OpenShell policy",
		"Inspect denial evidence",
		"Exporter has no policy-write credential or mutation client.",
		"Session security evidence",
		"Session latency",
		"Observed tokens in / out",
		"Failures / policy denials",
		"duration_ms",
		"input_tokens",
		"output_tokens",
		"token_usage_status",
		"openshell.sandbox.id",
		"agent.session.id",
		"trace_id",
		"request_id",
		"tool_call_id",
		"openshell.policy.version",
	} {
		if !strings.Contains(html, required) {
			t.Fatalf("showcase is missing %q", required)
		}
	}
	for _, forbidden := range []string{"Apply policy", "/v1/policies", "openshell policy set"} {
		if strings.Contains(html, forbidden) {
			t.Fatalf("presentation fixture contains policy mutation surface %q", forbidden)
		}
	}
}

func TestDemoRunbookPreservesRealEvidenceBoundary(t *testing.T) {
	encoded, err := os.ReadFile("../README.md")
	if err != nil {
		t.Fatal(err)
	}
	pitch := string(encoded)
	for _, required := range []string{"real agent execution", "Do not replace", "temporal correlation", "separate actuator", "not external destination certification"} {
		if !strings.Contains(pitch, required) {
			t.Fatalf("demo runbook is missing %q", required)
		}
	}
	for _, forbidden := range []string{"exporter applies", "exporter recommends"} {
		if strings.Contains(strings.ToLower(pitch), forbidden) {
			t.Fatalf("demo runbook crosses the exporter boundary with %q", forbidden)
		}
	}
}
