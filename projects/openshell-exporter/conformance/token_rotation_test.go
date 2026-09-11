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
	"strconv"
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

const (
	tokenRotationBeforeFile = "external-token-before-rotation.json"
	tokenRotationAfterFile  = "external-token-rotation.json"
)

type tokenAcceptanceCheck struct {
	Probe     string `json:"probe"`
	Transport string `json:"transport"`
	Endpoint  string `json:"endpoint"`
	Observed  string `json:"observed"`
}

type tokenBeforeRotationEvidence struct {
	SchemaVersion   string                 `json:"schema_version"`
	RunID           string                 `json:"run_id"`
	CandidateCommit string                 `json:"candidate_commit"`
	Result          string                 `json:"result"`
	CheckedAt       string                 `json:"checked_at"`
	TokenCommitment string                 `json:"token_commitment"`
	Checks          []tokenAcceptanceCheck `json:"checks"`
}

type tokenRotationCheck struct {
	Probe           string `json:"probe"`
	Transport       string `json:"transport"`
	Endpoint        string `json:"endpoint"`
	BeforeObserved  string `json:"before_observed"`
	RetiredObserved string `json:"retired_observed"`
	CurrentObserved string `json:"current_observed"`
}

type tokenRotationEvidence struct {
	SchemaVersion          string               `json:"schema_version"`
	RunID                  string               `json:"run_id"`
	CandidateCommit        string               `json:"candidate_commit"`
	Result                 string               `json:"result"`
	CheckedAt              string               `json:"checked_at"`
	BeforeEvidenceSHA256   string               `json:"before_evidence_sha256"`
	RetiredTokenCommitment string               `json:"retired_token_commitment"`
	CurrentTokenCommitment string               `json:"current_token_commitment"`
	Checks                 []tokenRotationCheck `json:"checks"`
}

func TestExternalBearerTokenRotation(t *testing.T) {
	phase := os.Getenv("CONFORMANCE_TOKEN_ROTATION_PHASE")
	if phase == "" {
		t.Skip("set CONFORMANCE_TOKEN_ROTATION_PHASE=before or after for the external bearer rotation gate")
	}
	if phase != "before" && phase != "after" {
		t.Fatal("CONFORMANCE_TOKEN_ROTATION_PHASE must be before or after")
	}

	runID := requiredGateEnv(t, "CONFORMANCE_RUN_ID")
	reportDirectory := requiredGateEnv(t, "CONFORMANCE_REPORT_DIR")
	currentToken := readToken(t)
	probes := rotationProbeMap(t)

	if phase == "before" {
		evidence := tokenBeforeRotationEvidence{
			SchemaVersion:   backendEvidenceSchemaVersion,
			RunID:           runID,
			CandidateCommit: requiredCandidateCommit(t),
			Result:          "accepted_before_rotation",
			CheckedAt:       time.Now().UTC().Format(time.RFC3339Nano),
			TokenCommitment: tokenCommitment(runID, currentToken),
			Checks:          probeCurrentTokenAcceptance(t, currentToken, "token-before-rotation"),
		}
		if err := validateTokenBeforeRotationEvidence(evidence, runID, probes); err != nil {
			t.Fatal(err)
		}
		path := filepath.Join(reportDirectory, tokenRotationBeforeFile)
		if err := writeRestrictedEvidence(path, evidence); err != nil {
			t.Fatal(err)
		}
		t.Logf("accepted-before-rotation evidence: %s", path)
		return
	}

	retiredToken := readConformanceTokenFile(t, requiredGateEnv(t, "CONFORMANCE_RETIRED_TOKEN_FILE"))
	if retiredToken == currentToken {
		t.Fatal("retired and current bearer tokens must be distinct")
	}
	before, beforeEncoded, err := readTokenBeforeRotationEvidence(reportDirectory)
	if err != nil {
		t.Fatal(err)
	}
	if err := validateTokenBeforeRotationEvidence(before, runID, probes); err != nil {
		t.Fatal(err)
	}
	if before.TokenCommitment != tokenCommitment(runID, retiredToken) {
		t.Fatal("retired token does not match the credential accepted by the before-rotation phase")
	}

	evidence := tokenRotationEvidence{
		SchemaVersion:          backendEvidenceSchemaVersion,
		RunID:                  runID,
		CandidateCommit:        requiredCandidateCommit(t),
		Result:                 "retired_rejected_current_accepted",
		CheckedAt:              time.Now().UTC().Format(time.RFC3339Nano),
		BeforeEvidenceSHA256:   evidenceDigest(beforeEncoded),
		RetiredTokenCommitment: tokenCommitment(runID, retiredToken),
		CurrentTokenCommitment: tokenCommitment(runID, currentToken),
		Checks:                 probeRetiredAndCurrentTokens(t, retiredToken, currentToken, before.Checks),
	}
	if err := validateTokenRotationEvidence(before, beforeEncoded, evidence, runID, probes); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(reportDirectory, tokenRotationAfterFile)
	if err := writeRestrictedEvidence(path, evidence); err != nil {
		t.Fatal(err)
	}
	t.Logf("retired-rejected/current-accepted evidence: %s", path)
}

func rotationProbeMap(t *testing.T) map[string]probeEvidence {
	t.Helper()
	boundCommit := requiredCandidateCommit(t)
	return map[string]probeEvidence{
		"cloudevents-https": {
			CandidateCommit: boundCommit,
			Probe:           "cloudevents-https", Transport: "cloudevents-batch+json/https",
			Endpoint: sanitizedEndpoint(t, requiredGateEnv(t, "CONFORMANCE_CLOUDEVENTS_URL")),
		},
		"otlp-http": {
			CandidateCommit: boundCommit,
			Probe:           "otlp-http", Transport: "otlp/protobuf/https",
			Endpoint: sanitizedEndpoint(t, requiredGateEnv(t, "CONFORMANCE_OTLP_HTTP_URL")),
		},
		"otlp-grpc": {
			CandidateCommit: boundCommit,
			Probe:           "otlp-grpc", Transport: "otlp/grpc/tls",
			Endpoint: requiredGateEnv(t, "CONFORMANCE_OTLP_GRPC_ENDPOINT"),
		},
	}
}

func probeCurrentTokenAcceptance(t *testing.T, token, suffix string) []tokenAcceptanceCheck {
	t.Helper()
	cloudEndpoint := requiredGateEnv(t, "CONFORMANCE_CLOUDEVENTS_URL")
	httpEndpoint := requiredGateEnv(t, "CONFORMANCE_OTLP_HTTP_URL")
	grpcEndpoint := requiredGateEnv(t, "CONFORMANCE_OTLP_GRPC_ENDPOINT")

	cloudStatus := mustHTTPAcceptance(
		t, conformanceHTTPClient(t, cloudEndpoint), cloudEndpoint,
		"application/cloudevents-batch+json", conformanceCloudEventBatch(t, "cloudevents-"+suffix), token,
	)
	_, _, httpTraceID, httpSpanID := conformanceIdentity(t, "otlp-http-"+suffix)
	httpPayload, err := traceRequest(t, "otlp-http-"+suffix, httpTraceID, httpSpanID).MarshalProto()
	if err != nil {
		t.Fatal(err)
	}
	httpStatus := mustHTTPAcceptance(
		t, conformanceHTTPClient(t, httpEndpoint), httpEndpoint, "application/x-protobuf", httpPayload, token,
	)
	connection := rotationGRPCConnection(t, grpcEndpoint)
	defer func() { _ = connection.Close() }()
	_, _, grpcTraceID, grpcSpanID := conformanceIdentity(t, "otlp-grpc-"+suffix)
	if err := probeGRPCAuthenticationAcceptance(
		connection, traceRequest(t, "otlp-grpc-"+suffix, grpcTraceID, grpcSpanID), token,
	); err != nil {
		t.Fatal(err)
	}
	return []tokenAcceptanceCheck{
		{Probe: "cloudevents-https", Transport: "cloudevents-batch+json/https", Endpoint: sanitizedEndpoint(t, cloudEndpoint), Observed: fmt.Sprintf("http:%d", cloudStatus)},
		{Probe: "otlp-http", Transport: "otlp/protobuf/https", Endpoint: sanitizedEndpoint(t, httpEndpoint), Observed: fmt.Sprintf("http:%d", httpStatus)},
		{Probe: "otlp-grpc", Transport: "otlp/grpc/tls", Endpoint: grpcEndpoint, Observed: "grpc:OK"},
	}
}

func probeRetiredAndCurrentTokens(
	t *testing.T,
	retiredToken, currentToken string,
	beforeChecks []tokenAcceptanceCheck,
) []tokenRotationCheck {
	t.Helper()
	cloudEndpoint := requiredGateEnv(t, "CONFORMANCE_CLOUDEVENTS_URL")
	httpEndpoint := requiredGateEnv(t, "CONFORMANCE_OTLP_HTTP_URL")
	grpcEndpoint := requiredGateEnv(t, "CONFORMANCE_OTLP_GRPC_ENDPOINT")
	before := make(map[string]string, len(beforeChecks))
	for _, check := range beforeChecks {
		before[check.Probe] = check.Observed
	}

	cloudBody := conformanceCloudEventBatch(t, "cloudevents-token-after-rotation")
	cloudRetired := mustHTTPRejection(
		t, conformanceHTTPClient(t, cloudEndpoint), cloudEndpoint,
		"application/cloudevents-batch+json", cloudBody, retiredToken,
	)
	cloudCurrent := mustHTTPAcceptance(
		t, conformanceHTTPClient(t, cloudEndpoint), cloudEndpoint,
		"application/cloudevents-batch+json", cloudBody, currentToken,
	)

	_, _, httpTraceID, httpSpanID := conformanceIdentity(t, "otlp-http-token-after-rotation")
	httpBody, err := traceRequest(t, "otlp-http-token-after-rotation", httpTraceID, httpSpanID).MarshalProto()
	if err != nil {
		t.Fatal(err)
	}
	httpRetired := mustHTTPRejection(
		t, conformanceHTTPClient(t, httpEndpoint), httpEndpoint, "application/x-protobuf", httpBody, retiredToken,
	)
	httpCurrent := mustHTTPAcceptance(
		t, conformanceHTTPClient(t, httpEndpoint), httpEndpoint, "application/x-protobuf", httpBody, currentToken,
	)

	connection := rotationGRPCConnection(t, grpcEndpoint)
	defer func() { _ = connection.Close() }()
	_, _, grpcTraceID, grpcSpanID := conformanceIdentity(t, "otlp-grpc-token-after-rotation")
	grpcRequest := traceRequest(t, "otlp-grpc-token-after-rotation", grpcTraceID, grpcSpanID)
	grpcRetired, err := probeGRPCAuthenticationRejection(connection, grpcRequest, retiredToken)
	if err != nil {
		t.Fatal(err)
	}
	if err := probeGRPCAuthenticationAcceptance(connection, grpcRequest, currentToken); err != nil {
		t.Fatal(err)
	}

	return []tokenRotationCheck{
		{
			Probe: "cloudevents-https", Transport: "cloudevents-batch+json/https",
			Endpoint: sanitizedEndpoint(t, cloudEndpoint), BeforeObserved: before["cloudevents-https"],
			RetiredObserved: fmt.Sprintf("http:%d", cloudRetired), CurrentObserved: fmt.Sprintf("http:%d", cloudCurrent),
		},
		{
			Probe: "otlp-http", Transport: "otlp/protobuf/https",
			Endpoint: sanitizedEndpoint(t, httpEndpoint), BeforeObserved: before["otlp-http"],
			RetiredObserved: fmt.Sprintf("http:%d", httpRetired), CurrentObserved: fmt.Sprintf("http:%d", httpCurrent),
		},
		{
			Probe: "otlp-grpc", Transport: "otlp/grpc/tls", Endpoint: grpcEndpoint,
			BeforeObserved: before["otlp-grpc"], RetiredObserved: "grpc:" + grpcRetired.String(),
			CurrentObserved: "grpc:OK",
		},
	}
}

func rotationGRPCConnection(t *testing.T, endpoint string) *grpc.ClientConn {
	t.Helper()
	connection, err := grpc.NewClient(
		endpoint,
		grpc.WithTransportCredentials(credentials.NewTLS(clientTLS(t, "https://"+endpoint))),
	)
	if err != nil {
		t.Fatal(err)
	}
	return connection
}

func mustHTTPAcceptance(
	t *testing.T,
	client *http.Client,
	endpoint, contentType string,
	body []byte,
	token string,
) int {
	t.Helper()
	statusCode, err := probeHTTPAuthenticationAcceptance(client, endpoint, contentType, body, token)
	if err != nil {
		t.Fatal(err)
	}
	return statusCode
}

func mustHTTPRejection(
	t *testing.T,
	client *http.Client,
	endpoint, contentType string,
	body []byte,
	token string,
) int {
	t.Helper()
	statusCode, err := probeHTTPAuthenticationRejection(client, endpoint, contentType, body, token)
	if err != nil {
		t.Fatal(err)
	}
	return statusCode
}

func probeHTTPAuthenticationAcceptance(
	client *http.Client,
	endpoint, contentType string,
	body []byte,
	token string,
) (int, error) {
	request, err := http.NewRequestWithContext(context.Background(), http.MethodPost, endpoint, bytes.NewReader(body))
	if err != nil {
		return 0, err
	}
	request.Header.Set("Content-Type", contentType)
	request.Header.Set("Authorization", "Bearer "+token)
	response, err := client.Do(request)
	if err != nil {
		return 0, err
	}
	_, copyErr := io.Copy(io.Discard, response.Body)
	closeErr := response.Body.Close()
	if copyErr != nil {
		return response.StatusCode, copyErr
	}
	if closeErr != nil {
		return response.StatusCode, closeErr
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return response.StatusCode, fmt.Errorf("current bearer credential returned HTTP %d, want 2xx", response.StatusCode)
	}
	return response.StatusCode, nil
}

func probeGRPCAuthenticationAcceptance(
	connection *grpc.ClientConn,
	request ptraceotlp.ExportRequest,
	token string,
) error {
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	ctx = metadata.AppendToOutgoingContext(ctx, "authorization", "Bearer "+token)
	if _, err := ptraceotlp.NewGRPCClient(connection).Export(ctx, request); err != nil {
		return fmt.Errorf("current bearer credential was not accepted by OTLP gRPC: %w", err)
	}
	return nil
}

func tokenCommitment(runID, token string) string {
	sum := sha256.Sum256([]byte("openshell-conformance-token-commitment:" + runID + ":" + token))
	return "sha256:" + hex.EncodeToString(sum[:])
}

func readConformanceTokenFile(t *testing.T, path string) string {
	t.Helper()
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

func readTokenBeforeRotationEvidence(directory string) (tokenBeforeRotationEvidence, []byte, error) {
	var evidence tokenBeforeRotationEvidence
	encoded, err := readJSONEvidence(filepath.Join(directory, tokenRotationBeforeFile), &evidence)
	if err != nil {
		return tokenBeforeRotationEvidence{}, nil, fmt.Errorf("read before-rotation token evidence: %w", err)
	}
	return evidence, encoded, nil
}

func readTokenRotationEvidence(directory string) (tokenRotationEvidence, []byte, error) {
	var evidence tokenRotationEvidence
	encoded, err := readJSONEvidence(filepath.Join(directory, tokenRotationAfterFile), &evidence)
	if err != nil {
		return tokenRotationEvidence{}, nil, fmt.Errorf("read token rotation evidence: %w", err)
	}
	return evidence, encoded, nil
}

func readAndValidateTokenRotationEvidence(
	directory string,
	runID string,
	probes map[string]probeEvidence,
) (tokenRotationEvidence, []byte, error) {
	before, beforeEncoded, err := readTokenBeforeRotationEvidence(directory)
	if err != nil {
		return tokenRotationEvidence{}, nil, err
	}
	rotation, rotationEncoded, err := readTokenRotationEvidence(directory)
	if err != nil {
		return tokenRotationEvidence{}, nil, err
	}
	if err := validateTokenRotationEvidence(before, beforeEncoded, rotation, runID, probes); err != nil {
		return tokenRotationEvidence{}, nil, err
	}
	return rotation, rotationEncoded, nil
}

func validateTokenBeforeRotationEvidence(
	evidence tokenBeforeRotationEvidence,
	runID string,
	probes map[string]probeEvidence,
) error {
	if evidence.SchemaVersion != backendEvidenceSchemaVersion || evidence.RunID != runID {
		return fmt.Errorf("before-rotation token evidence does not match schema or run")
	}
	expectedCommit, err := probeCandidateCommit(probes)
	if err != nil {
		return err
	}
	if evidence.CandidateCommit != expectedCommit {
		return fmt.Errorf("before-rotation token evidence belongs to candidate %s, expected %s", evidence.CandidateCommit, expectedCommit)
	}
	if evidence.Result != "accepted_before_rotation" {
		return fmt.Errorf("before-rotation token evidence does not prove acceptance")
	}
	if err := validateEvidenceTimestamp(evidence.CheckedAt, "before-rotation checked_at"); err != nil {
		return err
	}
	if err := validateTokenCommitment(evidence.TokenCommitment); err != nil {
		return err
	}
	if len(evidence.Checks) != 3 {
		return fmt.Errorf("before-rotation token evidence must contain exactly three checks")
	}
	seen := make(map[string]struct{}, len(evidence.Checks))
	for _, check := range evidence.Checks {
		probe, ok := probes[check.Probe]
		if !ok || check.Transport != probe.Transport || check.Endpoint != probe.Endpoint {
			return fmt.Errorf("before-rotation token evidence for %s does not match transport", check.Probe)
		}
		if _, duplicate := seen[check.Probe]; duplicate {
			return fmt.Errorf("before-rotation token evidence contains duplicate probe %q", check.Probe)
		}
		seen[check.Probe] = struct{}{}
		if !isAcceptedObservation(check.Probe, check.Observed) {
			return fmt.Errorf("before-rotation token evidence for %s is not accepted", check.Probe)
		}
	}
	return nil
}

func validateTokenRotationEvidence(
	before tokenBeforeRotationEvidence,
	beforeEncoded []byte,
	evidence tokenRotationEvidence,
	runID string,
	probes map[string]probeEvidence,
) error {
	if err := validateTokenBeforeRotationEvidence(before, runID, probes); err != nil {
		return err
	}
	if evidence.SchemaVersion != backendEvidenceSchemaVersion || evidence.RunID != runID {
		return fmt.Errorf("token rotation evidence does not match schema or run")
	}
	if evidence.CandidateCommit != before.CandidateCommit {
		return fmt.Errorf("token rotation evidence does not match the before-rotation candidate")
	}
	if evidence.Result != "retired_rejected_current_accepted" {
		return fmt.Errorf("token rotation evidence does not prove credential transition")
	}
	if err := validateEvidenceTimestamp(evidence.CheckedAt, "token rotation checked_at"); err != nil {
		return err
	}
	beforeTime, _ := time.Parse(time.RFC3339Nano, before.CheckedAt)
	afterTime, _ := time.Parse(time.RFC3339Nano, evidence.CheckedAt)
	if !afterTime.After(beforeTime) {
		return fmt.Errorf("token rotation check must occur after before-rotation acceptance")
	}
	if evidence.BeforeEvidenceSHA256 != evidenceDigest(beforeEncoded) {
		return fmt.Errorf("token rotation evidence does not bind the before-rotation evidence")
	}
	if err := validateTokenCommitment(evidence.RetiredTokenCommitment); err != nil {
		return err
	}
	if err := validateTokenCommitment(evidence.CurrentTokenCommitment); err != nil {
		return err
	}
	if evidence.RetiredTokenCommitment != before.TokenCommitment ||
		evidence.CurrentTokenCommitment == evidence.RetiredTokenCommitment {
		return fmt.Errorf("token rotation commitments do not prove a distinct current credential")
	}
	if len(evidence.Checks) != 3 {
		return fmt.Errorf("token rotation evidence must contain exactly three checks")
	}
	beforeByProbe := make(map[string]tokenAcceptanceCheck, len(before.Checks))
	for _, check := range before.Checks {
		beforeByProbe[check.Probe] = check
	}
	seen := make(map[string]struct{}, len(evidence.Checks))
	for _, check := range evidence.Checks {
		probe, ok := probes[check.Probe]
		if !ok || check.Transport != probe.Transport || check.Endpoint != probe.Endpoint {
			return fmt.Errorf("token rotation evidence for %s does not match transport", check.Probe)
		}
		if _, duplicate := seen[check.Probe]; duplicate {
			return fmt.Errorf("token rotation evidence contains duplicate probe %q", check.Probe)
		}
		seen[check.Probe] = struct{}{}
		if check.BeforeObserved != beforeByProbe[check.Probe].Observed ||
			!isAcceptedObservation(check.Probe, check.BeforeObserved) ||
			!isRejectedObservation(check.Probe, check.RetiredObserved) ||
			!isAcceptedObservation(check.Probe, check.CurrentObserved) {
			return fmt.Errorf("token rotation evidence for %s does not prove accepted/rejected/accepted", check.Probe)
		}
	}
	return nil
}

func probeCandidateCommit(probes map[string]probeEvidence) (string, error) {
	expected := ""
	for _, name := range []string{"cloudevents-https", "otlp-http", "otlp-grpc"} {
		probe, ok := probes[name]
		if !ok {
			return "", fmt.Errorf("missing %s transport probe", name)
		}
		if err := validateCandidateCommit(probe.CandidateCommit); err != nil {
			return "", fmt.Errorf("%s transport probe: %w", name, err)
		}
		if expected == "" {
			expected = probe.CandidateCommit
		} else if probe.CandidateCommit != expected {
			return "", fmt.Errorf("transport probes belong to different candidate commits")
		}
	}
	return expected, nil
}

func validateTokenCommitment(value string) error {
	if !strings.HasPrefix(value, "sha256:") {
		return fmt.Errorf("token commitment must use sha256")
	}
	return validateHexIdentifier(strings.TrimPrefix(value, "sha256:"), 64, "token commitment")
}

func isAcceptedObservation(probe, observed string) bool {
	if probe == "otlp-grpc" {
		return observed == "grpc:OK"
	}
	encodedStatus, ok := strings.CutPrefix(observed, "http:")
	if !ok {
		return false
	}
	statusCode, err := strconv.Atoi(encodedStatus)
	if err != nil {
		return false
	}
	return statusCode >= 200 && statusCode < 300
}

func isRejectedObservation(probe, observed string) bool {
	if probe == "otlp-grpc" {
		return observed == "grpc:Unauthenticated" || observed == "grpc:PermissionDenied"
	}
	return observed == "http:401" || observed == "http:403"
}

func writeRestrictedEvidence(path string, evidence any) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		return err
	}
	encoded, err := json.MarshalIndent(evidence, "", "  ")
	if err != nil {
		return err
	}
	file, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if err != nil {
		return err
	}
	removeIncomplete := true
	defer func() {
		if removeIncomplete {
			_ = file.Close()
			_ = os.Remove(path)
		}
	}()
	if _, err := file.Write(append(encoded, '\n')); err != nil {
		return err
	}
	if err := file.Sync(); err != nil {
		return err
	}
	if err := file.Close(); err != nil {
		return err
	}
	removeIncomplete = false
	return nil
}

func writeTokenRotationFixtures(
	t *testing.T,
	directory, runID string,
	probes map[string]probeEvidence,
	beforeTime time.Time,
) {
	t.Helper()
	boundCommit, err := probeCandidateCommit(probes)
	if err != nil {
		t.Fatal(err)
	}
	retiredCommitment := tokenCommitment(runID, strings.Repeat("r", 64))
	currentCommitment := tokenCommitment(runID, strings.Repeat("c", 64))
	before := tokenBeforeRotationEvidence{
		SchemaVersion:   backendEvidenceSchemaVersion,
		CandidateCommit: boundCommit,
		RunID:           runID, Result: "accepted_before_rotation",
		CheckedAt: beforeTime.UTC().Format(time.RFC3339Nano), TokenCommitment: retiredCommitment,
		Checks: []tokenAcceptanceCheck{
			{Probe: "cloudevents-https", Transport: probes["cloudevents-https"].Transport, Endpoint: probes["cloudevents-https"].Endpoint, Observed: "http:202"},
			{Probe: "otlp-http", Transport: probes["otlp-http"].Transport, Endpoint: probes["otlp-http"].Endpoint, Observed: "http:200"},
			{Probe: "otlp-grpc", Transport: probes["otlp-grpc"].Transport, Endpoint: probes["otlp-grpc"].Endpoint, Observed: "grpc:OK"},
		},
	}
	beforePath := filepath.Join(directory, tokenRotationBeforeFile)
	if err := writeRestrictedEvidence(beforePath, before); err != nil {
		t.Fatal(err)
	}
	beforeEncoded, err := os.ReadFile(beforePath)
	if err != nil {
		t.Fatal(err)
	}
	after := tokenRotationEvidence{
		SchemaVersion:   backendEvidenceSchemaVersion,
		CandidateCommit: boundCommit,
		RunID:           runID, Result: "retired_rejected_current_accepted",
		CheckedAt:            beforeTime.Add(time.Minute).UTC().Format(time.RFC3339Nano),
		BeforeEvidenceSHA256: evidenceDigest(beforeEncoded), RetiredTokenCommitment: retiredCommitment,
		CurrentTokenCommitment: currentCommitment,
		Checks: []tokenRotationCheck{
			{Probe: "cloudevents-https", Transport: probes["cloudevents-https"].Transport, Endpoint: probes["cloudevents-https"].Endpoint, BeforeObserved: "http:202", RetiredObserved: "http:401", CurrentObserved: "http:202"},
			{Probe: "otlp-http", Transport: probes["otlp-http"].Transport, Endpoint: probes["otlp-http"].Endpoint, BeforeObserved: "http:200", RetiredObserved: "http:403", CurrentObserved: "http:200"},
			{Probe: "otlp-grpc", Transport: probes["otlp-grpc"].Transport, Endpoint: probes["otlp-grpc"].Endpoint, BeforeObserved: "grpc:OK", RetiredObserved: "grpc:Unauthenticated", CurrentObserved: "grpc:OK"},
		},
	}
	if err := writeRestrictedEvidence(filepath.Join(directory, tokenRotationAfterFile), after); err != nil {
		t.Fatal(err)
	}
}

func TestTokenCommitmentIsScopedAndCredentialFree(t *testing.T) {
	token := strings.Repeat("s", 64)
	first := tokenCommitment("run-a-1234", token)
	if first != tokenCommitment("run-a-1234", token) ||
		first == tokenCommitment("run-b-1234", token) ||
		strings.Contains(first, token) {
		t.Fatal("token commitment is not deterministic, run-scoped, and credential-free")
	}
	if err := validateTokenCommitment(first); err != nil {
		t.Fatal(err)
	}
}

func TestHTTPTokenRotationPair(t *testing.T) {
	retired := strings.Repeat("r", 64)
	current := strings.Repeat("c", 64)
	server := httptest.NewTLSServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		switch request.Header.Get("Authorization") {
		case "Bearer " + retired:
			writer.WriteHeader(http.StatusUnauthorized)
		case "Bearer " + current:
			writer.WriteHeader(http.StatusAccepted)
		default:
			writer.WriteHeader(http.StatusForbidden)
		}
	}))
	defer server.Close()
	mustHTTPRejection(t, server.Client(), server.URL, "application/x-protobuf", []byte("body"), retired)
	mustHTTPAcceptance(t, server.Client(), server.URL, "application/x-protobuf", []byte("body"), current)
}

type rotatingTraceServer struct {
	ptraceotlp.UnimplementedGRPCServer
	retired string
	current string
}

func (server *rotatingTraceServer) Export(
	ctx context.Context,
	_ ptraceotlp.ExportRequest,
) (ptraceotlp.ExportResponse, error) {
	values := metadata.ValueFromIncomingContext(ctx, "authorization")
	if len(values) != 1 {
		return ptraceotlp.NewExportResponse(), status.Error(codes.InvalidArgument, "authorization metadata missing")
	}
	switch values[0] {
	case "Bearer " + server.retired:
		return ptraceotlp.NewExportResponse(), status.Error(codes.Unauthenticated, "retired bearer")
	case "Bearer " + server.current:
		return ptraceotlp.NewExportResponse(), nil
	default:
		return ptraceotlp.NewExportResponse(), status.Error(codes.PermissionDenied, "unknown bearer")
	}
}

func TestGRPCTokenRotationPair(t *testing.T) {
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	retired := strings.Repeat("r", 64)
	current := strings.Repeat("c", 64)
	server := grpc.NewServer()
	ptraceotlp.RegisterGRPCServer(server, &rotatingTraceServer{retired: retired, current: current})
	go func() { _ = server.Serve(listener) }()
	defer server.Stop()
	connection, err := grpc.NewClient(listener.Addr().String(), grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = connection.Close() }()
	if _, err := probeGRPCAuthenticationRejection(connection, ptraceotlp.NewExportRequest(), retired); err != nil {
		t.Fatal(err)
	}
	if err := probeGRPCAuthenticationAcceptance(connection, ptraceotlp.NewExportRequest(), current); err != nil {
		t.Fatal(err)
	}
}

func TestValidateTokenRotationEvidence(t *testing.T) {
	runID := "candidate-rotation-2026-a"
	probes := map[string]probeEvidence{
		"cloudevents-https": {CandidateCommit: fixtureCandidateCommit, Probe: "cloudevents-https", Transport: "cloudevents-batch+json/https", Endpoint: "https://receiver.example.test/v1/events"},
		"otlp-http":         {CandidateCommit: fixtureCandidateCommit, Probe: "otlp-http", Transport: "otlp/protobuf/https", Endpoint: "https://otlp.example.test/v1/traces"},
		"otlp-grpc":         {CandidateCommit: fixtureCandidateCommit, Probe: "otlp-grpc", Transport: "otlp/grpc/tls", Endpoint: "otlp.example.test:4317"},
	}
	directory := t.TempDir()
	writeTokenRotationFixtures(t, directory, runID, probes, time.Now().Add(-time.Minute))
	before, beforeEncoded, err := readTokenBeforeRotationEvidence(directory)
	if err != nil {
		t.Fatal(err)
	}
	after, _, err := readTokenRotationEvidence(directory)
	if err != nil {
		t.Fatal(err)
	}
	if err := validateTokenRotationEvidence(before, beforeEncoded, after, runID, probes); err != nil {
		t.Fatal(err)
	}

	invalid := after
	invalid.CurrentTokenCommitment = invalid.RetiredTokenCommitment
	if err := validateTokenRotationEvidence(before, beforeEncoded, invalid, runID, probes); err == nil {
		t.Fatal("rotation validation accepted the same current and retired credential")
	}
	invalid = after
	invalid.Checks = append([]tokenRotationCheck(nil), after.Checks...)
	invalid.Checks[0].RetiredObserved = "http:202"
	if err := validateTokenRotationEvidence(before, beforeEncoded, invalid, runID, probes); err == nil {
		t.Fatal("rotation validation accepted a retired credential")
	}
	invalid = after
	invalid.Checks = append([]tokenRotationCheck(nil), after.Checks...)
	invalid.Checks[0].CurrentObserved = "http:202junk"
	if err := validateTokenRotationEvidence(before, beforeEncoded, invalid, runID, probes); err == nil {
		t.Fatal("rotation validation accepted a malformed HTTP observation")
	}
}

func TestTokenRotationEvidenceIsRestrictedAndCredentialFree(t *testing.T) {
	path := filepath.Join(t.TempDir(), tokenRotationAfterFile)
	secret := strings.Repeat("secret-token-", 4)
	evidence := tokenRotationEvidence{
		SchemaVersion:   backendEvidenceSchemaVersion,
		CandidateCommit: fixtureCandidateCommit,
		RunID:           "candidate-rotation-2026-a", Result: "retired_rejected_current_accepted",
		CheckedAt:              time.Now().UTC().Format(time.RFC3339Nano),
		RetiredTokenCommitment: tokenCommitment("candidate-rotation-2026-a", secret),
		CurrentTokenCommitment: tokenCommitment("candidate-rotation-2026-a", strings.Repeat("c", 64)),
	}
	if err := writeRestrictedEvidence(path, evidence); err != nil {
		t.Fatal(err)
	}
	encoded, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(encoded), secret) || strings.Contains(strings.ToLower(string(encoded)), "authorization") {
		t.Fatal("token rotation evidence contains credential material")
	}
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if info.Mode().Perm() != 0o600 {
		t.Fatalf("token rotation evidence mode = %o, want 600", info.Mode().Perm())
	}
	if err := writeRestrictedEvidence(path, evidence); err == nil {
		t.Fatal("token rotation evidence was overwritten")
	}
}
