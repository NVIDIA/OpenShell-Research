// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package conformance

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

const backendEvidenceSchemaVersion = "1.0"

type cloudEventsBackendEvidence struct {
	SchemaVersion    string                  `json:"schema_version"`
	RunID            string                  `json:"run_id"`
	Receiver         string                  `json:"receiver"`
	QueriedAt        string                  `json:"queried_at"`
	DeduplicatedRows int                     `json:"deduplicated_rows"`
	DeliveryAttempts int                     `json:"delivery_attempts"`
	Event            cloudEventBackendRecord `json:"event"`
}

type cloudEventBackendRecord struct {
	SpecVersion      string `json:"specversion"`
	ID               string `json:"id"`
	Source           string `json:"source"`
	Type             string `json:"type"`
	UnknownExtension struct {
		Retain bool `json:"retain"`
	} `json:"unknown_extension"`
	Data struct {
		Original struct {
			ConformanceProbe           bool   `json:"conformance_probe"`
			ConformanceRunID           string `json:"conformance_run_id"`
			ConformanceCandidateCommit string `json:"conformance_candidate_commit"`
		} `json:"original"`
	} `json:"data"`
}

type otlpBackendEvidence struct {
	SchemaVersion string          `json:"schema_version"`
	RunID         string          `json:"run_id"`
	Backend       string          `json:"backend"`
	QueriedAt     string          `json:"queried_at"`
	Transport     string          `json:"transport"`
	MatchingSpans int             `json:"matching_spans"`
	Span          otlpBackendSpan `json:"span"`
}

type otlpBackendSpan struct {
	TraceID    string         `json:"trace_id"`
	SpanID     string         `json:"span_id"`
	Attributes map[string]any `json:"attributes"`
}

type verifiedDestinationProof struct {
	Probe            string `json:"probe"`
	Backend          string `json:"backend"`
	QueriedAt        string `json:"queried_at"`
	EvidenceSHA256   string `json:"evidence_sha256"`
	CloudEventSource string `json:"cloudevent_source,omitempty"`
	CloudEventID     string `json:"cloudevent_id,omitempty"`
	DeduplicatedRows int    `json:"deduplicated_rows,omitempty"`
	DeliveryAttempts int    `json:"delivery_attempts,omitempty"`
	TraceID          string `json:"trace_id,omitempty"`
	SpanID           string `json:"span_id,omitempty"`
	MatchingSpans    int    `json:"matching_spans,omitempty"`
}

type externalDestinationVerification struct {
	SchemaVersion                     string                           `json:"schema_version"`
	RunID                             string                           `json:"run_id"`
	CandidateCommit                   string                           `json:"candidate_commit"`
	Result                            string                           `json:"result"`
	VerifiedAt                        string                           `json:"verified_at"`
	Proofs                            []verifiedDestinationProof       `json:"proofs"`
	AuthenticationEvidenceSHA256      string                           `json:"authentication_evidence_sha256"`
	AuthenticationCheckedAt           string                           `json:"authentication_checked_at"`
	AuthenticationChecks              []authenticationCheck            `json:"authentication_checks"`
	TokenRotationEvidenceSHA256       string                           `json:"token_rotation_evidence_sha256"`
	TokenRotationCheckedAt            string                           `json:"token_rotation_checked_at"`
	TokenRotationChecks               []tokenRotationCheck             `json:"token_rotation_checks"`
	CertificateRotationEvidenceSHA256 string                           `json:"certificate_rotation_evidence_sha256"`
	CertificateRotationCheckedAt      string                           `json:"certificate_rotation_checked_at"`
	CertificateRotationChecks         []clientCertificateRotationCheck `json:"certificate_rotation_checks"`
	ExpiredCredentialEvidenceSHA256   string                           `json:"expired_credential_evidence_sha256"`
	ExpiredCredentialCheckedAt        string                           `json:"expired_credential_checked_at"`
	ExpiredCredentialChecks           []expiredCredentialCheck         `json:"expired_credential_checks"`
	ResponseSemanticsEvidenceSHA256   string                           `json:"response_semantics_evidence_sha256"`
	ResponseSemanticsQueriedAt        string                           `json:"response_semantics_queried_at"`
	ResponseSemanticsScenarioCount    int                              `json:"response_semantics_scenario_count"`
	ResponseSemanticsRetryableCount   int                              `json:"response_semantics_retryable_count"`
	ResponseSemanticsPermanentCount   int                              `json:"response_semantics_permanent_count"`
	OTLPGRPCResponseEvidenceSHA256    string                           `json:"otlp_grpc_response_evidence_sha256"`
	OTLPGRPCResponseQueriedAt         string                           `json:"otlp_grpc_response_queried_at"`
	OTLPGRPCResponseScenarioCount     int                              `json:"otlp_grpc_response_scenario_count"`
	OTLPGRPCResponseRetryableCount    int                              `json:"otlp_grpc_response_retryable_count"`
	OTLPGRPCResponsePermanentCount    int                              `json:"otlp_grpc_response_permanent_count"`
	CloudEventsLimitsEvidenceSHA256   string                           `json:"cloudevents_limits_evidence_sha256"`
	CloudEventsLimitsQueriedAt        string                           `json:"cloudevents_limits_queried_at"`
	CloudEventsLimitsCountEvents      int                              `json:"cloudevents_limits_count_events"`
	CloudEventsLimitsByteEvents       int                              `json:"cloudevents_limits_byte_events"`
	CloudEventsLimitsRequests         int                              `json:"cloudevents_limits_requests"`
	CloudEventsLimitsRecoveries       int                              `json:"cloudevents_limits_recoveries"`
	Limitations                       []string                         `json:"limitations"`
}

func TestVerifyExternalBackendEvidence(t *testing.T) {
	if os.Getenv("CONFORMANCE_VERIFY_BACKEND_EVIDENCE") != "true" {
		t.Skip("set CONFORMANCE_VERIFY_BACKEND_EVIDENCE=true to verify external backend query exports")
	}
	runID := requiredVerificationEnv(t, "CONFORMANCE_RUN_ID")
	reportDirectory := requiredVerificationEnv(t, "CONFORMANCE_REPORT_DIR")
	backendDirectory := requiredVerificationEnv(t, "CONFORMANCE_BACKEND_EVIDENCE_DIR")
	boundCommit := requiredCandidateCommit(t)

	verification, err := verifyExternalBackendEvidence(reportDirectory, backendDirectory, runID)
	if err != nil {
		t.Fatal(err)
	}
	if verification.CandidateCommit != boundCommit {
		t.Fatalf("external evidence belongs to candidate %s, conformance binary is %s", verification.CandidateCommit, boundCommit)
	}
	path := filepath.Join(reportDirectory, "external-destination-verification.json")
	if err := writeVerificationEvidence(path, verification); err != nil {
		t.Fatal(err)
	}
	t.Logf("verified independent backend evidence: %s", path)
}

func verifyExternalBackendEvidence(
	reportDirectory string,
	backendDirectory string,
	runID string,
) (externalDestinationVerification, error) {
	if err := validateSafeEvidenceLabel(runID, "run_id"); err != nil {
		return externalDestinationVerification{}, err
	}

	cloudProbe, err := readProbeEvidence(reportDirectory, "cloudevents-https")
	if err != nil {
		return externalDestinationVerification{}, err
	}
	httpProbe, err := readProbeEvidence(reportDirectory, "otlp-http")
	if err != nil {
		return externalDestinationVerification{}, err
	}
	grpcProbe, err := readProbeEvidence(reportDirectory, "otlp-grpc")
	if err != nil {
		return externalDestinationVerification{}, err
	}
	if err := validateTransportProbe(cloudProbe, runID, "cloudevents-https", 2); err != nil {
		return externalDestinationVerification{}, err
	}
	if err := validateTransportProbe(httpProbe, runID, "otlp-http", 1); err != nil {
		return externalDestinationVerification{}, err
	}
	if err := validateTransportProbe(grpcProbe, runID, "otlp-grpc", 1); err != nil {
		return externalDestinationVerification{}, err
	}
	if httpProbe.TraceID == grpcProbe.TraceID || httpProbe.SpanID == grpcProbe.SpanID {
		return externalDestinationVerification{}, fmt.Errorf("OTLP HTTP and gRPC probes must have distinct trace and span identities")
	}
	authentication, authenticationEncoded, err := readAuthenticationEvidence(reportDirectory)
	if err != nil {
		return externalDestinationVerification{}, err
	}
	transportProbes := map[string]probeEvidence{
		"cloudevents-https": cloudProbe,
		"otlp-http":         httpProbe,
		"otlp-grpc":         grpcProbe,
	}
	bundleCommit, err := probeCandidateCommit(transportProbes)
	if err != nil {
		return externalDestinationVerification{}, err
	}
	if err := validateAuthenticationEvidence(authentication, runID, transportProbes); err != nil {
		return externalDestinationVerification{}, err
	}

	tokenRotation, tokenRotationEncoded, err := readAndValidateTokenRotationEvidence(reportDirectory, runID, transportProbes)
	if err != nil {
		return externalDestinationVerification{}, err
	}
	certificateRotation, certificateRotationEncoded, err := readAndValidateClientCertificateRotationEvidence(
		reportDirectory,
		runID,
		transportProbes,
	)
	if err != nil {
		return externalDestinationVerification{}, err
	}
	if certificateRotation.TokenCommitment != tokenRotation.CurrentTokenCommitment {
		return externalDestinationVerification{}, fmt.Errorf("client-certificate rotation did not use the bearer proven current by token rotation")
	}
	expiredCredentials, expiredCredentialsEncoded, err := readAndValidateExpiredCredentialEvidence(reportDirectory, runID, transportProbes)
	if err != nil {
		return externalDestinationVerification{}, err
	}
	if expiredCredentials.ExpiredTokenCommitment != tokenRotation.RetiredTokenCommitment {
		return externalDestinationVerification{}, fmt.Errorf("expired bearer evidence is not bound to the credential proven retired by token rotation")
	}
	if expiredCredentials.CurrentTokenCommitment != tokenRotation.CurrentTokenCommitment {
		return externalDestinationVerification{}, fmt.Errorf("expired-credential evidence did not use the bearer proven current by token rotation")
	}
	if expiredCredentials.CurrentClientCertificateSHA256 != certificateRotation.CurrentClientCertificateSHA256 {
		return externalDestinationVerification{}, fmt.Errorf("expired-credential evidence did not use the client certificate proven current by rotation")
	}

	_, _, responseSemantics, err := readAndValidateCloudEventsResponseSemanticsEvidence(backendDirectory, runID, transportProbes)
	if err != nil {
		return externalDestinationVerification{}, err
	}
	otlpGRPCResponseSemantics, err := readAndValidateOTLPGRPCResponseSemanticsEvidence(backendDirectory, runID, transportProbes)
	if err != nil {
		return externalDestinationVerification{}, err
	}
	cloudEventsLimits, err := readAndValidateCloudEventsLimitsEvidence(backendDirectory, runID, transportProbes)
	if err != nil {
		return externalDestinationVerification{}, err
	}

	cloudBackend, cloudEncoded, err := readCloudEventsBackendEvidence(backendDirectory)
	if err != nil {
		return externalDestinationVerification{}, err
	}
	if err := validateCloudEventsBackendEvidence(cloudBackend, cloudProbe, runID); err != nil {
		return externalDestinationVerification{}, err
	}
	httpBackend, httpEncoded, err := readOTLPBackendEvidence(backendDirectory, "otlp-http")
	if err != nil {
		return externalDestinationVerification{}, err
	}
	if err := validateOTLPBackendEvidence(httpBackend, httpProbe, runID, "otlp-http"); err != nil {
		return externalDestinationVerification{}, err
	}
	grpcBackend, grpcEncoded, err := readOTLPBackendEvidence(backendDirectory, "otlp-grpc")
	if err != nil {
		return externalDestinationVerification{}, err
	}
	if err := validateOTLPBackendEvidence(grpcBackend, grpcProbe, runID, "otlp-grpc"); err != nil {
		return externalDestinationVerification{}, err
	}

	return externalDestinationVerification{
		SchemaVersion:   backendEvidenceSchemaVersion,
		RunID:           runID,
		CandidateCommit: bundleCommit,
		Result:          "passed",
		VerifiedAt:      time.Now().UTC().Format(time.RFC3339Nano),
		Proofs: []verifiedDestinationProof{
			{
				Probe:            "cloudevents-https",
				Backend:          cloudBackend.Receiver,
				QueriedAt:        cloudBackend.QueriedAt,
				EvidenceSHA256:   evidenceDigest(cloudEncoded),
				CloudEventSource: cloudBackend.Event.Source,
				CloudEventID:     cloudBackend.Event.ID,
				DeduplicatedRows: cloudBackend.DeduplicatedRows,
				DeliveryAttempts: cloudBackend.DeliveryAttempts,
			},
			{
				Probe:          "otlp-http",
				Backend:        httpBackend.Backend,
				QueriedAt:      httpBackend.QueriedAt,
				EvidenceSHA256: evidenceDigest(httpEncoded),
				TraceID:        httpBackend.Span.TraceID,
				SpanID:         httpBackend.Span.SpanID,
				MatchingSpans:  httpBackend.MatchingSpans,
			},
			{
				Probe:          "otlp-grpc",
				Backend:        grpcBackend.Backend,
				QueriedAt:      grpcBackend.QueriedAt,
				EvidenceSHA256: evidenceDigest(grpcEncoded),
				TraceID:        grpcBackend.Span.TraceID,
				SpanID:         grpcBackend.Span.SpanID,
				MatchingSpans:  grpcBackend.MatchingSpans,
			},
		},
		AuthenticationEvidenceSHA256:      evidenceDigest(authenticationEncoded),
		AuthenticationCheckedAt:           authentication.CheckedAt,
		AuthenticationChecks:              authentication.Checks,
		TokenRotationEvidenceSHA256:       evidenceDigest(tokenRotationEncoded),
		TokenRotationCheckedAt:            tokenRotation.CheckedAt,
		TokenRotationChecks:               tokenRotation.Checks,
		CertificateRotationEvidenceSHA256: evidenceDigest(certificateRotationEncoded),
		CertificateRotationCheckedAt:      certificateRotation.CheckedAt,
		CertificateRotationChecks:         certificateRotation.Checks,
		ExpiredCredentialEvidenceSHA256:   evidenceDigest(expiredCredentialsEncoded),
		ExpiredCredentialCheckedAt:        expiredCredentials.CheckedAt,
		ExpiredCredentialChecks:           expiredCredentials.Checks,
		ResponseSemanticsEvidenceSHA256:   responseSemantics.EvidenceSHA256,
		ResponseSemanticsQueriedAt:        responseSemantics.QueriedAt,
		ResponseSemanticsScenarioCount:    responseSemantics.ScenarioCount,
		ResponseSemanticsRetryableCount:   responseSemantics.RetryableCount,
		ResponseSemanticsPermanentCount:   responseSemantics.PermanentCount,
		OTLPGRPCResponseEvidenceSHA256:    otlpGRPCResponseSemantics.EvidenceSHA256,
		OTLPGRPCResponseQueriedAt:         otlpGRPCResponseSemantics.QueriedAt,
		OTLPGRPCResponseScenarioCount:     otlpGRPCResponseSemantics.ScenarioCount,
		OTLPGRPCResponseRetryableCount:    otlpGRPCResponseSemantics.RetryableCount,
		OTLPGRPCResponsePermanentCount:    otlpGRPCResponseSemantics.PermanentCount,
		CloudEventsLimitsEvidenceSHA256:   cloudEventsLimits.EvidenceSHA256,
		CloudEventsLimitsQueriedAt:        cloudEventsLimits.QueriedAt,
		CloudEventsLimitsCountEvents:      cloudEventsLimits.CountEvents,
		CloudEventsLimitsByteEvents:       cloudEventsLimits.ByteEvents,
		CloudEventsLimitsRequests:         cloudEventsLimits.AcceptedRequests,
		CloudEventsLimitsRecoveries:       cloudEventsLimits.OversizedRecoveries,
		Limitations: []string{
			"This verifies happy-path transport acceptance, exhaustive CloudEvents HTTP and OTLP gRPC response classification, CloudEvents count and byte splitting plus oversized-event recovery, invalid and expired bearer rejection, expired mTLS client-certificate rejection, two-phase bearer and client-certificate rotation, and independent backend identity proof only.",
			"It does not prove slow responses, sustained throttling/outage, restart, capacity, or privacy-review gates.",
		},
	}, nil
}

func readProbeEvidence(directory string, probe string) (probeEvidence, error) {
	var evidence probeEvidence
	path := filepath.Join(directory, probe+".json")
	if _, err := readJSONEvidence(path, &evidence); err != nil {
		return probeEvidence{}, fmt.Errorf("read %s transport evidence: %w", probe, err)
	}
	return evidence, nil
}

func readAuthenticationEvidence(directory string) (authenticationEvidence, []byte, error) {
	var evidence authenticationEvidence
	encoded, err := readJSONEvidence(filepath.Join(directory, "external-authentication.json"), &evidence)
	if err != nil {
		return authenticationEvidence{}, nil, fmt.Errorf("read authentication evidence: %w", err)
	}
	return evidence, encoded, nil
}

func validateAuthenticationEvidence(
	evidence authenticationEvidence,
	runID string,
	probes map[string]probeEvidence,
) error {
	if evidence.SchemaVersion != backendEvidenceSchemaVersion || evidence.RunID != runID {
		return fmt.Errorf("authentication evidence does not match schema or run")
	}
	expectedCommit, err := probeCandidateCommit(probes)
	if err != nil {
		return err
	}
	if evidence.CandidateCommit != expectedCommit {
		return fmt.Errorf("authentication evidence belongs to candidate %s, expected %s", evidence.CandidateCommit, expectedCommit)
	}
	if evidence.Result != "rejected_invalid_bearer" {
		return fmt.Errorf("authentication evidence does not prove invalid bearer rejection")
	}
	if err := validateEvidenceTimestamp(evidence.CheckedAt, "authentication checked_at"); err != nil {
		return err
	}
	expectedTransports := map[string]string{
		"cloudevents-https": "cloudevents-batch+json/https",
		"otlp-http":         "otlp/protobuf/https",
		"otlp-grpc":         "otlp/grpc/tls",
	}
	if len(evidence.Checks) != len(expectedTransports) {
		return fmt.Errorf("authentication evidence must contain exactly three transport checks")
	}
	seen := make(map[string]struct{}, len(evidence.Checks))
	for _, check := range evidence.Checks {
		expectedTransport, ok := expectedTransports[check.Probe]
		if !ok {
			return fmt.Errorf("authentication evidence contains unsupported probe %q", check.Probe)
		}
		if _, duplicate := seen[check.Probe]; duplicate {
			return fmt.Errorf("authentication evidence contains duplicate probe %q", check.Probe)
		}
		seen[check.Probe] = struct{}{}
		probe, ok := probes[check.Probe]
		if !ok || check.Transport != expectedTransport || check.Endpoint != probe.Endpoint {
			return fmt.Errorf("authentication evidence for %s does not match transport evidence", check.Probe)
		}
		if err := validateSafeEvidenceLabel(check.Endpoint, check.Probe+" authentication endpoint"); err != nil {
			return err
		}
		switch check.Probe {
		case "cloudevents-https", "otlp-http":
			if check.Observed != "http:401" && check.Observed != "http:403" {
				return fmt.Errorf("authentication evidence for %s did not record HTTP 401 or 403", check.Probe)
			}
		case "otlp-grpc":
			if check.Observed != "grpc:Unauthenticated" && check.Observed != "grpc:PermissionDenied" {
				return fmt.Errorf("authentication evidence for OTLP gRPC did not record credential rejection")
			}
		}
	}
	return nil
}

func readCloudEventsBackendEvidence(directory string) (cloudEventsBackendEvidence, []byte, error) {
	var evidence cloudEventsBackendEvidence
	encoded, err := readJSONEvidence(filepath.Join(directory, "cloudevents-backend.json"), &evidence)
	if err != nil {
		return cloudEventsBackendEvidence{}, nil, fmt.Errorf("read CloudEvents backend evidence: %w", err)
	}
	return evidence, encoded, nil
}

func readOTLPBackendEvidence(directory string, probe string) (otlpBackendEvidence, []byte, error) {
	var evidence otlpBackendEvidence
	encoded, err := readJSONEvidence(filepath.Join(directory, probe+"-backend.json"), &evidence)
	if err != nil {
		return otlpBackendEvidence{}, nil, fmt.Errorf("read %s backend evidence: %w", probe, err)
	}
	return evidence, encoded, nil
}

func readJSONEvidence(path string, target any) ([]byte, error) {
	encoded, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	if len(encoded) > 4*1024*1024 {
		return nil, fmt.Errorf("%s exceeds the 4 MiB evidence limit", filepath.Base(path))
	}
	if err := json.Unmarshal(encoded, target); err != nil {
		return nil, err
	}
	return encoded, nil
}

func validateTransportProbe(evidence probeEvidence, runID string, probe string, attempts int) error {
	if err := validateCandidateCommit(evidence.CandidateCommit); err != nil {
		return fmt.Errorf("%s transport evidence: %w", probe, err)
	}
	if evidence.SchemaVersion != backendEvidenceSchemaVersion {
		return fmt.Errorf("%s transport evidence has unsupported schema_version %q", probe, evidence.SchemaVersion)
	}
	if evidence.RunID != runID || evidence.Probe != probe {
		return fmt.Errorf("%s transport evidence identity does not match this run", probe)
	}
	if evidence.Result != "transport_accepted" || evidence.Attempts != attempts {
		return fmt.Errorf("%s transport evidence does not prove %d accepted attempt(s)", probe, attempts)
	}
	if err := validateEvidenceTimestamp(evidence.AcceptedAt, probe+" accepted_at"); err != nil {
		return err
	}
	if err := validateSafeEvidenceLabel(evidence.Endpoint, probe+" endpoint"); err != nil {
		return err
	}
	if err := validateSafeEvidenceLabel(evidence.DownstreamProofGate, probe+" downstream_proof_gate"); err != nil {
		return err
	}

	expectedTransport := ""
	statusesRequired := false
	switch probe {
	case "cloudevents-https":
		expectedTransport = "cloudevents-batch+json/https"
		statusesRequired = true
		if !strings.HasPrefix(evidence.CloudEventSource, "openshell://") {
			return fmt.Errorf("CloudEvents transport evidence has invalid source")
		}
		if err := validateCloudEventID(evidence.CloudEventID); err != nil {
			return err
		}
	case "otlp-http":
		expectedTransport = "otlp/protobuf/https"
		statusesRequired = true
		if err := validateHexIdentifier(evidence.TraceID, 32, probe+" trace_id"); err != nil {
			return err
		}
		if err := validateHexIdentifier(evidence.SpanID, 16, probe+" span_id"); err != nil {
			return err
		}
	case "otlp-grpc":
		expectedTransport = "otlp/grpc/tls"
		if err := validateHexIdentifier(evidence.TraceID, 32, probe+" trace_id"); err != nil {
			return err
		}
		if err := validateHexIdentifier(evidence.SpanID, 16, probe+" span_id"); err != nil {
			return err
		}
	default:
		return fmt.Errorf("unsupported conformance probe %q", probe)
	}
	if evidence.Transport != expectedTransport {
		return fmt.Errorf("%s transport evidence has unexpected transport %q", probe, evidence.Transport)
	}
	if statusesRequired && len(evidence.HTTPStatuses) != attempts {
		return fmt.Errorf("%s transport evidence status count does not match attempts", probe)
	}
	if !statusesRequired && len(evidence.HTTPStatuses) != 0 {
		return fmt.Errorf("%s transport evidence must not claim HTTP statuses", probe)
	}
	for _, status := range evidence.HTTPStatuses {
		if status < 200 || status >= 300 {
			return fmt.Errorf("%s transport evidence contains non-2xx status %d", probe, status)
		}
	}
	return nil
}

func validateCloudEventsBackendEvidence(
	evidence cloudEventsBackendEvidence,
	probe probeEvidence,
	runID string,
) error {
	if evidence.SchemaVersion != backendEvidenceSchemaVersion || evidence.RunID != runID {
		return fmt.Errorf("CloudEvents backend evidence does not match schema or run")
	}
	if err := validateSafeEvidenceLabel(evidence.Receiver, "receiver"); err != nil {
		return err
	}
	if err := validateEvidenceTimestamp(evidence.QueriedAt, "CloudEvents queried_at"); err != nil {
		return err
	}
	if evidence.DeduplicatedRows != 1 {
		return fmt.Errorf("CloudEvents backend must contain exactly one deduplicated row")
	}
	if evidence.DeliveryAttempts < probe.Attempts {
		return fmt.Errorf("CloudEvents backend must account for at least %d deliveries", probe.Attempts)
	}
	if evidence.Event.SpecVersion != "1.0" ||
		evidence.Event.Source != probe.CloudEventSource ||
		evidence.Event.ID != probe.CloudEventID {
		return fmt.Errorf("CloudEvents backend event identity does not match transport evidence")
	}
	if evidence.Event.Type == "" {
		return fmt.Errorf("CloudEvents backend event type is required")
	}
	if !evidence.Event.UnknownExtension.Retain {
		return fmt.Errorf("CloudEvents backend did not retain unknown_extension.retain")
	}
	if !evidence.Event.Data.Original.ConformanceProbe ||
		evidence.Event.Data.Original.ConformanceRunID != runID ||
		evidence.Event.Data.Original.ConformanceCandidateCommit != probe.CandidateCommit {
		return fmt.Errorf("CloudEvents backend did not retain conformance provenance")
	}
	return nil
}

func validateOTLPBackendEvidence(
	evidence otlpBackendEvidence,
	probe probeEvidence,
	runID string,
	transport string,
) error {
	if evidence.SchemaVersion != backendEvidenceSchemaVersion || evidence.RunID != runID {
		return fmt.Errorf("%s backend evidence does not match schema or run", transport)
	}
	if err := validateSafeEvidenceLabel(evidence.Backend, "backend"); err != nil {
		return err
	}
	if err := validateEvidenceTimestamp(evidence.QueriedAt, transport+" queried_at"); err != nil {
		return err
	}
	if evidence.Transport != transport || evidence.MatchingSpans != 1 {
		return fmt.Errorf("%s backend evidence must prove exactly one matching span", transport)
	}
	if evidence.Span.TraceID != probe.TraceID || evidence.Span.SpanID != probe.SpanID {
		return fmt.Errorf("%s backend span identity does not match transport evidence", transport)
	}
	runAttribute, runOK := evidence.Span.Attributes["openshell.conformance.run_id"].(string)
	probeAttribute, probeOK := evidence.Span.Attributes["openshell.conformance.probe"].(string)
	commitAttribute, commitOK := evidence.Span.Attributes["openshell.conformance.candidate_commit"].(string)
	if !runOK || runAttribute != runID || !probeOK || probeAttribute != transport {
		return fmt.Errorf("%s backend span did not retain conformance attributes", transport)
	}
	if !commitOK || commitAttribute != probe.CandidateCommit {
		return fmt.Errorf("%s backend span did not retain the conformance candidate commit", transport)
	}
	return nil
}

func validateCloudEventID(value string) error {
	if !strings.HasPrefix(value, "sha256:") {
		return fmt.Errorf("CloudEvents id must use sha256")
	}
	return validateHexIdentifier(strings.TrimPrefix(value, "sha256:"), 64, "CloudEvents id")
}

func validateHexIdentifier(value string, length int, field string) error {
	if len(value) != length {
		return fmt.Errorf("%s must contain %d lowercase hexadecimal characters", field, length)
	}
	decoded, err := hex.DecodeString(value)
	if err != nil || hex.EncodeToString(decoded) != value {
		return fmt.Errorf("%s must contain %d lowercase hexadecimal characters", field, length)
	}
	return nil
}

func validateSafeEvidenceLabel(value string, field string) error {
	if value == "" || len(value) > 256 || strings.TrimSpace(value) != value ||
		strings.ContainsAny(value, "\r\n\t") {
		return fmt.Errorf("%s must be a nonempty single-line label of at most 256 bytes", field)
	}
	return nil
}

func validateEvidenceTimestamp(value string, field string) error {
	if _, err := time.Parse(time.RFC3339Nano, value); err != nil {
		return fmt.Errorf("%s must be RFC3339: %w", field, err)
	}
	return nil
}

func evidenceDigest(encoded []byte) string {
	sum := sha256.Sum256(encoded)
	return "sha256:" + hex.EncodeToString(sum[:])
}

func requiredVerificationEnv(t *testing.T, name string) string {
	t.Helper()
	value := os.Getenv(name)
	if value == "" {
		t.Fatalf("%s is required when backend evidence verification is enabled", name)
	}
	return value
}

func writeVerificationEvidence(path string, evidence externalDestinationVerification) error {
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

func TestWriteVerificationEvidenceDoesNotOverwrite(t *testing.T) {
	path := filepath.Join(t.TempDir(), "verification.json")
	first := externalDestinationVerification{
		SchemaVersion: backendEvidenceSchemaVersion,
		RunID:         "candidate-first",
		Result:        "passed",
	}
	if err := writeVerificationEvidence(path, first); err != nil {
		t.Fatal(err)
	}
	before, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if info.Mode().Perm() != 0o600 {
		t.Fatalf("verification evidence mode = %o, want 600", info.Mode().Perm())
	}
	second := first
	second.RunID = "candidate-second"
	if err := writeVerificationEvidence(path, second); err == nil {
		t.Fatal("verification evidence was overwritten")
	}
	after, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if string(after) != string(before) {
		t.Fatal("existing verification evidence changed after overwrite attempt")
	}
}

func TestVerifyExternalBackendEvidenceAcceptsIndependentProof(t *testing.T) {
	runID, reportDirectory, backendDirectory := validBackendEvidenceFixture(t)
	verification, err := verifyExternalBackendEvidence(reportDirectory, backendDirectory, runID)
	if err != nil {
		t.Fatal(err)
	}
	if verification.Result != "passed" || len(verification.Proofs) != 3 ||
		verification.CandidateCommit != fixtureCandidateCommit ||
		len(verification.AuthenticationChecks) != 3 || verification.AuthenticationEvidenceSHA256 == "" ||
		len(verification.TokenRotationChecks) != 3 || verification.TokenRotationEvidenceSHA256 == "" ||
		len(verification.CertificateRotationChecks) != 3 || verification.CertificateRotationEvidenceSHA256 == "" ||
		len(verification.ExpiredCredentialChecks) != 3 || verification.ExpiredCredentialEvidenceSHA256 == "" ||
		verification.ResponseSemanticsEvidenceSHA256 == "" || verification.ResponseSemanticsScenarioCount != 300 ||
		verification.ResponseSemanticsRetryableCount != 103 || verification.ResponseSemanticsPermanentCount != 197 ||
		verification.OTLPGRPCResponseEvidenceSHA256 == "" || verification.OTLPGRPCResponseScenarioCount != 17 ||
		verification.OTLPGRPCResponseRetryableCount != 7 || verification.OTLPGRPCResponsePermanentCount != 10 ||
		verification.CloudEventsLimitsEvidenceSHA256 == "" || verification.CloudEventsLimitsCountEvents != 501 ||
		verification.CloudEventsLimitsByteEvents != 7 || verification.CloudEventsLimitsRequests != 4 ||
		verification.CloudEventsLimitsRecoveries != 1 {
		t.Fatalf("unexpected verification summary: %#v", verification)
	}
	if verification.Proofs[1].TraceID == verification.Proofs[2].TraceID {
		t.Fatal("OTLP transport proofs reused a trace ID")
	}
}

func TestVerifyExternalBackendEvidenceRejectsMismatches(t *testing.T) {
	tests := []struct {
		name   string
		file   string
		mutate func(map[string]any)
	}{
		{
			name: "CloudEvents ID",
			file: "cloudevents-backend.json",
			mutate: func(document map[string]any) {
				document["event"].(map[string]any)["id"] = "sha256:" + strings.Repeat("0", 64)
			},
		},
		{
			name: "unknown extension",
			file: "cloudevents-backend.json",
			mutate: func(document map[string]any) {
				document["event"].(map[string]any)["unknown_extension"].(map[string]any)["retain"] = false
			},
		},
		{
			name: "deduplicated rows",
			file: "cloudevents-backend.json",
			mutate: func(document map[string]any) {
				document["deduplicated_rows"] = float64(2)
			},
		},
		{
			name: "delivery accounting",
			file: "cloudevents-backend.json",
			mutate: func(document map[string]any) {
				document["delivery_attempts"] = float64(1)
			},
		},
		{
			name: "CloudEvents candidate provenance",
			file: "cloudevents-backend.json",
			mutate: func(document map[string]any) {
				original := document["event"].(map[string]any)["data"].(map[string]any)["original"].(map[string]any)
				original["conformance_candidate_commit"] = strings.Repeat("b", 40)
			},
		},
		{
			name: "HTTP trace identity",
			file: "otlp-http-backend.json",
			mutate: func(document map[string]any) {
				document["span"].(map[string]any)["trace_id"] = strings.Repeat("0", 32)
			},
		},
		{
			name: "gRPC probe attribute",
			file: "otlp-grpc-backend.json",
			mutate: func(document map[string]any) {
				document["span"].(map[string]any)["attributes"].(map[string]any)["openshell.conformance.probe"] = "otlp-http"
			},
		},
		{
			name: "OTLP candidate attribute",
			file: "otlp-http-backend.json",
			mutate: func(document map[string]any) {
				document["span"].(map[string]any)["attributes"].(map[string]any)["openshell.conformance.candidate_commit"] = strings.Repeat("b", 40)
			},
		},
		{
			name: "run mismatch",
			file: "otlp-http-backend.json",
			mutate: func(document map[string]any) {
				document["run_id"] = "different-run"
			},
		},
		{
			name: "query timestamp",
			file: "otlp-grpc-backend.json",
			mutate: func(document map[string]any) {
				document["queried_at"] = "not-a-time"
			},
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			runID, reportDirectory, backendDirectory := validBackendEvidenceFixture(t)
			mutateJSONFixture(t, filepath.Join(backendDirectory, test.file), test.mutate)
			if _, err := verifyExternalBackendEvidence(reportDirectory, backendDirectory, runID); err == nil {
				t.Fatal("verification accepted mismatched backend evidence")
			}
		})
	}
}

func TestVerifyExternalBackendEvidenceRejectsMissingAuthenticationProof(t *testing.T) {
	runID, reportDirectory, backendDirectory := validBackendEvidenceFixture(t)
	path := filepath.Join(reportDirectory, "external-authentication.json")
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}
	if _, err := verifyExternalBackendEvidence(reportDirectory, backendDirectory, runID); err == nil {
		t.Fatal("verification accepted missing authentication evidence")
	}
}

func TestVerifyExternalBackendEvidenceRejectsInvalidAuthenticationProof(t *testing.T) {
	tests := []struct {
		name   string
		mutate func(map[string]any)
	}{
		{
			name: "accepted credential",
			mutate: func(document map[string]any) {
				document["checks"].([]any)[0].(map[string]any)["observed"] = "http:202"
			},
		},
		{
			name: "wrong endpoint",
			mutate: func(document map[string]any) {
				document["checks"].([]any)[1].(map[string]any)["endpoint"] = "https://other.example.test/v1/traces"
			},
		},
		{
			name: "missing transport",
			mutate: func(document map[string]any) {
				document["checks"] = document["checks"].([]any)[:2]
			},
		},
		{
			name:   "wrong run",
			mutate: func(document map[string]any) { document["run_id"] = "different-run" },
		},
		{
			name: "wrong candidate",
			mutate: func(document map[string]any) {
				document["candidate_commit"] = strings.Repeat("b", 40)
			},
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			runID, reportDirectory, backendDirectory := validBackendEvidenceFixture(t)
			mutateJSONFixture(t, filepath.Join(reportDirectory, "external-authentication.json"), test.mutate)
			if _, err := verifyExternalBackendEvidence(reportDirectory, backendDirectory, runID); err == nil {
				t.Fatal("verification accepted invalid authentication evidence")
			}
		})
	}
}

func TestVerifyExternalBackendEvidenceRejectsMissingTokenRotationProof(t *testing.T) {
	runID, reportDirectory, backendDirectory := validBackendEvidenceFixture(t)
	if err := os.Remove(filepath.Join(reportDirectory, tokenRotationAfterFile)); err != nil {
		t.Fatal(err)
	}
	if _, err := verifyExternalBackendEvidence(reportDirectory, backendDirectory, runID); err == nil {
		t.Fatal("verification accepted missing token rotation evidence")
	}
}

func TestVerifyExternalBackendEvidenceRejectsInvalidTokenRotationProof(t *testing.T) {
	tests := []struct {
		name   string
		file   string
		mutate func(map[string]any)
	}{
		{
			name: "retired credential accepted",
			file: tokenRotationAfterFile,
			mutate: func(document map[string]any) {
				document["checks"].([]any)[0].(map[string]any)["retired_observed"] = "http:202"
			},
		},
		{
			name: "current credential unchanged",
			file: tokenRotationAfterFile,
			mutate: func(document map[string]any) {
				document["current_token_commitment"] = document["retired_token_commitment"]
			},
		},
		{
			name: "wrong endpoint",
			file: tokenRotationAfterFile,
			mutate: func(document map[string]any) {
				document["checks"].([]any)[1].(map[string]any)["endpoint"] = "https://other.example.test/v1/traces"
			},
		},
		{
			name: "before evidence changed",
			file: tokenRotationBeforeFile,
			mutate: func(document map[string]any) {
				document["checked_at"] = time.Now().Add(-time.Hour).UTC().Format(time.RFC3339Nano)
			},
		},
		{
			name: "before candidate mismatch",
			file: tokenRotationBeforeFile,
			mutate: func(document map[string]any) {
				document["candidate_commit"] = strings.Repeat("b", 40)
			},
		},
		{
			name: "after candidate mismatch",
			file: tokenRotationAfterFile,
			mutate: func(document map[string]any) {
				document["candidate_commit"] = strings.Repeat("b", 40)
			},
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			runID, reportDirectory, backendDirectory := validBackendEvidenceFixture(t)
			mutateJSONFixture(t, filepath.Join(reportDirectory, test.file), test.mutate)
			if _, err := verifyExternalBackendEvidence(reportDirectory, backendDirectory, runID); err == nil {
				t.Fatal("verification accepted invalid token rotation evidence")
			}
		})
	}
}

func TestVerifyExternalBackendEvidenceRejectsMissingClientCertificateRotationProof(t *testing.T) {
	runID, reportDirectory, backendDirectory := validBackendEvidenceFixture(t)
	if err := os.Remove(filepath.Join(reportDirectory, clientCertificateRotationFile)); err != nil {
		t.Fatal(err)
	}
	if _, err := verifyExternalBackendEvidence(reportDirectory, backendDirectory, runID); err == nil {
		t.Fatal("verification accepted missing client-certificate rotation evidence")
	}
}

func TestVerifyExternalBackendEvidenceRejectsInvalidClientCertificateRotationProof(t *testing.T) {
	tests := []struct {
		name   string
		file   string
		mutate func(map[string]any)
	}{
		{
			name: "retired certificate accepted",
			file: clientCertificateRotationFile,
			mutate: func(document map[string]any) {
				document["checks"].([]any)[0].(map[string]any)["retired_observed"] = "http:202"
			},
		},
		{
			name: "current certificate rejected",
			file: clientCertificateRotationFile,
			mutate: func(document map[string]any) {
				document["checks"].([]any)[1].(map[string]any)["current_observed"] = "http:403"
			},
		},
		{
			name: "same certificate",
			file: clientCertificateRotationFile,
			mutate: func(document map[string]any) {
				document["current_client_certificate_sha256"] = document["retired_client_certificate_sha256"]
			},
		},
		{
			name: "wrong bearer",
			file: clientCertificateRotationFile,
			mutate: func(document map[string]any) {
				document["token_commitment"] = "sha256:" + strings.Repeat("8", 64)
			},
		},
		{
			name: "wrong before hash",
			file: clientCertificateRotationFile,
			mutate: func(document map[string]any) {
				document["before_evidence_sha256"] = "sha256:" + strings.Repeat("9", 64)
			},
		},
		{
			name: "wrong endpoint",
			file: clientCertificateRotationFile,
			mutate: func(document map[string]any) {
				document["checks"].([]any)[2].(map[string]any)["endpoint"] = "other.example.test:4317"
			},
		},
		{
			name: "retired certificate expired during rotation",
			file: clientCertificateBeforeRotationFile,
			mutate: func(document map[string]any) {
				document["client_certificate_not_after"] = time.Now().Add(-time.Hour).UTC().Format(time.RFC3339Nano)
			},
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			runID, reportDirectory, backendDirectory := validBackendEvidenceFixture(t)
			mutateJSONFixture(t, filepath.Join(reportDirectory, test.file), test.mutate)
			if _, err := verifyExternalBackendEvidence(reportDirectory, backendDirectory, runID); err == nil {
				t.Fatal("verification accepted invalid client-certificate rotation evidence")
			}
		})
	}
}

func TestVerifyExternalBackendEvidenceRejectsMissingExpiredCredentialProof(t *testing.T) {
	runID, reportDirectory, backendDirectory := validBackendEvidenceFixture(t)
	if err := os.Remove(filepath.Join(reportDirectory, expiredCredentialEvidenceFile)); err != nil {
		t.Fatal(err)
	}
	if _, err := verifyExternalBackendEvidence(reportDirectory, backendDirectory, runID); err == nil {
		t.Fatal("verification accepted missing expired-credential evidence")
	}
}

func TestVerifyExternalBackendEvidenceRejectsInvalidExpiredCredentialProof(t *testing.T) {
	tests := []struct {
		name   string
		mutate func(map[string]any)
	}{
		{
			name: "expired bearer accepted",
			mutate: func(document map[string]any) {
				document["checks"].([]any)[0].(map[string]any)["expired_bearer_observed"] = "http:202"
			},
		},
		{
			name: "expired client certificate accepted",
			mutate: func(document map[string]any) {
				document["checks"].([]any)[1].(map[string]any)["expired_client_certificate_observed"] = "http:200"
			},
		},
		{
			name: "current credential rejected",
			mutate: func(document map[string]any) {
				document["checks"].([]any)[2].(map[string]any)["current_observed"] = "grpc:Unauthenticated"
			},
		},
		{
			name: "certificate not expired",
			mutate: func(document map[string]any) {
				document["expired_client_certificate_not_after"] = time.Now().Add(time.Hour).UTC().Format(time.RFC3339Nano)
			},
		},
		{
			name: "same certificate",
			mutate: func(document map[string]any) {
				document["expired_client_certificate_sha256"] = document["current_client_certificate_sha256"]
			},
		},
		{
			name: "same token",
			mutate: func(document map[string]any) {
				document["expired_token_commitment"] = document["current_token_commitment"]
			},
		},
		{
			name: "current token was not rotated current",
			mutate: func(document map[string]any) {
				document["current_token_commitment"] = "sha256:" + strings.Repeat("7", 64)
			},
		},
		{
			name: "current certificate was not rotated current",
			mutate: func(document map[string]any) {
				document["current_client_certificate_sha256"] = "sha256:" + strings.Repeat("7", 64)
			},
		},
		{
			name: "expired token was never retired",
			mutate: func(document map[string]any) {
				document["expired_token_commitment"] = "sha256:" + strings.Repeat("5", 64)
			},
		},
		{
			name: "wrong candidate",
			mutate: func(document map[string]any) {
				document["candidate_commit"] = strings.Repeat("b", 40)
			},
		},
		{
			name: "wrong endpoint",
			mutate: func(document map[string]any) {
				document["checks"].([]any)[1].(map[string]any)["endpoint"] = "https://other.example.test/v1/traces"
			},
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			runID, reportDirectory, backendDirectory := validBackendEvidenceFixture(t)
			mutateJSONFixture(
				t,
				filepath.Join(reportDirectory, expiredCredentialEvidenceFile),
				test.mutate,
			)
			if _, err := verifyExternalBackendEvidence(reportDirectory, backendDirectory, runID); err == nil {
				t.Fatal("verification accepted invalid expired-credential evidence")
			}
		})
	}
}

func TestVerifyExternalBackendEvidenceRejectsMissingResponseSemanticsProof(t *testing.T) {
	runID, reportDirectory, backendDirectory := validBackendEvidenceFixture(t)
	if err := os.Remove(filepath.Join(backendDirectory, cloudEventsResponseSemanticsFile)); err != nil {
		t.Fatal(err)
	}
	if _, err := verifyExternalBackendEvidence(reportDirectory, backendDirectory, runID); err == nil {
		t.Fatal("verification accepted missing CloudEvents response-semantics evidence")
	}
}

func TestVerifyExternalBackendEvidenceRejectsInvalidResponseSemanticsProof(t *testing.T) {
	tests := []struct {
		name   string
		mutate func(map[string]any)
	}{
		{
			name: "missing status",
			mutate: func(document map[string]any) {
				scenarios := document["scenarios"].([]any)
				document["scenarios"] = scenarios[:len(scenarios)-1]
			},
		},
		{
			name: "retryable not retried",
			mutate: func(document map[string]any) {
				scenario := document["scenarios"].([]any)[408-300].(map[string]any)
				attempts := scenario["attempts"].([]any)
				scenario["attempts"] = attempts[:1]
			},
		},
		{
			name: "permanent retried",
			mutate: func(document map[string]any) {
				scenario := document["scenarios"].([]any)[400-300].(map[string]any)
				attempts := scenario["attempts"].([]any)
				scenario["attempts"] = append(attempts, attempts[0])
			},
		},
		{
			name: "wrong candidate",
			mutate: func(document map[string]any) {
				document["candidate_commit"] = strings.Repeat("b", 40)
			},
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			runID, reportDirectory, backendDirectory := validBackendEvidenceFixture(t)
			mutateJSONFixture(
				t,
				filepath.Join(backendDirectory, cloudEventsResponseSemanticsFile),
				test.mutate,
			)
			if _, err := verifyExternalBackendEvidence(reportDirectory, backendDirectory, runID); err == nil {
				t.Fatal("verification accepted invalid CloudEvents response-semantics evidence")
			}
		})
	}
}

func TestVerifyExternalBackendEvidenceRejectsMissingOTLPGRPCResponseProof(t *testing.T) {
	runID, reportDirectory, backendDirectory := validBackendEvidenceFixture(t)
	if err := os.Remove(filepath.Join(backendDirectory, otlpGRPCResponseSemanticsFile)); err != nil {
		t.Fatal(err)
	}
	if _, err := verifyExternalBackendEvidence(reportDirectory, backendDirectory, runID); err == nil {
		t.Fatal("verification accepted missing OTLP gRPC response-semantics evidence")
	}
}

func TestVerifyExternalBackendEvidenceRejectsInvalidOTLPGRPCResponseProof(t *testing.T) {
	tests := []struct {
		name   string
		mutate func(map[string]any)
	}{
		{
			name: "missing scenario",
			mutate: func(document map[string]any) {
				scenarios := document["scenarios"].([]any)
				document["scenarios"] = scenarios[:len(scenarios)-1]
			},
		},
		{
			name: "retryable not retried",
			mutate: func(document map[string]any) {
				scenario := document["scenarios"].([]any)[0].(map[string]any)
				scenario["attempts"] = scenario["attempts"].([]any)[:1]
			},
		},
		{
			name: "resource exhausted without RetryInfo retried",
			mutate: func(document map[string]any) {
				scenario := document["scenarios"].([]any)[7].(map[string]any)
				attempts := scenario["attempts"].([]any)
				scenario["attempts"] = append(attempts, attempts[0])
			},
		},
		{
			name: "wrong candidate",
			mutate: func(document map[string]any) {
				document["candidate_commit"] = strings.Repeat("b", 40)
			},
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			runID, reportDirectory, backendDirectory := validBackendEvidenceFixture(t)
			mutateJSONFixture(t, filepath.Join(backendDirectory, otlpGRPCResponseSemanticsFile), test.mutate)
			if _, err := verifyExternalBackendEvidence(reportDirectory, backendDirectory, runID); err == nil {
				t.Fatal("verification accepted invalid OTLP gRPC response-semantics evidence")
			}
		})
	}
}

func TestVerifyExternalBackendEvidenceRejectsInvalidTransportReport(t *testing.T) {
	tests := []struct {
		name   string
		mutate func(map[string]any)
	}{
		{
			name: "transport",
			mutate: func(document map[string]any) {
				document["transport"] = "otlp/grpc/tls"
			},
		},
		{
			name: "candidate",
			mutate: func(document map[string]any) {
				document["candidate_commit"] = strings.Repeat("b", 40)
			},
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			runID, reportDirectory, backendDirectory := validBackendEvidenceFixture(t)
			mutateJSONFixture(t, filepath.Join(reportDirectory, "otlp-http.json"), test.mutate)
			if _, err := verifyExternalBackendEvidence(reportDirectory, backendDirectory, runID); err == nil {
				t.Fatal("verification accepted a mismatched transport report")
			}
		})
	}
}

func TestVerifyExternalBackendEvidenceRejectsSharedOTLPIdentity(t *testing.T) {
	runID, reportDirectory, backendDirectory := validBackendEvidenceFixture(t)
	var httpReport map[string]any
	encoded, err := os.ReadFile(filepath.Join(reportDirectory, "otlp-http.json"))
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(encoded, &httpReport); err != nil {
		t.Fatal(err)
	}
	traceID := httpReport["trace_id"].(string)
	spanID := httpReport["span_id"].(string)
	mutateJSONFixture(t, filepath.Join(reportDirectory, "otlp-grpc.json"), func(document map[string]any) {
		document["trace_id"] = traceID
		document["span_id"] = spanID
	})
	mutateJSONFixture(t, filepath.Join(backendDirectory, "otlp-grpc-backend.json"), func(document map[string]any) {
		span := document["span"].(map[string]any)
		span["trace_id"] = traceID
		span["span_id"] = spanID
	})
	if _, err := verifyExternalBackendEvidence(reportDirectory, backendDirectory, runID); err == nil {
		t.Fatal("verification accepted shared HTTP and gRPC trace identity")
	}
}

func validBackendEvidenceFixture(t *testing.T) (string, string, string) {
	t.Helper()
	runID := "candidate-2026-08-19-a"
	t.Setenv("CONFORMANCE_RUN_ID", runID)
	reportDirectory := t.TempDir()
	backendDirectory := t.TempDir()
	source := "openshell://conformance/workspaces/default/sandboxes/gate/sources/stream.warning"
	_, eventID, _, _ := conformanceIdentity(t, "cloudevents-https")
	_, _, httpTraceID, httpSpanID := conformanceIdentity(t, "otlp-http")
	_, _, grpcTraceID, grpcSpanID := conformanceIdentity(t, "otlp-grpc")
	nowTime := time.Now().UTC()
	now := nowTime.Format(time.RFC3339Nano)

	writeJSONFixture(t, filepath.Join(reportDirectory, "cloudevents-https.json"), probeEvidence{
		SchemaVersion:       backendEvidenceSchemaVersion,
		RunID:               runID,
		CandidateCommit:     fixtureCandidateCommit,
		Probe:               "cloudevents-https",
		Transport:           "cloudevents-batch+json/https",
		Endpoint:            "https://receiver.example.test/v1/events",
		Result:              "transport_accepted",
		AcceptedAt:          now,
		Attempts:            2,
		HTTPStatuses:        []int{202, 202},
		CloudEventSource:    source,
		CloudEventID:        eventID,
		DownstreamProofGate: "query exact source and id",
	})
	writeJSONFixture(t, filepath.Join(reportDirectory, "otlp-http.json"), probeEvidence{
		SchemaVersion:       backendEvidenceSchemaVersion,
		RunID:               runID,
		CandidateCommit:     fixtureCandidateCommit,
		Probe:               "otlp-http",
		Transport:           "otlp/protobuf/https",
		Endpoint:            "https://otlp.example.test/v1/traces",
		Result:              "transport_accepted",
		AcceptedAt:          now,
		Attempts:            1,
		HTTPStatuses:        []int{200},
		TraceID:             httpTraceID,
		SpanID:              httpSpanID,
		DownstreamProofGate: "query exact HTTP trace and span",
	})
	writeJSONFixture(t, filepath.Join(reportDirectory, "otlp-grpc.json"), probeEvidence{
		SchemaVersion:       backendEvidenceSchemaVersion,
		RunID:               runID,
		CandidateCommit:     fixtureCandidateCommit,
		Probe:               "otlp-grpc",
		Transport:           "otlp/grpc/tls",
		Endpoint:            "otlp.example.test:4317",
		Result:              "transport_accepted",
		AcceptedAt:          now,
		Attempts:            1,
		TraceID:             grpcTraceID,
		SpanID:              grpcSpanID,
		DownstreamProofGate: "query exact gRPC trace and span",
	})

	writeJSONFixture(t, filepath.Join(reportDirectory, "external-authentication.json"), authenticationEvidence{
		SchemaVersion:   backendEvidenceSchemaVersion,
		RunID:           runID,
		CandidateCommit: fixtureCandidateCommit,
		Result:          "rejected_invalid_bearer",
		CheckedAt:       now,
		Checks: []authenticationCheck{
			{
				Probe:     "cloudevents-https",
				Transport: "cloudevents-batch+json/https",
				Endpoint:  "https://receiver.example.test/v1/events",
				Observed:  "http:401",
			},
			{
				Probe:     "otlp-http",
				Transport: "otlp/protobuf/https",
				Endpoint:  "https://otlp.example.test/v1/traces",
				Observed:  "http:403",
			},
			{
				Probe:     "otlp-grpc",
				Transport: "otlp/grpc/tls",
				Endpoint:  "otlp.example.test:4317",
				Observed:  "grpc:Unauthenticated",
			},
		},
	})

	probes := map[string]probeEvidence{
		"cloudevents-https": {
			CandidateCommit: fixtureCandidateCommit,
			Probe:           "cloudevents-https", Transport: "cloudevents-batch+json/https",
			Endpoint: "https://receiver.example.test/v1/events",
		},
		"otlp-http": {
			CandidateCommit: fixtureCandidateCommit,
			Probe:           "otlp-http", Transport: "otlp/protobuf/https",
			Endpoint: "https://otlp.example.test/v1/traces",
		},
		"otlp-grpc": {
			CandidateCommit: fixtureCandidateCommit,
			Probe:           "otlp-grpc", Transport: "otlp/grpc/tls",
			Endpoint: "otlp.example.test:4317",
		},
	}
	writeTokenRotationFixtures(t, reportDirectory, runID, probes, nowTime.Add(-6*time.Minute))
	currentTokenCommitment := tokenCommitment(runID, strings.Repeat("c", 64))
	writeClientCertificateRotationFixtures(
		t,
		reportDirectory,
		runID,
		probes,
		nowTime.Add(-4*time.Minute),
		currentTokenCommitment,
		"sha256:"+strings.Repeat("3", 64),
	)
	writeExpiredCredentialFixture(t, reportDirectory, runID, nowTime)

	writeCloudEventsResponseSemanticsFixture(
		t,
		backendDirectory,
		runID,
		fixtureCandidateCommit,
		nowTime,
	)
	writeOTLPGRPCResponseSemanticsFixture(
		t,
		backendDirectory,
		runID,
		fixtureCandidateCommit,
		nowTime,
	)
	writeCloudEventsLimitsFixture(t, backendDirectory, runID, fixtureCandidateCommit, nowTime)
	writeJSONFixture(t, filepath.Join(backendDirectory, "cloudevents-backend.json"), map[string]any{
		"schema_version":    backendEvidenceSchemaVersion,
		"run_id":            runID,
		"receiver":          "external-staging-receiver",
		"queried_at":        now,
		"deduplicated_rows": 1,
		"delivery_attempts": 2,
		"event": map[string]any{
			"specversion":       "1.0",
			"id":                eventID,
			"source":            source,
			"type":              "com.nvidia.openshell.stream.warning.v1",
			"unknown_extension": map[string]any{"retain": true},
			"data": map[string]any{
				"original": map[string]any{
					"conformance_probe":            true,
					"conformance_run_id":           runID,
					"conformance_candidate_commit": fixtureCandidateCommit,
				},
			},
		},
	})
	writeOTLPBackendFixture(t, backendDirectory, runID, "otlp-http", httpTraceID, httpSpanID, now)
	writeOTLPBackendFixture(t, backendDirectory, runID, "otlp-grpc", grpcTraceID, grpcSpanID, now)
	return runID, reportDirectory, backendDirectory
}

func writeOTLPBackendFixture(
	t *testing.T,
	directory string,
	runID string,
	transport string,
	traceID string,
	spanID string,
	queriedAt string,
) {
	t.Helper()
	writeJSONFixture(t, filepath.Join(directory, transport+"-backend.json"), map[string]any{
		"schema_version": backendEvidenceSchemaVersion,
		"run_id":         runID,
		"backend":        "external-staging-otlp",
		"queried_at":     queriedAt,
		"transport":      transport,
		"matching_spans": 1,
		"span": map[string]any{
			"trace_id": traceID,
			"span_id":  spanID,
			"attributes": map[string]any{
				"openshell.conformance.run_id":           runID,
				"openshell.conformance.probe":            transport,
				"openshell.conformance.candidate_commit": fixtureCandidateCommit,
			},
		},
	})
}

func writeJSONFixture(t *testing.T, path string, value any) {
	t.Helper()
	encoded, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, append(encoded, '\n'), 0o600); err != nil {
		t.Fatal(err)
	}
}

func mutateJSONFixture(t *testing.T, path string, mutate func(map[string]any)) {
	t.Helper()
	var document map[string]any
	encoded, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(encoded, &document); err != nil {
		t.Fatal(err)
	}
	mutate(document)
	writeJSONFixture(t, path, document)
}
