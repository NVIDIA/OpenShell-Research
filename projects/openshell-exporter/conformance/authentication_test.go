// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package conformance

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
)

type authenticationCheck struct {
	Probe     string `json:"probe"`
	Transport string `json:"transport"`
	Endpoint  string `json:"endpoint"`
	Observed  string `json:"observed"`
}

type authenticationEvidence struct {
	SchemaVersion   string                `json:"schema_version"`
	RunID           string                `json:"run_id"`
	CandidateCommit string                `json:"candidate_commit"`
	Result          string                `json:"result"`
	CheckedAt       string                `json:"checked_at"`
	Checks          []authenticationCheck `json:"checks"`
}

func TestExternalAuthenticationRejection(t *testing.T) {
	cloudEndpoint := requiredGateEnv(t, "CONFORMANCE_CLOUDEVENTS_URL")
	httpEndpoint := requiredGateEnv(t, "CONFORMANCE_OTLP_HTTP_URL")
	grpcEndpoint := requiredGateEnv(t, "CONFORMANCE_OTLP_GRPC_ENDPOINT")
	runID := requiredGateEnv(t, "CONFORMANCE_RUN_ID")
	invalidToken := conformanceInvalidToken(runID, readToken(t))

	cloudStatus, err := probeHTTPAuthenticationRejection(
		conformanceHTTPClient(t, cloudEndpoint),
		cloudEndpoint,
		"application/cloudevents-batch+json",
		authenticationCloudEventBatch(t),
		invalidToken,
	)
	if err != nil {
		t.Fatal(err)
	}

	_, _, httpTraceID, httpSpanID := conformanceIdentity(t, "otlp-http-auth-rejection")
	httpRequest := traceRequest(t, "otlp-http-auth-rejection", httpTraceID, httpSpanID)
	httpBody, err := httpRequest.MarshalProto()
	if err != nil {
		t.Fatal(err)
	}
	httpStatus, err := probeHTTPAuthenticationRejection(
		conformanceHTTPClient(t, httpEndpoint),
		httpEndpoint,
		"application/x-protobuf",
		httpBody,
		invalidToken,
	)
	if err != nil {
		t.Fatal(err)
	}

	connection, err := grpc.NewClient(
		grpcEndpoint,
		grpc.WithTransportCredentials(credentials.NewTLS(clientTLS(t, "https://"+grpcEndpoint))),
	)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = connection.Close() }()
	_, _, grpcTraceID, grpcSpanID := conformanceIdentity(t, "otlp-grpc-auth-rejection")
	grpcCode, err := probeGRPCAuthenticationRejection(
		connection,
		traceRequest(t, "otlp-grpc-auth-rejection", grpcTraceID, grpcSpanID),
		invalidToken,
	)
	if err != nil {
		t.Fatal(err)
	}

	evidence := authenticationEvidence{
		SchemaVersion:   backendEvidenceSchemaVersion,
		RunID:           runID,
		CandidateCommit: requiredCandidateCommit(t),
		Result:          "rejected_invalid_bearer",
		CheckedAt:       time.Now().UTC().Format(time.RFC3339Nano),
		Checks: []authenticationCheck{
			{
				Probe:     "cloudevents-https",
				Transport: "cloudevents-batch+json/https",
				Endpoint:  sanitizedEndpoint(t, cloudEndpoint),
				Observed:  fmt.Sprintf("http:%d", cloudStatus),
			},
			{
				Probe:     "otlp-http",
				Transport: "otlp/protobuf/https",
				Endpoint:  sanitizedEndpoint(t, httpEndpoint),
				Observed:  fmt.Sprintf("http:%d", httpStatus),
			},
			{
				Probe:     "otlp-grpc",
				Transport: "otlp/grpc/tls",
				Endpoint:  grpcEndpoint,
				Observed:  "grpc:" + grpcCode.String(),
			},
		},
	}
	writeAuthenticationEvidence(t, evidence)
	t.Log("external CloudEvents and OTLP endpoints rejected the deterministic invalid bearer credential")
}

func probeHTTPAuthenticationRejection(
	client *http.Client,
	endpoint string,
	contentType string,
	body []byte,
	invalidToken string,
) (int, error) {
	request, err := http.NewRequestWithContext(context.Background(), http.MethodPost, endpoint, bytes.NewReader(body))
	if err != nil {
		return 0, err
	}
	request.Header.Set("Content-Type", contentType)
	request.Header.Set("Authorization", "Bearer "+invalidToken)
	response, err := client.Do(request)
	if err != nil {
		return 0, err
	}
	_, copyErr := io.Copy(io.Discard, response.Body)
	closeErr := response.Body.Close()
	if copyErr != nil {
		return 0, copyErr
	}
	if closeErr != nil {
		return 0, closeErr
	}
	if !isHTTPAuthenticationRejection(response.StatusCode) {
		return response.StatusCode, fmt.Errorf("invalid bearer credential returned HTTP %d, want 401 or 403", response.StatusCode)
	}
	return response.StatusCode, nil
}

func probeGRPCAuthenticationRejection(
	connection *grpc.ClientConn,
	request ptraceotlp.ExportRequest,
	invalidToken string,
) (codes.Code, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	ctx = metadata.AppendToOutgoingContext(ctx, "authorization", "Bearer "+invalidToken)
	_, err := ptraceotlp.NewGRPCClient(connection).Export(ctx, request)
	if err == nil {
		return codes.OK, fmt.Errorf("invalid bearer credential was accepted by OTLP gRPC")
	}
	code := status.Code(err)
	if !isGRPCAuthenticationRejection(code) {
		return code, fmt.Errorf("invalid bearer credential returned gRPC %s, want Unauthenticated or PermissionDenied", code)
	}
	return code, nil
}

func isHTTPAuthenticationRejection(statusCode int) bool {
	return statusCode == http.StatusUnauthorized || statusCode == http.StatusForbidden
}

func isGRPCAuthenticationRejection(code codes.Code) bool {
	return code == codes.Unauthenticated || code == codes.PermissionDenied
}

func conformanceInvalidToken(runID string, realToken string) string {
	sum := sha256.Sum256([]byte("openshell-invalid-conformance-token:" + runID))
	candidate := hex.EncodeToString(sum[:])
	if candidate == realToken {
		candidate = strings.Repeat("0", 64)
	}
	return candidate
}

func authenticationCloudEventBatch(t *testing.T) []byte {
	return conformanceCloudEventBatch(t, "cloudevents-auth-rejection")
}

func conformanceCloudEventBatch(t *testing.T, probe string) []byte {
	t.Helper()
	runID, eventID, _, _ := conformanceIdentity(t, probe)
	event := map[string]any{
		"specversion":     "1.0",
		"id":              eventID,
		"source":          "openshell://conformance/workspaces/default/sandboxes/gate/sources/stream.warning",
		"type":            "com.nvidia.openshell.stream.warning.v1",
		"dataschema":      "urn:openshell:event-envelope:1",
		"datacontenttype": "application/json",
		"subject":         "sandboxes/gate",
		"data": map[string]any{
			"schema_version": "1.0",
			"observed_time":  time.Now().UTC().Format(time.RFC3339Nano),
			"acquisition": map[string]any{
				"kind": "stream.warning", "durability": "non_resumable_stream", "source_instance": "external-conformance",
			},
			"openshell": map[string]any{"gateway_id": "conformance", "workspace": "default", "sandbox_id": "gate"},
			"security": map[string]any{
				"validation": map[string]any{"status": "not_applicable", "errors": []any{}},
				"redaction":  map[string]any{"profile_id": "conformance", "profile_version": "1", "applied": false, "count": 0},
			},
			"correlation": map[string]any{},
			"original":    map[string]any{"conformance_probe": true, "conformance_run_id": runID},
		},
	}
	encoded, err := json.Marshal([]any{event})
	if err != nil {
		t.Fatal(err)
	}
	return encoded
}

func writeAuthenticationEvidence(t *testing.T, evidence authenticationEvidence) {
	t.Helper()
	directory := requiredGateEnv(t, "CONFORMANCE_REPORT_DIR")
	if err := os.MkdirAll(directory, 0o750); err != nil {
		t.Fatal(err)
	}
	encoded, err := json.MarshalIndent(evidence, "", "  ")
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(directory, "external-authentication.json")
	if err := os.WriteFile(path, append(encoded, '\n'), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Logf("authentication evidence: %s", path)
}

func TestProbeHTTPAuthenticationRejection(t *testing.T) {
	for _, test := range []struct {
		name       string
		statusCode int
		wantError  bool
	}{
		{name: "unauthorized", statusCode: http.StatusUnauthorized},
		{name: "forbidden", statusCode: http.StatusForbidden},
		{name: "accepted", statusCode: http.StatusAccepted, wantError: true},
	} {
		t.Run(test.name, func(t *testing.T) {
			server := httptest.NewTLSServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
				if request.Header.Get("Authorization") == "" || request.Header.Get("Content-Type") != "application/x-protobuf" {
					writer.WriteHeader(http.StatusBadRequest)
					return
				}
				writer.WriteHeader(test.statusCode)
			}))
			defer server.Close()
			statusCode, err := probeHTTPAuthenticationRejection(
				server.Client(),
				server.URL,
				"application/x-protobuf",
				[]byte("valid-shaped-body"),
				strings.Repeat("x", 64),
			)
			if (err != nil) != test.wantError {
				t.Fatalf("probe error = %v, wantError %v", err, test.wantError)
			}
			if statusCode != test.statusCode {
				t.Fatalf("status = %d, want %d", statusCode, test.statusCode)
			}
		})
	}
}

type rejectingTraceServer struct {
	ptraceotlp.UnimplementedGRPCServer
	expectedAuthorization string
}

func (server *rejectingTraceServer) Export(
	ctx context.Context,
	_ ptraceotlp.ExportRequest,
) (ptraceotlp.ExportResponse, error) {
	values := metadata.ValueFromIncomingContext(ctx, "authorization")
	if len(values) != 1 || values[0] != server.expectedAuthorization {
		return ptraceotlp.NewExportResponse(), status.Error(codes.InvalidArgument, "authorization metadata missing")
	}
	return ptraceotlp.NewExportResponse(), status.Error(codes.Unauthenticated, "invalid bearer")
}

func TestProbeGRPCAuthenticationRejection(t *testing.T) {
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	invalidToken := strings.Repeat("x", 64)
	server := grpc.NewServer()
	ptraceotlp.RegisterGRPCServer(server, &rejectingTraceServer{expectedAuthorization: "Bearer " + invalidToken})
	go func() { _ = server.Serve(listener) }()
	defer server.Stop()

	connection, err := grpc.NewClient(
		listener.Addr().String(),
		grpc.WithTransportCredentials(insecure.NewCredentials()),
	)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = connection.Close() }()
	code, err := probeGRPCAuthenticationRejection(connection, ptraceotlp.NewExportRequest(), invalidToken)
	if err != nil {
		t.Fatal(err)
	}
	if code != codes.Unauthenticated {
		t.Fatalf("gRPC code = %s, want Unauthenticated", code)
	}
}

func TestAuthenticationEvidenceIsCredentialFreeAndRestricted(t *testing.T) {
	directory := t.TempDir()
	t.Setenv("CONFORMANCE_REPORT_DIR", directory)
	evidence := authenticationEvidence{
		SchemaVersion:   backendEvidenceSchemaVersion,
		RunID:           "candidate-2026-08-19-a",
		CandidateCommit: fixtureCandidateCommit,
		Result:          "rejected_invalid_bearer",
		CheckedAt:       time.Now().UTC().Format(time.RFC3339Nano),
		Checks: []authenticationCheck{{
			Probe: "otlp-http", Transport: "otlp/protobuf/https",
			Endpoint: "https://receiver.example.test/v1/traces", Observed: "http:401",
		}},
	}
	writeAuthenticationEvidence(t, evidence)
	path := filepath.Join(directory, "external-authentication.json")
	encoded, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(strings.ToLower(string(encoded)), "authorization") ||
		strings.Contains(string(encoded), strings.Repeat("x", 32)) {
		t.Fatal("authentication evidence contained credential material")
	}
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if info.Mode().Perm() != 0o600 {
		t.Fatalf("authentication evidence mode = %o, want 600", info.Mode().Perm())
	}
}

func TestConformanceHTTPClientRefusesRedirects(t *testing.T) {
	if err := refuseConformanceRedirect(&http.Request{}, nil); err != http.ErrUseLastResponse {
		t.Fatalf("redirect policy error = %v, want http.ErrUseLastResponse", err)
	}
}

func TestAuthenticationRejectionClassification(t *testing.T) {
	for _, statusCode := range []int{http.StatusUnauthorized, http.StatusForbidden} {
		if !isHTTPAuthenticationRejection(statusCode) {
			t.Fatalf("HTTP %d should be an authentication rejection", statusCode)
		}
	}
	if isHTTPAuthenticationRejection(http.StatusBadRequest) || isHTTPAuthenticationRejection(http.StatusAccepted) {
		t.Fatal("non-authentication HTTP status was accepted")
	}
	for _, code := range []codes.Code{codes.Unauthenticated, codes.PermissionDenied} {
		if !isGRPCAuthenticationRejection(code) {
			t.Fatalf("gRPC %s should be an authentication rejection", code)
		}
	}
	if isGRPCAuthenticationRejection(codes.InvalidArgument) || isGRPCAuthenticationRejection(codes.OK) {
		t.Fatal("non-authentication gRPC status was accepted")
	}
}

func TestInvalidTokenIsDeterministicAndDistinct(t *testing.T) {
	first := conformanceInvalidToken("candidate-2026-08-19-a", strings.Repeat("a", 64))
	second := conformanceInvalidToken("candidate-2026-08-19-a", strings.Repeat("a", 64))
	other := conformanceInvalidToken("candidate-2026-08-19-b", strings.Repeat("a", 64))
	if first != second || first == other || first == strings.Repeat("a", 64) || len(first) != 64 {
		t.Fatal("invalid conformance token identity is not deterministic, distinct, and bounded")
	}
}
