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
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/metadata"
)

const expiredCredentialEvidenceFile = "external-expired-credentials.json"

type expiredCredentialCheck struct {
	Probe                            string `json:"probe"`
	Transport                        string `json:"transport"`
	Endpoint                         string `json:"endpoint"`
	CurrentObserved                  string `json:"current_observed"`
	ExpiredBearerObserved            string `json:"expired_bearer_observed"`
	ExpiredClientCertificateObserved string `json:"expired_client_certificate_observed"`
}

type expiredCredentialEvidence struct {
	SchemaVersion                     string                   `json:"schema_version"`
	RunID                             string                   `json:"run_id"`
	CandidateCommit                   string                   `json:"candidate_commit"`
	Result                            string                   `json:"result"`
	CheckedAt                         string                   `json:"checked_at"`
	CurrentTokenCommitment            string                   `json:"current_token_commitment"`
	ExpiredTokenCommitment            string                   `json:"expired_token_commitment"`
	CurrentClientCertificateSHA256    string                   `json:"current_client_certificate_sha256"`
	CurrentClientCertificateNotBefore string                   `json:"current_client_certificate_not_before"`
	CurrentClientCertificateNotAfter  string                   `json:"current_client_certificate_not_after"`
	ExpiredClientCertificateSHA256    string                   `json:"expired_client_certificate_sha256"`
	ExpiredClientCertificateNotAfter  string                   `json:"expired_client_certificate_not_after"`
	Checks                            []expiredCredentialCheck `json:"checks"`
}

func TestExternalExpiredCredentialRejection(t *testing.T) {
	if os.Getenv("CONFORMANCE_VERIFY_EXPIRED_CREDENTIALS") != "true" {
		t.Skip("set CONFORMANCE_VERIFY_EXPIRED_CREDENTIALS=true for the expired bearer and mTLS credential gate")
	}

	runID := requiredOptInCredentialEnv(t, "CONFORMANCE_RUN_ID")
	reportDirectory := requiredOptInCredentialEnv(t, "CONFORMANCE_REPORT_DIR")
	currentToken := readToken(t)
	expiredToken := readConformanceTokenFile(t, requiredOptInCredentialEnv(t, "CONFORMANCE_EXPIRED_TOKEN_FILE"))
	if currentToken == expiredToken {
		t.Fatal("current and expired bearer tokens must be distinct")
	}

	currentCertPath := requiredOptInCredentialEnv(t, "CONFORMANCE_CLIENT_CERT_FILE")
	currentKeyPath := requiredOptInCredentialEnv(t, "CONFORMANCE_CLIENT_KEY_FILE")
	expiredCertPath := requiredOptInCredentialEnv(t, "CONFORMANCE_EXPIRED_CLIENT_CERT_FILE")
	expiredKeyPath := requiredOptInCredentialEnv(t, "CONFORMANCE_EXPIRED_CLIENT_KEY_FILE")
	currentFingerprint, currentCertificate := certificateEvidence(t, currentCertPath, currentKeyPath)
	expiredFingerprint, expiredCertificate := certificateEvidence(t, expiredCertPath, expiredKeyPath)
	if currentFingerprint == expiredFingerprint {
		t.Fatal("current and expired client certificates must be distinct")
	}
	now := time.Now().UTC()
	if now.Before(currentCertificate.NotBefore) || !now.Before(currentCertificate.NotAfter) {
		t.Fatal("current client certificate is not valid at probe time")
	}
	if !expiredCertificate.NotAfter.Before(now) {
		t.Fatal("CONFORMANCE_EXPIRED_CLIENT_CERT_FILE must contain an already-expired leaf certificate")
	}

	probes := rotationProbeMap(t)
	rotation, _, err := readAndValidateTokenRotationEvidence(reportDirectory, runID, probes)
	if err != nil {
		t.Fatal(err)
	}
	currentTokenCommitment := tokenCommitment(runID, currentToken)
	if currentTokenCommitment != rotation.CurrentTokenCommitment {
		t.Fatal("expired-credential proof must use the bearer proven current by token rotation")
	}
	certificateRotation, _, err := readAndValidateClientCertificateRotationEvidence(
		reportDirectory,
		runID,
		probes,
	)
	if err != nil {
		t.Fatal(err)
	}
	if currentFingerprint != certificateRotation.CurrentClientCertificateSHA256 {
		t.Fatal("expired-credential proof must use the client certificate proven current by rotation")
	}
	expiredTokenCommitment := tokenCommitment(runID, expiredToken)
	if expiredTokenCommitment != rotation.RetiredTokenCommitment {
		t.Fatal("expired bearer token must be the credential proven accepted and then retired by the token-rotation evidence")
	}
	currentChecks := probeCurrentTokenAcceptance(t, currentToken, "expired-credentials-current")
	expiredBearerChecks := probeExpiredBearerRejection(t, expiredToken)
	expiredCertificateChecks := probeExpiredClientCertificateRejection(
		t, currentToken, expiredCertPath, expiredKeyPath,
	)
	checkedAt := time.Now().UTC()
	evidence := expiredCredentialEvidence{
		SchemaVersion:                     backendEvidenceSchemaVersion,
		RunID:                             runID,
		CandidateCommit:                   requiredCandidateCommit(t),
		Result:                            "expired_credentials_rejected_current_accepted",
		CheckedAt:                         checkedAt.Format(time.RFC3339Nano),
		CurrentTokenCommitment:            currentTokenCommitment,
		ExpiredTokenCommitment:            expiredTokenCommitment,
		CurrentClientCertificateSHA256:    currentFingerprint,
		CurrentClientCertificateNotBefore: currentCertificate.NotBefore.UTC().Format(time.RFC3339Nano),
		CurrentClientCertificateNotAfter:  currentCertificate.NotAfter.UTC().Format(time.RFC3339Nano),
		ExpiredClientCertificateSHA256:    expiredFingerprint,
		ExpiredClientCertificateNotAfter:  expiredCertificate.NotAfter.UTC().Format(time.RFC3339Nano),
		Checks: mergeExpiredCredentialChecks(
			currentChecks,
			expiredBearerChecks,
			expiredCertificateChecks,
		),
	}
	if err := validateExpiredCredentialEvidence(evidence, runID, probes); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(reportDirectory, expiredCredentialEvidenceFile)
	if err := writeRestrictedEvidence(path, evidence); err != nil {
		t.Fatal(err)
	}
	t.Logf("expired bearer and mTLS credential rejection evidence: %s", path)
}

func probeExpiredBearerRejection(t *testing.T, expiredToken string) []authenticationCheck {
	t.Helper()
	cloudEndpoint := requiredOptInCredentialEnv(t, "CONFORMANCE_CLOUDEVENTS_URL")
	httpEndpoint := requiredOptInCredentialEnv(t, "CONFORMANCE_OTLP_HTTP_URL")
	grpcEndpoint := requiredOptInCredentialEnv(t, "CONFORMANCE_OTLP_GRPC_ENDPOINT")

	cloudStatus := mustHTTPRejection(
		t,
		conformanceHTTPClient(t, cloudEndpoint),
		cloudEndpoint,
		"application/cloudevents-batch+json",
		conformanceCloudEventBatch(t, "cloudevents-expired-bearer"),
		expiredToken,
	)
	_, _, httpTraceID, httpSpanID := conformanceIdentity(t, "otlp-http-expired-bearer")
	httpBody, err := traceRequest(t, "otlp-http-expired-bearer", httpTraceID, httpSpanID).MarshalProto()
	if err != nil {
		t.Fatal(err)
	}
	httpStatus := mustHTTPRejection(
		t,
		conformanceHTTPClient(t, httpEndpoint),
		httpEndpoint,
		"application/x-protobuf",
		httpBody,
		expiredToken,
	)
	connection := rotationGRPCConnection(t, grpcEndpoint)
	defer func() { _ = connection.Close() }()
	_, _, grpcTraceID, grpcSpanID := conformanceIdentity(t, "otlp-grpc-expired-bearer")
	grpcCode, err := probeGRPCAuthenticationRejection(
		connection,
		traceRequest(t, "otlp-grpc-expired-bearer", grpcTraceID, grpcSpanID),
		expiredToken,
	)
	if err != nil {
		t.Fatal(err)
	}
	return []authenticationCheck{
		{Probe: "cloudevents-https", Transport: "cloudevents-batch+json/https", Endpoint: sanitizedEndpoint(t, cloudEndpoint), Observed: fmt.Sprintf("http:%d", cloudStatus)},
		{Probe: "otlp-http", Transport: "otlp/protobuf/https", Endpoint: sanitizedEndpoint(t, httpEndpoint), Observed: fmt.Sprintf("http:%d", httpStatus)},
		{Probe: "otlp-grpc", Transport: "otlp/grpc/tls", Endpoint: grpcEndpoint, Observed: "grpc:" + grpcCode.String()},
	}
}

func probeExpiredClientCertificateRejection(
	t *testing.T,
	currentToken string,
	expiredCertPath string,
	expiredKeyPath string,
) []authenticationCheck {
	t.Helper()
	cloudEndpoint := requiredOptInCredentialEnv(t, "CONFORMANCE_CLOUDEVENTS_URL")
	httpEndpoint := requiredOptInCredentialEnv(t, "CONFORMANCE_OTLP_HTTP_URL")
	grpcEndpoint := requiredOptInCredentialEnv(t, "CONFORMANCE_OTLP_GRPC_ENDPOINT")

	cloudClient := conformanceHTTPClientWithCertificate(t, cloudEndpoint, expiredCertPath, expiredKeyPath)
	if err := probeHTTPClientCertificateRejection(
		cloudClient,
		cloudEndpoint,
		"application/cloudevents-batch+json",
		conformanceCloudEventBatch(t, "cloudevents-expired-client-certificate"),
		currentToken,
	); err != nil {
		t.Fatal(err)
	}

	_, _, httpTraceID, httpSpanID := conformanceIdentity(t, "otlp-http-expired-client-certificate")
	httpBody, err := traceRequest(
		t,
		"otlp-http-expired-client-certificate",
		httpTraceID,
		httpSpanID,
	).MarshalProto()
	if err != nil {
		t.Fatal(err)
	}
	httpClient := conformanceHTTPClientWithCertificate(t, httpEndpoint, expiredCertPath, expiredKeyPath)
	if err := probeHTTPClientCertificateRejection(
		httpClient,
		httpEndpoint,
		"application/x-protobuf",
		httpBody,
		currentToken,
	); err != nil {
		t.Fatal(err)
	}

	config := clientTLSWithCertificate(t, "https://"+grpcEndpoint, expiredCertPath, expiredKeyPath)
	connection, err := grpc.NewClient(grpcEndpoint, grpc.WithTransportCredentials(credentials.NewTLS(config)))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = connection.Close() }()
	_, _, grpcTraceID, grpcSpanID := conformanceIdentity(t, "otlp-grpc-expired-client-certificate")
	if err := probeGRPCClientCertificateRejection(
		connection,
		traceRequest(t, "otlp-grpc-expired-client-certificate", grpcTraceID, grpcSpanID),
		currentToken,
	); err != nil {
		t.Fatal(err)
	}

	return []authenticationCheck{
		{Probe: "cloudevents-https", Transport: "cloudevents-batch+json/https", Endpoint: sanitizedEndpoint(t, cloudEndpoint), Observed: "tls:client_certificate_rejected"},
		{Probe: "otlp-http", Transport: "otlp/protobuf/https", Endpoint: sanitizedEndpoint(t, httpEndpoint), Observed: "tls:client_certificate_rejected"},
		{Probe: "otlp-grpc", Transport: "otlp/grpc/tls", Endpoint: grpcEndpoint, Observed: "tls:client_certificate_rejected"},
	}
}

func probeHTTPClientCertificateRejection(
	client *http.Client,
	endpoint string,
	contentType string,
	body []byte,
	token string,
) error {
	request, err := http.NewRequestWithContext(context.Background(), http.MethodPost, endpoint, bytes.NewReader(body))
	if err != nil {
		return err
	}
	request.Header.Set("Content-Type", contentType)
	request.Header.Set("Authorization", "Bearer "+token)
	response, err := client.Do(request)
	if err == nil {
		_, copyErr := io.Copy(io.Discard, response.Body)
		closeErr := response.Body.Close()
		if copyErr != nil {
			return copyErr
		}
		if closeErr != nil {
			return closeErr
		}
		return fmt.Errorf("expired client certificate reached HTTP response %d", response.StatusCode)
	}
	if !isTLSClientCertificateRejection(err) {
		return fmt.Errorf("expired client certificate failed without a TLS rejection: %w", err)
	}
	return nil
}

func probeGRPCClientCertificateRejection(
	connection *grpc.ClientConn,
	request ptraceotlp.ExportRequest,
	token string,
) error {
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	ctx = metadata.AppendToOutgoingContext(ctx, "authorization", "Bearer "+token)
	_, err := ptraceotlp.NewGRPCClient(connection).Export(ctx, request)
	if err == nil {
		return fmt.Errorf("expired client certificate was accepted by OTLP gRPC")
	}
	if !isTLSClientCertificateRejection(err) {
		return fmt.Errorf("expired client certificate failed without a TLS rejection: %w", err)
	}
	return nil
}

func isTLSClientCertificateRejection(err error) bool {
	if err == nil {
		return false
	}
	message := strings.ToLower(err.Error())
	for _, marker := range []string{
		"bad certificate",
		"certificate expired",
		"certificate has expired",
		"certificate required",
		"expired certificate",
		"remote error: tls",
		"tls: handshake",
		"tls handshake",
		"authentication handshake failed",
	} {
		if strings.Contains(message, marker) {
			return true
		}
	}
	return false
}

func conformanceHTTPClientWithCertificate(
	t *testing.T,
	endpoint string,
	certPath string,
	keyPath string,
) *http.Client {
	t.Helper()
	return &http.Client{
		Transport: &http.Transport{
			TLSClientConfig: clientTLSWithCertificate(t, endpoint, certPath, keyPath),
		},
		Timeout:       15 * time.Second,
		CheckRedirect: refuseConformanceRedirect,
	}
}

func clientTLSWithCertificate(
	t *testing.T,
	endpoint string,
	certPath string,
	keyPath string,
) *tls.Config {
	t.Helper()
	config := clientTLS(t, endpoint).Clone()
	certificate, err := tls.LoadX509KeyPair(certPath, keyPath)
	if err != nil {
		t.Fatal(err)
	}
	config.Certificates = []tls.Certificate{certificate}
	return config
}

func certificateEvidence(t *testing.T, certPath string, keyPath string) (string, *x509.Certificate) {
	t.Helper()
	pair, err := tls.LoadX509KeyPair(certPath, keyPath)
	if err != nil {
		t.Fatal(err)
	}
	if len(pair.Certificate) == 0 {
		t.Fatal("client certificate file contains no leaf certificate")
	}
	leaf, err := x509.ParseCertificate(pair.Certificate[0])
	if err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256(pair.Certificate[0])
	return "sha256:" + hex.EncodeToString(sum[:]), leaf
}

func mergeExpiredCredentialChecks(
	current []tokenAcceptanceCheck,
	expiredBearer []authenticationCheck,
	expiredCertificate []authenticationCheck,
) []expiredCredentialCheck {
	currentByProbe := make(map[string]tokenAcceptanceCheck, len(current))
	expiredBearerByProbe := make(map[string]authenticationCheck, len(expiredBearer))
	expiredCertificateByProbe := make(map[string]authenticationCheck, len(expiredCertificate))
	for _, check := range current {
		currentByProbe[check.Probe] = check
	}
	for _, check := range expiredBearer {
		expiredBearerByProbe[check.Probe] = check
	}
	for _, check := range expiredCertificate {
		expiredCertificateByProbe[check.Probe] = check
	}
	result := make([]expiredCredentialCheck, 0, 3)
	for _, probe := range []string{"cloudevents-https", "otlp-http", "otlp-grpc"} {
		result = append(result, expiredCredentialCheck{
			Probe:                            probe,
			Transport:                        currentByProbe[probe].Transport,
			Endpoint:                         currentByProbe[probe].Endpoint,
			CurrentObserved:                  currentByProbe[probe].Observed,
			ExpiredBearerObserved:            expiredBearerByProbe[probe].Observed,
			ExpiredClientCertificateObserved: expiredCertificateByProbe[probe].Observed,
		})
	}
	return result
}

func readAndValidateExpiredCredentialEvidence(
	directory string,
	runID string,
	probes map[string]probeEvidence,
) (expiredCredentialEvidence, []byte, error) {
	var evidence expiredCredentialEvidence
	encoded, err := readJSONEvidence(filepath.Join(directory, expiredCredentialEvidenceFile), &evidence)
	if err != nil {
		return expiredCredentialEvidence{}, nil, fmt.Errorf("read expired-credential evidence: %w", err)
	}
	if err := validateExpiredCredentialEvidence(evidence, runID, probes); err != nil {
		return expiredCredentialEvidence{}, nil, err
	}
	return evidence, encoded, nil
}

func validateExpiredCredentialEvidence(
	evidence expiredCredentialEvidence,
	runID string,
	probes map[string]probeEvidence,
) error {
	if evidence.SchemaVersion != backendEvidenceSchemaVersion || evidence.RunID != runID {
		return fmt.Errorf("expired-credential evidence does not match schema or run")
	}
	expectedCommit, err := probeCandidateCommit(probes)
	if err != nil {
		return err
	}
	if evidence.CandidateCommit != expectedCommit {
		return fmt.Errorf(
			"expired-credential evidence belongs to candidate %s, expected %s",
			evidence.CandidateCommit,
			expectedCommit,
		)
	}
	if evidence.Result != "expired_credentials_rejected_current_accepted" {
		return fmt.Errorf("expired-credential evidence does not prove current acceptance and expired rejection")
	}
	if err := validateEvidenceTimestamp(evidence.CheckedAt, "expired-credential checked_at"); err != nil {
		return err
	}
	checkedAt, _ := time.Parse(time.RFC3339Nano, evidence.CheckedAt)
	currentNotBefore, err := time.Parse(time.RFC3339Nano, evidence.CurrentClientCertificateNotBefore)
	if err != nil {
		return fmt.Errorf("current client certificate not_before must be RFC3339: %w", err)
	}
	currentNotAfter, err := time.Parse(time.RFC3339Nano, evidence.CurrentClientCertificateNotAfter)
	if err != nil {
		return fmt.Errorf("current client certificate not_after must be RFC3339: %w", err)
	}
	expiredNotAfter, err := time.Parse(time.RFC3339Nano, evidence.ExpiredClientCertificateNotAfter)
	if err != nil {
		return fmt.Errorf("expired client certificate not_after must be RFC3339: %w", err)
	}
	if checkedAt.Before(currentNotBefore) || !checkedAt.Before(currentNotAfter) {
		return fmt.Errorf("current client certificate was not valid at checked_at")
	}
	if !expiredNotAfter.Before(checkedAt) {
		return fmt.Errorf("expired client certificate was not expired at checked_at")
	}
	if err := validateTokenCommitment(evidence.CurrentTokenCommitment); err != nil {
		return err
	}
	if err := validateTokenCommitment(evidence.ExpiredTokenCommitment); err != nil {
		return err
	}
	if evidence.CurrentTokenCommitment == evidence.ExpiredTokenCommitment {
		return fmt.Errorf("current and expired bearer commitments must be distinct")
	}
	if err := validateCertificateFingerprint(evidence.CurrentClientCertificateSHA256); err != nil {
		return err
	}
	if err := validateCertificateFingerprint(evidence.ExpiredClientCertificateSHA256); err != nil {
		return err
	}
	if evidence.CurrentClientCertificateSHA256 == evidence.ExpiredClientCertificateSHA256 {
		return fmt.Errorf("current and expired client certificate fingerprints must be distinct")
	}
	if len(evidence.Checks) != 3 {
		return fmt.Errorf("expired-credential evidence must contain exactly three transport checks")
	}
	seen := make(map[string]struct{}, len(evidence.Checks))
	for _, check := range evidence.Checks {
		probe, ok := probes[check.Probe]
		if !ok || check.Transport != probe.Transport || check.Endpoint != probe.Endpoint {
			return fmt.Errorf("expired-credential evidence for %s does not match transport", check.Probe)
		}
		if _, duplicate := seen[check.Probe]; duplicate {
			return fmt.Errorf("expired-credential evidence contains duplicate probe %q", check.Probe)
		}
		seen[check.Probe] = struct{}{}
		if !isAcceptedObservation(check.Probe, check.CurrentObserved) ||
			!isRejectedObservation(check.Probe, check.ExpiredBearerObserved) ||
			check.ExpiredClientCertificateObserved != "tls:client_certificate_rejected" {
			return fmt.Errorf(
				"expired-credential evidence for %s does not prove accepted/rejected/rejected",
				check.Probe,
			)
		}
	}
	return nil
}

func validateCertificateFingerprint(value string) error {
	if !strings.HasPrefix(value, "sha256:") {
		return fmt.Errorf("certificate fingerprint must use sha256")
	}
	return validateHexIdentifier(strings.TrimPrefix(value, "sha256:"), 64, "certificate fingerprint")
}

func requiredOptInCredentialEnv(t *testing.T, name string) string {
	t.Helper()
	value := os.Getenv(name)
	if value == "" {
		t.Fatalf("%s is required when CONFORMANCE_VERIFY_EXPIRED_CREDENTIALS=true", name)
	}
	return value
}

func TestTLSClientCertificateRejectionClassification(t *testing.T) {
	for _, message := range []string{
		"remote error: tls: bad certificate",
		"transport: authentication handshake failed: tls: certificate required",
		"x509: certificate has expired",
	} {
		if !isTLSClientCertificateRejection(fmt.Errorf("%s", message)) {
			t.Fatalf("did not classify TLS client certificate rejection %q", message)
		}
	}
	for _, message := range []string{
		"context deadline exceeded",
		"connection refused",
		"unexpected HTTP 503",
	} {
		if isTLSClientCertificateRejection(fmt.Errorf("%s", message)) {
			t.Fatalf("misclassified non-TLS failure %q", message)
		}
	}
}

func writeExpiredCredentialFixture(
	t *testing.T,
	directory string,
	runID string,
	checkedAt time.Time,
) {
	t.Helper()
	rotation, _, err := readTokenRotationEvidence(directory)
	if err != nil {
		t.Fatal(err)
	}
	writeJSONFixture(t, filepath.Join(directory, expiredCredentialEvidenceFile), expiredCredentialEvidence{
		SchemaVersion:                     backendEvidenceSchemaVersion,
		RunID:                             runID,
		CandidateCommit:                   fixtureCandidateCommit,
		Result:                            "expired_credentials_rejected_current_accepted",
		CheckedAt:                         checkedAt.Format(time.RFC3339Nano),
		CurrentTokenCommitment:            rotation.CurrentTokenCommitment,
		ExpiredTokenCommitment:            rotation.RetiredTokenCommitment,
		CurrentClientCertificateSHA256:    "sha256:" + strings.Repeat("3", 64),
		CurrentClientCertificateNotBefore: checkedAt.Add(-time.Hour).Format(time.RFC3339Nano),
		CurrentClientCertificateNotAfter:  checkedAt.Add(time.Hour).Format(time.RFC3339Nano),
		ExpiredClientCertificateSHA256:    "sha256:" + strings.Repeat("4", 64),
		ExpiredClientCertificateNotAfter:  checkedAt.Add(-time.Hour).Format(time.RFC3339Nano),
		Checks: []expiredCredentialCheck{
			{
				Probe: "cloudevents-https", Transport: "cloudevents-batch+json/https",
				Endpoint:        "https://receiver.example.test/v1/events",
				CurrentObserved: "http:202", ExpiredBearerObserved: "http:401",
				ExpiredClientCertificateObserved: "tls:client_certificate_rejected",
			},
			{
				Probe: "otlp-http", Transport: "otlp/protobuf/https",
				Endpoint:        "https://otlp.example.test/v1/traces",
				CurrentObserved: "http:200", ExpiredBearerObserved: "http:403",
				ExpiredClientCertificateObserved: "tls:client_certificate_rejected",
			},
			{
				Probe: "otlp-grpc", Transport: "otlp/grpc/tls",
				Endpoint:        "otlp.example.test:4317",
				CurrentObserved: "grpc:OK", ExpiredBearerObserved: "grpc:Unauthenticated",
				ExpiredClientCertificateObserved: "tls:client_certificate_rejected",
			},
		},
	})
}
