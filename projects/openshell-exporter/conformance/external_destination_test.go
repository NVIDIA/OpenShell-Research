// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package conformance

import (
	"bytes"
	"context"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/ptrace"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/metadata"
)

type probeEvidence struct {
	SchemaVersion       string `json:"schema_version"`
	RunID               string `json:"run_id"`
	CandidateCommit     string `json:"candidate_commit"`
	Probe               string `json:"probe"`
	Transport           string `json:"transport"`
	Endpoint            string `json:"endpoint"`
	Result              string `json:"result"`
	AcceptedAt          string `json:"accepted_at"`
	Attempts            int    `json:"attempts"`
	HTTPStatuses        []int  `json:"http_statuses,omitempty"`
	CloudEventSource    string `json:"cloudevent_source,omitempty"`
	CloudEventID        string `json:"cloudevent_id,omitempty"`
	TraceID             string `json:"trace_id,omitempty"`
	SpanID              string `json:"span_id,omitempty"`
	DownstreamProofGate string `json:"downstream_proof_gate"`
}

func TestExternalCloudEventsHTTPS(t *testing.T) {
	endpoint := requiredGateEnv(t, "CONFORMANCE_CLOUDEVENTS_URL")
	runID, eventID, _, _ := conformanceIdentity(t, "cloudevents-https")
	token := readToken(t)
	client := conformanceHTTPClient(t, endpoint)
	source := "openshell://conformance/workspaces/default/sandboxes/gate/sources/stream.warning"
	event := map[string]any{
		"specversion":       "1.0",
		"id":                eventID,
		"source":            source,
		"type":              "com.nvidia.openshell.stream.warning.v1",
		"dataschema":        "urn:openshell:event-envelope:1",
		"datacontenttype":   "application/json",
		"subject":           "sandboxes/gate",
		"unknown_extension": map[string]any{"retain": true},
		"data": map[string]any{
			"schema_version": "1.0",
			"observed_time":  time.Now().UTC().Format(time.RFC3339Nano),
			"acquisition": map[string]any{
				"kind":               "stream.warning",
				"durability":         "non_resumable_stream",
				"source_instance":    "external-conformance",
				"replay_limitations": "conformance probe; no source replay",
			},
			"openshell": map[string]any{
				"gateway_id": "conformance",
				"workspace":  "default",
				"sandbox_id": "gate",
			},
			"security": map[string]any{
				"validation": map[string]any{"status": "not_applicable", "errors": []any{}},
				"redaction": map[string]any{
					"profile_id": "conformance", "profile_version": "1", "applied": false, "count": 0,
				},
			},
			"correlation": map[string]any{},
			"original": map[string]any{
				"conformance_probe": true, "conformance_run_id": runID,
				"conformance_candidate_commit": requiredCandidateCommit(t),
			},
		},
	}
	body, err := json.Marshal([]any{event})
	if err != nil {
		t.Fatal(err)
	}
	statuses := make([]int, 0, 2)
	for attempt := 1; attempt <= 2; attempt++ {
		req, err := http.NewRequestWithContext(context.Background(), http.MethodPost, endpoint, bytes.NewReader(body))
		if err != nil {
			t.Fatal(err)
		}
		req.Header.Set("Content-Type", "application/cloudevents-batch+json")
		req.Header.Set("Authorization", "Bearer "+token)
		response, err := client.Do(req)
		if err != nil {
			t.Fatalf("CloudEvents attempt %d: %v", attempt, err)
		}
		statuses = append(statuses, response.StatusCode)
		if err := response.Body.Close(); err != nil {
			t.Fatalf("close CloudEvents response: %v", err)
		}
		if response.StatusCode < 200 || response.StatusCode >= 300 {
			t.Fatalf("CloudEvents attempt %d returned %s", attempt, response.Status)
		}
	}
	writeProbeEvidence(t, probeEvidence{
		SchemaVersion:       "1.0",
		RunID:               runID,
		CandidateCommit:     requiredCandidateCommit(t),
		Probe:               "cloudevents-https",
		Transport:           "cloudevents-batch+json/https",
		Endpoint:            sanitizedEndpoint(t, endpoint),
		Result:              "transport_accepted",
		AcceptedAt:          time.Now().UTC().Format(time.RFC3339Nano),
		Attempts:            2,
		HTTPStatuses:        statuses,
		CloudEventSource:    source,
		CloudEventID:        eventID,
		DownstreamProofGate: "query the same source+id and prove one evidence row, duplicate accounting, and unknown-extension retention",
	})
	t.Logf("sent source=%s id=%s twice; downstream query evidence is still required", source, eventID)
}

func TestExternalOTLPHTTP(t *testing.T) {
	endpoint := requiredGateEnv(t, "CONFORMANCE_OTLP_HTTP_URL")
	runID, _, traceID, spanID := conformanceIdentity(t, "otlp-http")
	request := traceRequest(t, "otlp-http", traceID, spanID)
	encoded, err := request.MarshalProto()
	if err != nil {
		t.Fatal(err)
	}
	client := conformanceHTTPClient(t, endpoint)
	httpRequest, err := http.NewRequestWithContext(context.Background(), http.MethodPost, endpoint, bytes.NewReader(encoded))
	if err != nil {
		t.Fatal(err)
	}
	httpRequest.Header.Set("Content-Type", "application/x-protobuf")
	httpRequest.Header.Set("Authorization", "Bearer "+readToken(t))
	response, err := client.Do(httpRequest)
	if err != nil {
		t.Fatal(err)
	}
	defer func() {
		_ = response.Body.Close()
	}()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		t.Fatalf("OTLP HTTP returned %s", response.Status)
	}
	writeProbeEvidence(t, probeEvidence{
		SchemaVersion:       "1.0",
		RunID:               runID,
		CandidateCommit:     requiredCandidateCommit(t),
		Probe:               "otlp-http",
		Transport:           "otlp/protobuf/https",
		Endpoint:            sanitizedEndpoint(t, endpoint),
		Result:              "transport_accepted",
		AcceptedAt:          time.Now().UTC().Format(time.RFC3339Nano),
		Attempts:            1,
		HTTPStatuses:        []int{response.StatusCode},
		TraceID:             traceID,
		SpanID:              spanID,
		DownstreamProofGate: "query the external backend and prove exact trace_id and span_id preservation",
	})
	t.Logf("transport accepted trace_id=%s span_id=%s; downstream query evidence is still required", traceID, spanID)
}

func TestExternalOTLPGRPC(t *testing.T) {
	endpoint := requiredGateEnv(t, "CONFORMANCE_OTLP_GRPC_ENDPOINT")
	runID, _, traceID, spanID := conformanceIdentity(t, "otlp-grpc")
	connection, err := grpc.NewClient(endpoint, grpc.WithTransportCredentials(credentials.NewTLS(clientTLS(t, "https://"+endpoint))))
	if err != nil {
		t.Fatal(err)
	}
	defer func() {
		_ = connection.Close()
	}()
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	ctx = metadata.AppendToOutgoingContext(ctx, "authorization", "Bearer "+readToken(t))
	if _, err := ptraceotlp.NewGRPCClient(connection).Export(ctx, traceRequest(t, "otlp-grpc", traceID, spanID)); err != nil {
		t.Fatal(err)
	}
	writeProbeEvidence(t, probeEvidence{
		SchemaVersion:       "1.0",
		RunID:               runID,
		CandidateCommit:     requiredCandidateCommit(t),
		Probe:               "otlp-grpc",
		Transport:           "otlp/grpc/tls",
		Endpoint:            endpoint,
		Result:              "transport_accepted",
		AcceptedAt:          time.Now().UTC().Format(time.RFC3339Nano),
		Attempts:            1,
		TraceID:             traceID,
		SpanID:              spanID,
		DownstreamProofGate: "query the external backend and prove exact trace_id and span_id preservation",
	})
	t.Logf("transport accepted trace_id=%s span_id=%s; downstream query evidence is still required", traceID, spanID)
}

func conformanceIdentity(t *testing.T, probe string) (runID, eventID, traceID, spanID string) {
	t.Helper()
	runID = requiredGateEnv(t, "CONFORMANCE_RUN_ID")
	if len(runID) < 8 || len(runID) > 128 {
		t.Fatal("CONFORMANCE_RUN_ID must contain 8 to 128 safe characters")
	}
	for _, character := range runID {
		if (character < 'a' || character > 'z') &&
			(character < 'A' || character > 'Z') &&
			(character < '0' || character > '9') &&
			character != '.' && character != '_' && character != '-' {
			t.Fatal("CONFORMANCE_RUN_ID may contain only letters, digits, dot, underscore, and hyphen")
		}
	}
	if probe == "" {
		t.Fatal("conformance probe name is required")
	}
	identityInput := runID + ":" + probe
	eventHash := sha256.Sum256([]byte("openshell-conformance-event:" + identityInput))
	traceHash := sha256.Sum256([]byte("openshell-conformance-trace:" + identityInput))
	spanHash := sha256.Sum256([]byte("openshell-conformance-span:" + identityInput))
	return runID,
		"sha256:" + hex.EncodeToString(eventHash[:]),
		hex.EncodeToString(traceHash[:16]),
		hex.EncodeToString(spanHash[:8])
}

func traceRequest(t *testing.T, probe, traceIDText, spanIDText string) ptraceotlp.ExportRequest {
	t.Helper()
	request := ptraceotlp.NewExportRequest()
	resourceSpans := request.Traces().ResourceSpans().AppendEmpty()
	resourceSpans.Resource().Attributes().PutStr("openshell.conformance.run_id", requiredGateEnv(t, "CONFORMANCE_RUN_ID"))
	resourceSpans.Resource().Attributes().PutStr("openshell.conformance.probe", probe)
	if validateCandidateCommit(candidateCommit) == nil {
		resourceSpans.Resource().Attributes().PutStr("openshell.conformance.candidate_commit", candidateCommit)
	}
	span := resourceSpans.ScopeSpans().AppendEmpty().Spans().AppendEmpty()
	traceBytes, err := hex.DecodeString(traceIDText)
	if err != nil {
		t.Fatal(err)
	}
	spanBytes, err := hex.DecodeString(spanIDText)
	if err != nil {
		t.Fatal(err)
	}
	var traceID pcommon.TraceID
	var spanID pcommon.SpanID
	copy(traceID[:], traceBytes)
	copy(spanID[:], spanBytes)
	span.SetTraceID(traceID)
	span.SetSpanID(spanID)
	span.SetName("openshell-destination-conformance")
	span.SetKind(ptrace.SpanKindInternal)
	span.SetStartTimestamp(pcommon.NewTimestampFromTime(time.Now().Add(-time.Millisecond)))
	span.SetEndTimestamp(pcommon.NewTimestampFromTime(time.Now()))
	span.Attributes().PutBool("openshell.conformance_probe", true)
	return request
}

func writeProbeEvidence(t *testing.T, evidence probeEvidence) {
	t.Helper()
	boundCommit := requiredCandidateCommit(t)
	if evidence.CandidateCommit != boundCommit {
		t.Fatalf("transport evidence candidate_commit=%q, want %q", evidence.CandidateCommit, boundCommit)
	}
	directory := requiredGateEnv(t, "CONFORMANCE_REPORT_DIR")
	if err := os.MkdirAll(directory, 0o750); err != nil {
		t.Fatal(err)
	}
	encoded, err := json.MarshalIndent(evidence, "", "  ")
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(directory, evidence.Probe+".json")
	if err := os.WriteFile(path, append(encoded, '\n'), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Logf("transport evidence: %s", path)
}

func sanitizedEndpoint(t *testing.T, endpoint string) string {
	t.Helper()
	parsed, err := url.Parse(endpoint)
	if err != nil {
		t.Fatal(err)
	}
	parsed.User = nil
	parsed.RawQuery = ""
	parsed.Fragment = ""
	return parsed.String()
}

func conformanceHTTPClient(t *testing.T, endpoint string) *http.Client {
	t.Helper()
	return &http.Client{
		Transport:     &http.Transport{TLSClientConfig: clientTLS(t, endpoint)},
		Timeout:       15 * time.Second,
		CheckRedirect: refuseConformanceRedirect,
	}
}

func refuseConformanceRedirect(_ *http.Request, _ []*http.Request) error {
	return http.ErrUseLastResponse
}

func clientTLS(t *testing.T, endpoint string) *tls.Config {
	t.Helper()
	parsed, err := url.Parse(endpoint)
	if err != nil {
		t.Fatal(err)
	}
	roots, err := x509.SystemCertPool()
	if err != nil {
		roots = x509.NewCertPool()
	}
	if path := os.Getenv("CONFORMANCE_CA_FILE"); path != "" {
		encoded, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		if !roots.AppendCertsFromPEM(encoded) {
			t.Fatal("CA file contains no certificates")
		}
	}
	config := &tls.Config{MinVersion: tls.VersionTLS12, RootCAs: roots, ServerName: parsed.Hostname()}
	certPath, keyPath := os.Getenv("CONFORMANCE_CLIENT_CERT_FILE"), os.Getenv("CONFORMANCE_CLIENT_KEY_FILE")
	if (certPath == "") != (keyPath == "") {
		t.Fatal("client cert and key must be configured together")
	}
	if certPath != "" {
		certificate, err := tls.LoadX509KeyPair(certPath, keyPath)
		if err != nil {
			t.Fatal(err)
		}
		config.Certificates = []tls.Certificate{certificate}
	}
	return config
}

func readToken(t *testing.T) string {
	t.Helper()
	path := requiredGateEnv(t, "CONFORMANCE_TOKEN_FILE")
	encoded, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	token := strings.TrimSpace(string(encoded))
	if len(token) < 32 {
		t.Fatal("conformance bearer token must be at least 32 bytes")
	}
	return token
}

func requiredGateEnv(t *testing.T, name string) string {
	t.Helper()
	value := os.Getenv(name)
	if value == "" {
		t.Skipf("%s is required for an external destination gate", name)
	}
	return value
}

func TestConformanceIdentityIsStableWithinRunAndDistinctAcrossRuns(t *testing.T) {
	t.Setenv("CONFORMANCE_RUN_ID", "candidate-2026-08-18-a")
	_, eventA, traceA, spanA := conformanceIdentity(t, "otlp-http")
	_, eventARepeat, traceARepeat, spanARepeat := conformanceIdentity(t, "otlp-http")
	if eventA != eventARepeat || traceA != traceARepeat || spanA != spanARepeat {
		t.Fatal("probe identity changed within one named run")
	}
	_, eventOtherProbe, traceOtherProbe, spanOtherProbe := conformanceIdentity(t, "otlp-grpc")
	if eventA == eventOtherProbe || traceA == traceOtherProbe || spanA == spanOtherProbe {
		t.Fatal("probe identity was reused across transports")
	}
	t.Setenv("CONFORMANCE_RUN_ID", "candidate-2026-08-18-b")
	_, eventB, traceB, spanB := conformanceIdentity(t, "otlp-http")
	if eventA == eventB || traceA == traceB || spanA == spanB {
		t.Fatal("probe identity was reused across named runs")
	}
}
