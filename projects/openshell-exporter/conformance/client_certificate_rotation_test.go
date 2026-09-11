// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package conformance

import (
	"fmt"
	"os"
	"path/filepath"
	"testing"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials"
)

const (
	clientCertificateBeforeRotationFile = "external-client-certificate-before-rotation.json"
	clientCertificateRotationFile       = "external-client-certificate-rotation.json"
)

type clientCertificateBeforeRotationEvidence struct {
	SchemaVersion              string                 `json:"schema_version"`
	RunID                      string                 `json:"run_id"`
	CandidateCommit            string                 `json:"candidate_commit"`
	Result                     string                 `json:"result"`
	CheckedAt                  string                 `json:"checked_at"`
	TokenCommitment            string                 `json:"token_commitment"`
	ClientCertificateSHA256    string                 `json:"client_certificate_sha256"`
	ClientCertificateNotBefore string                 `json:"client_certificate_not_before"`
	ClientCertificateNotAfter  string                 `json:"client_certificate_not_after"`
	Checks                     []tokenAcceptanceCheck `json:"checks"`
}

type clientCertificateRotationCheck struct {
	Probe           string `json:"probe"`
	Transport       string `json:"transport"`
	Endpoint        string `json:"endpoint"`
	BeforeObserved  string `json:"before_observed"`
	RetiredObserved string `json:"retired_observed"`
	CurrentObserved string `json:"current_observed"`
}

type clientCertificateRotationEvidence struct {
	SchemaVersion                     string                           `json:"schema_version"`
	RunID                             string                           `json:"run_id"`
	CandidateCommit                   string                           `json:"candidate_commit"`
	Result                            string                           `json:"result"`
	CheckedAt                         string                           `json:"checked_at"`
	BeforeEvidenceSHA256              string                           `json:"before_evidence_sha256"`
	TokenCommitment                   string                           `json:"token_commitment"`
	RetiredClientCertificateSHA256    string                           `json:"retired_client_certificate_sha256"`
	CurrentClientCertificateSHA256    string                           `json:"current_client_certificate_sha256"`
	CurrentClientCertificateNotBefore string                           `json:"current_client_certificate_not_before"`
	CurrentClientCertificateNotAfter  string                           `json:"current_client_certificate_not_after"`
	Checks                            []clientCertificateRotationCheck `json:"checks"`
}

func TestExternalClientCertificateRotation(t *testing.T) {
	phase := os.Getenv("CONFORMANCE_CERTIFICATE_ROTATION_PHASE")
	if phase == "" {
		t.Skip("set CONFORMANCE_CERTIFICATE_ROTATION_PHASE=before or after for the external mTLS rotation gate")
	}
	if phase != "before" && phase != "after" {
		t.Fatal("CONFORMANCE_CERTIFICATE_ROTATION_PHASE must be before or after")
	}

	runID := requiredGateEnv(t, "CONFORMANCE_RUN_ID")
	reportDirectory := requiredGateEnv(t, "CONFORMANCE_REPORT_DIR")
	token := readToken(t)
	probes := rotationProbeMap(t)
	tokenRotation, _, err := readAndValidateTokenRotationEvidence(reportDirectory, runID, probes)
	if err != nil {
		t.Fatal(err)
	}
	currentTokenCommitment := tokenCommitment(runID, token)
	if currentTokenCommitment != tokenRotation.CurrentTokenCommitment {
		t.Fatal("certificate rotation must use the bearer proven current by token-rotation evidence")
	}

	retiredCertPath := requiredGateEnv(t, "CONFORMANCE_RETIRED_CLIENT_CERT_FILE")
	retiredKeyPath := requiredGateEnv(t, "CONFORMANCE_RETIRED_CLIENT_KEY_FILE")
	retiredFingerprint, retiredCertificate := certificateEvidence(t, retiredCertPath, retiredKeyPath)
	now := time.Now().UTC()
	if now.Before(retiredCertificate.NotBefore) || !now.Before(retiredCertificate.NotAfter) {
		t.Fatal("retired client certificate must remain cryptographically valid throughout the rotation proof")
	}

	if phase == "before" {
		evidence := clientCertificateBeforeRotationEvidence{
			SchemaVersion:              backendEvidenceSchemaVersion,
			RunID:                      runID,
			CandidateCommit:            requiredCandidateCommit(t),
			Result:                     "accepted_before_rotation",
			CheckedAt:                  now.Format(time.RFC3339Nano),
			TokenCommitment:            currentTokenCommitment,
			ClientCertificateSHA256:    retiredFingerprint,
			ClientCertificateNotBefore: retiredCertificate.NotBefore.UTC().Format(time.RFC3339Nano),
			ClientCertificateNotAfter:  retiredCertificate.NotAfter.UTC().Format(time.RFC3339Nano),
			Checks: probeClientCertificateAcceptance(
				t, token, retiredCertPath, retiredKeyPath, "certificate-before-rotation",
			),
		}
		if err := validateClientCertificateBeforeRotationEvidence(evidence, runID, probes); err != nil {
			t.Fatal(err)
		}
		path := filepath.Join(reportDirectory, clientCertificateBeforeRotationFile)
		if err := writeRestrictedEvidence(path, evidence); err != nil {
			t.Fatal(err)
		}
		t.Logf("client certificate accepted-before-rotation evidence: %s", path)
		return
	}

	currentCertPath := requiredGateEnv(t, "CONFORMANCE_CLIENT_CERT_FILE")
	currentKeyPath := requiredGateEnv(t, "CONFORMANCE_CLIENT_KEY_FILE")
	currentFingerprint, currentCertificate := certificateEvidence(t, currentCertPath, currentKeyPath)
	if currentFingerprint == retiredFingerprint {
		t.Fatal("retired and current client certificates must be distinct")
	}
	if now.Before(currentCertificate.NotBefore) || !now.Before(currentCertificate.NotAfter) {
		t.Fatal("current client certificate is not valid at probe time")
	}
	before, beforeEncoded, err := readClientCertificateBeforeRotationEvidence(reportDirectory)
	if err != nil {
		t.Fatal(err)
	}
	if err := validateClientCertificateBeforeRotationEvidence(before, runID, probes); err != nil {
		t.Fatal(err)
	}
	if before.ClientCertificateSHA256 != retiredFingerprint {
		t.Fatal("retired certificate does not match the certificate accepted by the before-rotation phase")
	}
	if before.TokenCommitment != currentTokenCommitment {
		t.Fatal("before and after certificate-rotation phases used different bearer credentials")
	}

	evidence := clientCertificateRotationEvidence{
		SchemaVersion:                     backendEvidenceSchemaVersion,
		RunID:                             runID,
		CandidateCommit:                   requiredCandidateCommit(t),
		Result:                            "retired_rejected_current_accepted",
		CheckedAt:                         time.Now().UTC().Format(time.RFC3339Nano),
		BeforeEvidenceSHA256:              evidenceDigest(beforeEncoded),
		TokenCommitment:                   currentTokenCommitment,
		RetiredClientCertificateSHA256:    retiredFingerprint,
		CurrentClientCertificateSHA256:    currentFingerprint,
		CurrentClientCertificateNotBefore: currentCertificate.NotBefore.UTC().Format(time.RFC3339Nano),
		CurrentClientCertificateNotAfter:  currentCertificate.NotAfter.UTC().Format(time.RFC3339Nano),
		Checks: probeRetiredAndCurrentClientCertificates(
			t,
			token,
			retiredCertPath,
			retiredKeyPath,
			currentCertPath,
			currentKeyPath,
			before.Checks,
		),
	}
	if err := validateClientCertificateRotationEvidence(before, beforeEncoded, evidence, runID, probes); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(reportDirectory, clientCertificateRotationFile)
	if err := writeRestrictedEvidence(path, evidence); err != nil {
		t.Fatal(err)
	}
	t.Logf("retired-client-certificate-rejected/current-accepted evidence: %s", path)
}

func probeClientCertificateAcceptance(
	t *testing.T,
	token string,
	certPath string,
	keyPath string,
	suffix string,
) []tokenAcceptanceCheck {
	t.Helper()
	cloudEndpoint := requiredGateEnv(t, "CONFORMANCE_CLOUDEVENTS_URL")
	httpEndpoint := requiredGateEnv(t, "CONFORMANCE_OTLP_HTTP_URL")
	grpcEndpoint := requiredGateEnv(t, "CONFORMANCE_OTLP_GRPC_ENDPOINT")

	cloudStatus := mustHTTPAcceptance(
		t,
		conformanceHTTPClientWithCertificate(t, cloudEndpoint, certPath, keyPath),
		cloudEndpoint,
		"application/cloudevents-batch+json",
		conformanceCloudEventBatch(t, "cloudevents-"+suffix),
		token,
	)
	_, _, httpTraceID, httpSpanID := conformanceIdentity(t, "otlp-http-"+suffix)
	httpBody, err := traceRequest(t, "otlp-http-"+suffix, httpTraceID, httpSpanID).MarshalProto()
	if err != nil {
		t.Fatal(err)
	}
	httpStatus := mustHTTPAcceptance(
		t,
		conformanceHTTPClientWithCertificate(t, httpEndpoint, certPath, keyPath),
		httpEndpoint,
		"application/x-protobuf",
		httpBody,
		token,
	)
	connection := clientCertificateGRPCConnection(t, grpcEndpoint, certPath, keyPath)
	defer func() { _ = connection.Close() }()
	_, _, grpcTraceID, grpcSpanID := conformanceIdentity(t, "otlp-grpc-"+suffix)
	if err := probeGRPCAuthenticationAcceptance(
		connection,
		traceRequest(t, "otlp-grpc-"+suffix, grpcTraceID, grpcSpanID),
		token,
	); err != nil {
		t.Fatal(err)
	}
	return []tokenAcceptanceCheck{
		{Probe: "cloudevents-https", Transport: "cloudevents-batch+json/https", Endpoint: sanitizedEndpoint(t, cloudEndpoint), Observed: fmt.Sprintf("http:%d", cloudStatus)},
		{Probe: "otlp-http", Transport: "otlp/protobuf/https", Endpoint: sanitizedEndpoint(t, httpEndpoint), Observed: fmt.Sprintf("http:%d", httpStatus)},
		{Probe: "otlp-grpc", Transport: "otlp/grpc/tls", Endpoint: grpcEndpoint, Observed: "grpc:OK"},
	}
}

func probeRetiredAndCurrentClientCertificates(
	t *testing.T,
	token string,
	retiredCertPath string,
	retiredKeyPath string,
	currentCertPath string,
	currentKeyPath string,
	beforeChecks []tokenAcceptanceCheck,
) []clientCertificateRotationCheck {
	t.Helper()
	cloudEndpoint := requiredGateEnv(t, "CONFORMANCE_CLOUDEVENTS_URL")
	httpEndpoint := requiredGateEnv(t, "CONFORMANCE_OTLP_HTTP_URL")
	grpcEndpoint := requiredGateEnv(t, "CONFORMANCE_OTLP_GRPC_ENDPOINT")
	before := make(map[string]string, len(beforeChecks))
	for _, check := range beforeChecks {
		before[check.Probe] = check.Observed
	}

	cloudBody := conformanceCloudEventBatch(t, "cloudevents-certificate-after-rotation")
	if err := probeHTTPClientCertificateRejection(
		conformanceHTTPClientWithCertificate(t, cloudEndpoint, retiredCertPath, retiredKeyPath),
		cloudEndpoint,
		"application/cloudevents-batch+json",
		cloudBody,
		token,
	); err != nil {
		t.Fatal(err)
	}
	cloudCurrent := mustHTTPAcceptance(
		t,
		conformanceHTTPClientWithCertificate(t, cloudEndpoint, currentCertPath, currentKeyPath),
		cloudEndpoint,
		"application/cloudevents-batch+json",
		cloudBody,
		token,
	)

	_, _, httpTraceID, httpSpanID := conformanceIdentity(t, "otlp-http-certificate-after-rotation")
	httpBody, err := traceRequest(
		t,
		"otlp-http-certificate-after-rotation",
		httpTraceID,
		httpSpanID,
	).MarshalProto()
	if err != nil {
		t.Fatal(err)
	}
	if err := probeHTTPClientCertificateRejection(
		conformanceHTTPClientWithCertificate(t, httpEndpoint, retiredCertPath, retiredKeyPath),
		httpEndpoint,
		"application/x-protobuf",
		httpBody,
		token,
	); err != nil {
		t.Fatal(err)
	}
	httpCurrent := mustHTTPAcceptance(
		t,
		conformanceHTTPClientWithCertificate(t, httpEndpoint, currentCertPath, currentKeyPath),
		httpEndpoint,
		"application/x-protobuf",
		httpBody,
		token,
	)

	retiredConnection := clientCertificateGRPCConnection(t, grpcEndpoint, retiredCertPath, retiredKeyPath)
	_, _, grpcTraceID, grpcSpanID := conformanceIdentity(t, "otlp-grpc-certificate-after-rotation")
	grpcRequest := traceRequest(t, "otlp-grpc-certificate-after-rotation", grpcTraceID, grpcSpanID)
	if err := probeGRPCClientCertificateRejection(retiredConnection, grpcRequest, token); err != nil {
		_ = retiredConnection.Close()
		t.Fatal(err)
	}
	_ = retiredConnection.Close()
	currentConnection := clientCertificateGRPCConnection(t, grpcEndpoint, currentCertPath, currentKeyPath)
	defer func() { _ = currentConnection.Close() }()
	if err := probeGRPCAuthenticationAcceptance(currentConnection, grpcRequest, token); err != nil {
		t.Fatal(err)
	}

	return []clientCertificateRotationCheck{
		{
			Probe: "cloudevents-https", Transport: "cloudevents-batch+json/https",
			Endpoint: sanitizedEndpoint(t, cloudEndpoint), BeforeObserved: before["cloudevents-https"],
			RetiredObserved: "tls:client_certificate_rejected", CurrentObserved: fmt.Sprintf("http:%d", cloudCurrent),
		},
		{
			Probe: "otlp-http", Transport: "otlp/protobuf/https",
			Endpoint: sanitizedEndpoint(t, httpEndpoint), BeforeObserved: before["otlp-http"],
			RetiredObserved: "tls:client_certificate_rejected", CurrentObserved: fmt.Sprintf("http:%d", httpCurrent),
		},
		{
			Probe: "otlp-grpc", Transport: "otlp/grpc/tls", Endpoint: grpcEndpoint,
			BeforeObserved: before["otlp-grpc"], RetiredObserved: "tls:client_certificate_rejected",
			CurrentObserved: "grpc:OK",
		},
	}
}

func clientCertificateGRPCConnection(
	t *testing.T,
	endpoint string,
	certPath string,
	keyPath string,
) *grpc.ClientConn {
	t.Helper()
	config := clientTLSWithCertificate(t, "https://"+endpoint, certPath, keyPath)
	connection, err := grpc.NewClient(
		endpoint,
		grpc.WithTransportCredentials(credentials.NewTLS(config)),
	)
	if err != nil {
		t.Fatal(err)
	}
	return connection
}

func readClientCertificateBeforeRotationEvidence(
	directory string,
) (clientCertificateBeforeRotationEvidence, []byte, error) {
	var evidence clientCertificateBeforeRotationEvidence
	encoded, err := readJSONEvidence(filepath.Join(directory, clientCertificateBeforeRotationFile), &evidence)
	if err != nil {
		return clientCertificateBeforeRotationEvidence{}, nil, fmt.Errorf("read before-rotation client-certificate evidence: %w", err)
	}
	return evidence, encoded, nil
}

func readClientCertificateRotationEvidence(
	directory string,
) (clientCertificateRotationEvidence, []byte, error) {
	var evidence clientCertificateRotationEvidence
	encoded, err := readJSONEvidence(filepath.Join(directory, clientCertificateRotationFile), &evidence)
	if err != nil {
		return clientCertificateRotationEvidence{}, nil, fmt.Errorf("read client-certificate rotation evidence: %w", err)
	}
	return evidence, encoded, nil
}

func readAndValidateClientCertificateRotationEvidence(
	directory string,
	runID string,
	probes map[string]probeEvidence,
) (clientCertificateRotationEvidence, []byte, error) {
	before, beforeEncoded, err := readClientCertificateBeforeRotationEvidence(directory)
	if err != nil {
		return clientCertificateRotationEvidence{}, nil, err
	}
	rotation, rotationEncoded, err := readClientCertificateRotationEvidence(directory)
	if err != nil {
		return clientCertificateRotationEvidence{}, nil, err
	}
	if err := validateClientCertificateRotationEvidence(before, beforeEncoded, rotation, runID, probes); err != nil {
		return clientCertificateRotationEvidence{}, nil, err
	}
	return rotation, rotationEncoded, nil
}

func validateClientCertificateBeforeRotationEvidence(
	evidence clientCertificateBeforeRotationEvidence,
	runID string,
	probes map[string]probeEvidence,
) error {
	if evidence.SchemaVersion != backendEvidenceSchemaVersion || evidence.RunID != runID {
		return fmt.Errorf("before-rotation client-certificate evidence does not match schema or run")
	}
	expectedCommit, err := probeCandidateCommit(probes)
	if err != nil {
		return err
	}
	if evidence.CandidateCommit != expectedCommit {
		return fmt.Errorf("before-rotation client-certificate evidence belongs to candidate %s, expected %s", evidence.CandidateCommit, expectedCommit)
	}
	if evidence.Result != "accepted_before_rotation" {
		return fmt.Errorf("before-rotation client-certificate evidence does not prove acceptance")
	}
	if err := validateEvidenceTimestamp(evidence.CheckedAt, "before-rotation client-certificate checked_at"); err != nil {
		return err
	}
	if err := validateTokenCommitment(evidence.TokenCommitment); err != nil {
		return err
	}
	if err := validateCertificateFingerprint(evidence.ClientCertificateSHA256); err != nil {
		return err
	}
	checkedAt, _ := time.Parse(time.RFC3339Nano, evidence.CheckedAt)
	notBefore, err := time.Parse(time.RFC3339Nano, evidence.ClientCertificateNotBefore)
	if err != nil {
		return fmt.Errorf("before-rotation client certificate not_before must be RFC3339: %w", err)
	}
	notAfter, err := time.Parse(time.RFC3339Nano, evidence.ClientCertificateNotAfter)
	if err != nil {
		return fmt.Errorf("before-rotation client certificate not_after must be RFC3339: %w", err)
	}
	if checkedAt.Before(notBefore) || !checkedAt.Before(notAfter) {
		return fmt.Errorf("before-rotation client certificate was not valid at checked_at")
	}
	if len(evidence.Checks) != 3 {
		return fmt.Errorf("before-rotation client-certificate evidence must contain exactly three checks")
	}
	seen := make(map[string]struct{}, len(evidence.Checks))
	for _, check := range evidence.Checks {
		probe, ok := probes[check.Probe]
		if !ok || check.Transport != probe.Transport || check.Endpoint != probe.Endpoint {
			return fmt.Errorf("before-rotation client-certificate evidence for %s does not match transport", check.Probe)
		}
		if _, duplicate := seen[check.Probe]; duplicate {
			return fmt.Errorf("before-rotation client-certificate evidence contains duplicate probe %q", check.Probe)
		}
		seen[check.Probe] = struct{}{}
		if !isAcceptedObservation(check.Probe, check.Observed) {
			return fmt.Errorf("before-rotation client-certificate evidence for %s is not accepted", check.Probe)
		}
	}
	return nil
}

func validateClientCertificateRotationEvidence(
	before clientCertificateBeforeRotationEvidence,
	beforeEncoded []byte,
	evidence clientCertificateRotationEvidence,
	runID string,
	probes map[string]probeEvidence,
) error {
	if err := validateClientCertificateBeforeRotationEvidence(before, runID, probes); err != nil {
		return err
	}
	if evidence.SchemaVersion != backendEvidenceSchemaVersion || evidence.RunID != runID {
		return fmt.Errorf("client-certificate rotation evidence does not match schema or run")
	}
	if evidence.CandidateCommit != before.CandidateCommit {
		return fmt.Errorf("client-certificate rotation evidence does not match the before-rotation candidate")
	}
	if evidence.Result != "retired_rejected_current_accepted" {
		return fmt.Errorf("client-certificate rotation evidence does not prove credential transition")
	}
	if err := validateEvidenceTimestamp(evidence.CheckedAt, "client-certificate rotation checked_at"); err != nil {
		return err
	}
	beforeTime, _ := time.Parse(time.RFC3339Nano, before.CheckedAt)
	afterTime, _ := time.Parse(time.RFC3339Nano, evidence.CheckedAt)
	if !afterTime.After(beforeTime) {
		return fmt.Errorf("client-certificate rotation check must occur after before-rotation acceptance")
	}
	if evidence.BeforeEvidenceSHA256 != evidenceDigest(beforeEncoded) {
		return fmt.Errorf("client-certificate rotation evidence does not bind the before-rotation evidence")
	}
	if evidence.TokenCommitment != before.TokenCommitment {
		return fmt.Errorf("client-certificate rotation phases used different bearer credentials")
	}
	if evidence.RetiredClientCertificateSHA256 != before.ClientCertificateSHA256 {
		return fmt.Errorf("client-certificate rotation does not retire the previously accepted certificate")
	}
	if err := validateCertificateFingerprint(evidence.CurrentClientCertificateSHA256); err != nil {
		return err
	}
	if evidence.CurrentClientCertificateSHA256 == evidence.RetiredClientCertificateSHA256 {
		return fmt.Errorf("current and retired client certificates must be distinct")
	}
	retiredNotAfter, _ := time.Parse(time.RFC3339Nano, before.ClientCertificateNotAfter)
	if !afterTime.Before(retiredNotAfter) {
		return fmt.Errorf("retired client certificate was expired at the rotation check")
	}
	currentNotBefore, err := time.Parse(time.RFC3339Nano, evidence.CurrentClientCertificateNotBefore)
	if err != nil {
		return fmt.Errorf("current client certificate not_before must be RFC3339: %w", err)
	}
	currentNotAfter, err := time.Parse(time.RFC3339Nano, evidence.CurrentClientCertificateNotAfter)
	if err != nil {
		return fmt.Errorf("current client certificate not_after must be RFC3339: %w", err)
	}
	if afterTime.Before(currentNotBefore) || !afterTime.Before(currentNotAfter) {
		return fmt.Errorf("current client certificate was not valid at rotation checked_at")
	}
	if len(evidence.Checks) != 3 {
		return fmt.Errorf("client-certificate rotation evidence must contain exactly three checks")
	}
	beforeByProbe := make(map[string]tokenAcceptanceCheck, len(before.Checks))
	for _, check := range before.Checks {
		beforeByProbe[check.Probe] = check
	}
	seen := make(map[string]struct{}, len(evidence.Checks))
	for _, check := range evidence.Checks {
		probe, ok := probes[check.Probe]
		if !ok || check.Transport != probe.Transport || check.Endpoint != probe.Endpoint {
			return fmt.Errorf("client-certificate rotation evidence for %s does not match transport", check.Probe)
		}
		if _, duplicate := seen[check.Probe]; duplicate {
			return fmt.Errorf("client-certificate rotation evidence contains duplicate probe %q", check.Probe)
		}
		seen[check.Probe] = struct{}{}
		if check.BeforeObserved != beforeByProbe[check.Probe].Observed ||
			!isAcceptedObservation(check.Probe, check.BeforeObserved) ||
			check.RetiredObserved != "tls:client_certificate_rejected" ||
			!isAcceptedObservation(check.Probe, check.CurrentObserved) {
			return fmt.Errorf("client-certificate rotation evidence for %s does not prove accepted/rejected/accepted", check.Probe)
		}
	}
	return nil
}

func writeClientCertificateRotationFixtures(
	t *testing.T,
	directory string,
	runID string,
	probes map[string]probeEvidence,
	beforeTime time.Time,
	currentTokenCommitment string,
	currentCertificateFingerprint string,
) {
	t.Helper()
	boundCommit, err := probeCandidateCommit(probes)
	if err != nil {
		t.Fatal(err)
	}
	retiredFingerprint := "sha256:" + repeatHex("6", 64)
	before := clientCertificateBeforeRotationEvidence{
		SchemaVersion:              backendEvidenceSchemaVersion,
		RunID:                      runID,
		CandidateCommit:            boundCommit,
		Result:                     "accepted_before_rotation",
		CheckedAt:                  beforeTime.UTC().Format(time.RFC3339Nano),
		TokenCommitment:            currentTokenCommitment,
		ClientCertificateSHA256:    retiredFingerprint,
		ClientCertificateNotBefore: beforeTime.Add(-time.Hour).UTC().Format(time.RFC3339Nano),
		ClientCertificateNotAfter:  beforeTime.Add(2 * time.Hour).UTC().Format(time.RFC3339Nano),
		Checks: []tokenAcceptanceCheck{
			{Probe: "cloudevents-https", Transport: probes["cloudevents-https"].Transport, Endpoint: probes["cloudevents-https"].Endpoint, Observed: "http:202"},
			{Probe: "otlp-http", Transport: probes["otlp-http"].Transport, Endpoint: probes["otlp-http"].Endpoint, Observed: "http:200"},
			{Probe: "otlp-grpc", Transport: probes["otlp-grpc"].Transport, Endpoint: probes["otlp-grpc"].Endpoint, Observed: "grpc:OK"},
		},
	}
	beforePath := filepath.Join(directory, clientCertificateBeforeRotationFile)
	if err := writeRestrictedEvidence(beforePath, before); err != nil {
		t.Fatal(err)
	}
	beforeEncoded, err := os.ReadFile(beforePath)
	if err != nil {
		t.Fatal(err)
	}
	after := clientCertificateRotationEvidence{
		SchemaVersion:                     backendEvidenceSchemaVersion,
		RunID:                             runID,
		CandidateCommit:                   boundCommit,
		Result:                            "retired_rejected_current_accepted",
		CheckedAt:                         beforeTime.Add(time.Minute).UTC().Format(time.RFC3339Nano),
		BeforeEvidenceSHA256:              evidenceDigest(beforeEncoded),
		TokenCommitment:                   currentTokenCommitment,
		RetiredClientCertificateSHA256:    retiredFingerprint,
		CurrentClientCertificateSHA256:    currentCertificateFingerprint,
		CurrentClientCertificateNotBefore: beforeTime.Add(-time.Hour).UTC().Format(time.RFC3339Nano),
		CurrentClientCertificateNotAfter:  beforeTime.Add(2 * time.Hour).UTC().Format(time.RFC3339Nano),
		Checks: []clientCertificateRotationCheck{
			{Probe: "cloudevents-https", Transport: probes["cloudevents-https"].Transport, Endpoint: probes["cloudevents-https"].Endpoint, BeforeObserved: "http:202", RetiredObserved: "tls:client_certificate_rejected", CurrentObserved: "http:202"},
			{Probe: "otlp-http", Transport: probes["otlp-http"].Transport, Endpoint: probes["otlp-http"].Endpoint, BeforeObserved: "http:200", RetiredObserved: "tls:client_certificate_rejected", CurrentObserved: "http:200"},
			{Probe: "otlp-grpc", Transport: probes["otlp-grpc"].Transport, Endpoint: probes["otlp-grpc"].Endpoint, BeforeObserved: "grpc:OK", RetiredObserved: "tls:client_certificate_rejected", CurrentObserved: "grpc:OK"},
		},
	}
	if err := writeRestrictedEvidence(filepath.Join(directory, clientCertificateRotationFile), after); err != nil {
		t.Fatal(err)
	}
}

func repeatHex(value string, count int) string {
	result := ""
	for len(result) < count {
		result += value
	}
	return result[:count]
}

func TestValidateClientCertificateRotationEvidence(t *testing.T) {
	runID := "candidate-certificate-rotation-2026-a"
	probes := map[string]probeEvidence{
		"cloudevents-https": {CandidateCommit: fixtureCandidateCommit, Probe: "cloudevents-https", Transport: "cloudevents-batch+json/https", Endpoint: "https://receiver.example.test/v1/events"},
		"otlp-http":         {CandidateCommit: fixtureCandidateCommit, Probe: "otlp-http", Transport: "otlp/protobuf/https", Endpoint: "https://otlp.example.test/v1/traces"},
		"otlp-grpc":         {CandidateCommit: fixtureCandidateCommit, Probe: "otlp-grpc", Transport: "otlp/grpc/tls", Endpoint: "otlp.example.test:4317"},
	}
	directory := t.TempDir()
	tokenCommitment := "sha256:" + repeatHex("1", 64)
	currentFingerprint := "sha256:" + repeatHex("3", 64)
	writeClientCertificateRotationFixtures(
		t,
		directory,
		runID,
		probes,
		time.Now().Add(-2*time.Minute),
		tokenCommitment,
		currentFingerprint,
	)
	before, beforeEncoded, err := readClientCertificateBeforeRotationEvidence(directory)
	if err != nil {
		t.Fatal(err)
	}
	after, _, err := readClientCertificateRotationEvidence(directory)
	if err != nil {
		t.Fatal(err)
	}
	if err := validateClientCertificateRotationEvidence(before, beforeEncoded, after, runID, probes); err != nil {
		t.Fatal(err)
	}

	tests := []struct {
		name   string
		mutate func(*clientCertificateRotationEvidence)
	}{
		{
			name: "same certificate",
			mutate: func(value *clientCertificateRotationEvidence) {
				value.CurrentClientCertificateSHA256 = value.RetiredClientCertificateSHA256
			},
		},
		{
			name: "different bearer",
			mutate: func(value *clientCertificateRotationEvidence) {
				value.TokenCommitment = "sha256:" + repeatHex("2", 64)
			},
		},
		{
			name: "retired accepted",
			mutate: func(value *clientCertificateRotationEvidence) {
				value.Checks[0].RetiredObserved = "http:202"
			},
		},
		{
			name: "current rejected",
			mutate: func(value *clientCertificateRotationEvidence) {
				value.Checks[1].CurrentObserved = "http:403"
			},
		},
		{
			name: "wrong before hash",
			mutate: func(value *clientCertificateRotationEvidence) {
				value.BeforeEvidenceSHA256 = "sha256:" + repeatHex("9", 64)
			},
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			invalid := after
			invalid.Checks = append([]clientCertificateRotationCheck(nil), after.Checks...)
			test.mutate(&invalid)
			if err := validateClientCertificateRotationEvidence(before, beforeEncoded, invalid, runID, probes); err == nil {
				t.Fatal("client-certificate rotation validation accepted invalid evidence")
			}
		})
	}

	expiredBefore := before
	expiredBefore.ClientCertificateNotAfter = after.CheckedAt
	if err := validateClientCertificateRotationEvidence(expiredBefore, beforeEncoded, after, runID, probes); err == nil {
		t.Fatal("client-certificate rotation validation accepted a retired certificate that expired at rotation")
	}
}
