// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package conformance

import (
	"bufio"
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

const (
	pilotManifestSchemaVersion = "1.0"
	pilotVerificationFile      = "canary-pilot-verification.json"
	pilotMaximumLedgerLine     = 1024 * 1024
	pilotMaximumSampleGap      = 15 * time.Minute
	pilotMaximumFinalQueue     = 0.05
)

type pilotManifest struct {
	SchemaVersion    string                 `json:"schema_version"`
	Mode             string                 `json:"mode"`
	RunID            string                 `json:"run_id"`
	CandidateCommit  string                 `json:"candidate_commit"`
	ImageDigest      string                 `json:"image_digest"`
	StartedAt        string                 `json:"started_at"`
	FinishedAt       string                 `json:"finished_at"`
	ExternalReceiver string                 `json:"external_receiver"`
	Gateways         []pilotGatewayManifest `json:"gateways"`
}

type pilotGatewayManifest struct {
	GatewayID              string          `json:"gateway_id"`
	ExpectedDurableRecords int64           `json:"expected_durable_records"`
	Monitoring             pilotMonitoring `json:"monitoring"`
}

type pilotMonitoring struct {
	SampleCount                 int64   `json:"sample_count"`
	MaxSampleGapSeconds         int64   `json:"max_sample_gap_seconds"`
	MemoryLimitBytes            int64   `json:"memory_limit_bytes"`
	MaxRSSBytes                 int64   `json:"max_rss_bytes"`
	MaxQueueUtilization         float64 `json:"max_queue_utilization"`
	FinalQueueUtilization       float64 `json:"final_queue_utilization"`
	MinDiskFreeBytes            int64   `json:"min_disk_free_bytes"`
	MaxCheckpointAgeSeconds     float64 `json:"max_checkpoint_age_seconds"`
	RestartCount                int64   `json:"restart_count"`
	UnexplainedRestarts         int64   `json:"unexplained_restarts"`
	OOMKills                    int64   `json:"oom_kills"`
	StreamGapMetricDelta        int64   `json:"stream_gap_metric_delta"`
	StreamWarningEvents         int64   `json:"stream_warning_events"`
	UnexplainedStreamGaps       int64   `json:"unexplained_stream_gaps"`
	UnresolvedOperationalAlerts int64   `json:"unresolved_operational_alerts"`
}

type pilotSourceIdentity struct {
	GatewayID        string `json:"gateway_id"`
	Workspace        string `json:"workspace"`
	AcquisitionKind  string `json:"acquisition_kind"`
	SourceInstance   string `json:"source_instance"`
	FilePath         string `json:"file_path"`
	FilePathResolved string `json:"file_path_resolved"`
	RecordOffset     int64  `json:"record_offset"`
	RecordNumber     int64  `json:"record_number"`
	BodySHA256       string `json:"body_sha256"`
	Source           string `json:"source"`
	ID               string `json:"id"`
	Type             string `json:"type"`
}

type pilotDeliveredIdentity struct {
	GatewayID string `json:"gateway_id"`
	Source    string `json:"source"`
	ID        string `json:"id"`
	Type      string `json:"type"`
}

type pilotGatewayVerification struct {
	GatewayID            string          `json:"gateway_id"`
	DurableSourceRecords int64           `json:"durable_source_records"`
	RecoveryRecords      int64           `json:"recovery_records"`
	DestinationRecords   int64           `json:"destination_records"`
	Monitoring           pilotMonitoring `json:"monitoring"`
}

type pilotVerification struct {
	SchemaVersion               string                     `json:"schema_version"`
	Result                      string                     `json:"result"`
	VerifiedAt                  string                     `json:"verified_at"`
	Mode                        string                     `json:"mode"`
	RunID                       string                     `json:"run_id"`
	CandidateCommit             string                     `json:"candidate_commit"`
	ImageDigest                 string                     `json:"image_digest"`
	StartedAt                   string                     `json:"started_at"`
	FinishedAt                  string                     `json:"finished_at"`
	DurationHours               float64                    `json:"duration_hours"`
	ExternalReceiver            string                     `json:"external_receiver"`
	Gateways                    []pilotGatewayVerification `json:"gateways"`
	DurableSourceRecords        int64                      `json:"durable_source_records"`
	RecoveryRecords             int64                      `json:"recovery_records"`
	DestinationRecords          int64                      `json:"destination_records"`
	ManifestSHA256              string                     `json:"manifest_sha256"`
	SourceProjectionSHA256      string                     `json:"source_projection_sha256"`
	RecoveryProjectionSHA256    string                     `json:"recovery_projection_sha256"`
	DestinationProjectionSHA256 string                     `json:"destination_projection_sha256"`
	Limitations                 []string                   `json:"limitations"`
}

type pilotLedgerCounts struct {
	ByGateway map[string]int64
	Total     int64
}

func TestVerifyCanaryPilotReconciliation(t *testing.T) {
	if os.Getenv("CONFORMANCE_VERIFY_CANARY_PILOT") != "true" {
		t.Skip("set CONFORMANCE_VERIFY_CANARY_PILOT=true to verify canary or pilot reconciliation evidence")
	}
	manifestPath := requiredVerificationEnv(t, "CONFORMANCE_PILOT_MANIFEST")
	sourcePath := requiredVerificationEnv(t, "CONFORMANCE_PILOT_SOURCE_PROJECTION")
	recoveryPath := requiredVerificationEnv(t, "CONFORMANCE_PILOT_RECOVERY_PROJECTION")
	destinationPath := requiredVerificationEnv(t, "CONFORMANCE_PILOT_DESTINATION_PROJECTION")
	reportDirectory := requiredVerificationEnv(t, "CONFORMANCE_REPORT_DIR")

	verification, err := verifyCanaryPilotReconciliation(
		manifestPath,
		sourcePath,
		recoveryPath,
		destinationPath,
		requiredCandidateCommit(t),
	)
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(reportDirectory, pilotVerificationFile)
	if err := writeRestrictedEvidence(path, verification); err != nil {
		t.Fatal(err)
	}
	t.Logf("verified %s reconciliation: %s", verification.Mode, path)
}

func verifyCanaryPilotReconciliation(
	manifestPath string,
	sourcePath string,
	recoveryPath string,
	destinationPath string,
	expectedCommit string,
) (pilotVerification, error) {
	manifest, manifestEncoded, startedAt, finishedAt, err := readAndValidatePilotManifest(manifestPath, expectedCommit)
	if err != nil {
		return pilotVerification{}, err
	}

	counts, err := reconcilePilotLedgers(manifest, sourcePath, recoveryPath, destinationPath)
	if err != nil {
		return pilotVerification{}, err
	}

	gatewayEvidence := make([]pilotGatewayVerification, 0, len(manifest.Gateways))
	for _, gateway := range manifest.Gateways {
		observed := counts.ByGateway[gateway.GatewayID]
		if observed != gateway.ExpectedDurableRecords {
			return pilotVerification{}, fmt.Errorf(
				"gateway %q durable source count is %d, manifest requires %d",
				gateway.GatewayID,
				observed,
				gateway.ExpectedDurableRecords,
			)
		}
		gatewayEvidence = append(gatewayEvidence, pilotGatewayVerification{
			GatewayID:            gateway.GatewayID,
			DurableSourceRecords: observed,
			RecoveryRecords:      observed,
			DestinationRecords:   observed,
			Monitoring:           gateway.Monitoring,
		})
	}

	sourceDigest, err := digestPilotFile(sourcePath)
	if err != nil {
		return pilotVerification{}, fmt.Errorf("digest source projection: %w", err)
	}
	recoveryDigest, err := digestPilotFile(recoveryPath)
	if err != nil {
		return pilotVerification{}, fmt.Errorf("digest recovery projection: %w", err)
	}
	destinationDigest, err := digestPilotFile(destinationPath)
	if err != nil {
		return pilotVerification{}, fmt.Errorf("digest destination projection: %w", err)
	}

	return pilotVerification{
		SchemaVersion:               pilotManifestSchemaVersion,
		Result:                      "passed_" + manifest.Mode + "_reconciliation",
		VerifiedAt:                  time.Now().UTC().Format(time.RFC3339Nano),
		Mode:                        manifest.Mode,
		RunID:                       manifest.RunID,
		CandidateCommit:             manifest.CandidateCommit,
		ImageDigest:                 manifest.ImageDigest,
		StartedAt:                   manifest.StartedAt,
		FinishedAt:                  manifest.FinishedAt,
		DurationHours:               finishedAt.Sub(startedAt).Hours(),
		ExternalReceiver:            manifest.ExternalReceiver,
		Gateways:                    gatewayEvidence,
		DurableSourceRecords:        counts.Total,
		RecoveryRecords:             counts.Total,
		DestinationRecords:          counts.Total,
		ManifestSHA256:              evidenceDigest(manifestEncoded),
		SourceProjectionSHA256:      sourceDigest,
		RecoveryProjectionSHA256:    recoveryDigest,
		DestinationProjectionSHA256: destinationDigest,
		Limitations: []string{
			"This verifies identity-only source, redacted recovery, and independent destination projections supplied by the operator.",
			"It does not authenticate the projection producer, replace signed release evidence, or prove source activity that never reached the source ledger.",
			"Non-resumable WatchSandbox and Relay completeness is limited to reported gap metrics and warning evidence.",
		},
	}, nil
}

func readAndValidatePilotManifest(
	path string,
	expectedCommit string,
) (pilotManifest, []byte, time.Time, time.Time, error) {
	encoded, err := os.ReadFile(path)
	if err != nil {
		return pilotManifest{}, nil, time.Time{}, time.Time{}, fmt.Errorf("read pilot manifest: %w", err)
	}
	if len(encoded) > 4*1024*1024 {
		return pilotManifest{}, nil, time.Time{}, time.Time{}, fmt.Errorf("pilot manifest exceeds 4 MiB")
	}
	var manifest pilotManifest
	decoder := json.NewDecoder(bytes.NewReader(encoded))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&manifest); err != nil {
		return pilotManifest{}, nil, time.Time{}, time.Time{}, fmt.Errorf("decode pilot manifest: %w", err)
	}
	if err := requireJSONEOF(decoder); err != nil {
		return pilotManifest{}, nil, time.Time{}, time.Time{}, fmt.Errorf("decode pilot manifest: %w", err)
	}
	startedAt, finishedAt, err := validatePilotManifest(manifest, expectedCommit)
	if err != nil {
		return pilotManifest{}, nil, time.Time{}, time.Time{}, err
	}
	return manifest, encoded, startedAt, finishedAt, nil
}

func validatePilotManifest(
	manifest pilotManifest,
	expectedCommit string,
) (time.Time, time.Time, error) {
	if manifest.SchemaVersion != pilotManifestSchemaVersion {
		return time.Time{}, time.Time{}, fmt.Errorf("pilot manifest has unsupported schema_version %q", manifest.SchemaVersion)
	}
	if manifest.Mode != "canary" && manifest.Mode != "pilot" {
		return time.Time{}, time.Time{}, fmt.Errorf("mode must be canary or pilot")
	}
	if err := validateSafeEvidenceLabel(manifest.RunID, "run_id"); err != nil {
		return time.Time{}, time.Time{}, err
	}
	if err := requireMatchingCandidateCommit(manifest.CandidateCommit, expectedCommit, "pilot manifest"); err != nil {
		return time.Time{}, time.Time{}, err
	}
	if !strings.HasPrefix(manifest.ImageDigest, "sha256:") {
		return time.Time{}, time.Time{}, fmt.Errorf("image_digest must use sha256")
	}
	if err := validateHexIdentifier(strings.TrimPrefix(manifest.ImageDigest, "sha256:"), 64, "image_digest"); err != nil {
		return time.Time{}, time.Time{}, err
	}
	if err := validateSafeEvidenceLabel(manifest.ExternalReceiver, "external_receiver"); err != nil {
		return time.Time{}, time.Time{}, err
	}
	startedAt, err := time.Parse(time.RFC3339Nano, manifest.StartedAt)
	if err != nil {
		return time.Time{}, time.Time{}, fmt.Errorf("started_at must be RFC3339: %w", err)
	}
	finishedAt, err := time.Parse(time.RFC3339Nano, manifest.FinishedAt)
	if err != nil || !finishedAt.After(startedAt) {
		return time.Time{}, time.Time{}, fmt.Errorf("finished_at must be RFC3339 and after started_at")
	}
	if finishedAt.After(time.Now().UTC().Add(5 * time.Minute)) {
		return time.Time{}, time.Time{}, fmt.Errorf("finished_at cannot be in the future")
	}
	duration := finishedAt.Sub(startedAt)
	switch manifest.Mode {
	case "canary":
		if duration < 7*24*time.Hour || len(manifest.Gateways) != 1 {
			return time.Time{}, time.Time{}, fmt.Errorf("canary requires at least seven days and exactly one gateway")
		}
	case "pilot":
		if duration < 30*24*time.Hour || len(manifest.Gateways) < 2 {
			return time.Time{}, time.Time{}, fmt.Errorf("pilot requires at least 30 days and at least two gateways")
		}
	}

	seen := map[string]struct{}{}
	for _, gateway := range manifest.Gateways {
		if err := validateSafeEvidenceLabel(gateway.GatewayID, "gateway_id"); err != nil {
			return time.Time{}, time.Time{}, err
		}
		if _, duplicate := seen[gateway.GatewayID]; duplicate {
			return time.Time{}, time.Time{}, fmt.Errorf("duplicate gateway_id %q", gateway.GatewayID)
		}
		seen[gateway.GatewayID] = struct{}{}
		if gateway.ExpectedDurableRecords <= 0 {
			return time.Time{}, time.Time{}, fmt.Errorf("gateway %q must expect durable source records", gateway.GatewayID)
		}
		if err := validatePilotMonitoring(gateway.GatewayID, gateway.Monitoring, duration); err != nil {
			return time.Time{}, time.Time{}, err
		}
	}
	return startedAt, finishedAt, nil
}

func validatePilotMonitoring(gatewayID string, monitoring pilotMonitoring, duration time.Duration) error {
	if monitoring.MaxSampleGapSeconds <= 0 || time.Duration(monitoring.MaxSampleGapSeconds)*time.Second > pilotMaximumSampleGap {
		return fmt.Errorf("gateway %q monitoring gap must be positive and at most 15 minutes", gatewayID)
	}
	minimumSamples := int64(math.Ceil(duration.Seconds() / float64(monitoring.MaxSampleGapSeconds)))
	if monitoring.SampleCount < minimumSamples {
		return fmt.Errorf("gateway %q monitoring sample_count is %d, need at least %d", gatewayID, monitoring.SampleCount, minimumSamples)
	}
	if monitoring.MemoryLimitBytes <= 0 || monitoring.MaxRSSBytes <= 0 ||
		monitoring.MaxRSSBytes > monitoring.MemoryLimitBytes ||
		monitoring.MinDiskFreeBytes <= 0 || monitoring.MaxCheckpointAgeSeconds < 0 {
		return fmt.Errorf("gateway %q monitoring resource envelope is incomplete", gatewayID)
	}
	if monitoring.MaxQueueUtilization < 0 || monitoring.MaxQueueUtilization >= 1 ||
		monitoring.FinalQueueUtilization < 0 || monitoring.FinalQueueUtilization > pilotMaximumFinalQueue {
		return fmt.Errorf("gateway %q queue utilization is saturated or did not drain", gatewayID)
	}
	for field, value := range map[string]int64{
		"restart_count":                 monitoring.RestartCount,
		"unexplained_restarts":          monitoring.UnexplainedRestarts,
		"oom_kills":                     monitoring.OOMKills,
		"stream_gap_metric_delta":       monitoring.StreamGapMetricDelta,
		"stream_warning_events":         monitoring.StreamWarningEvents,
		"unexplained_stream_gaps":       monitoring.UnexplainedStreamGaps,
		"unresolved_operational_alerts": monitoring.UnresolvedOperationalAlerts,
	} {
		if value < 0 {
			return fmt.Errorf("gateway %q %s cannot be negative", gatewayID, field)
		}
	}
	if monitoring.OOMKills != 0 || monitoring.UnexplainedRestarts != 0 ||
		monitoring.UnexplainedStreamGaps != 0 || monitoring.UnresolvedOperationalAlerts != 0 {
		return fmt.Errorf("gateway %q has unexplained failures or unresolved alerts", gatewayID)
	}
	if monitoring.StreamGapMetricDelta != monitoring.StreamWarningEvents {
		return fmt.Errorf("gateway %q stream gap metric delta does not match emitted warning evidence", gatewayID)
	}
	return nil
}

func reconcilePilotLedgers(
	manifest pilotManifest,
	sourcePath string,
	recoveryPath string,
	destinationPath string,
) (pilotLedgerCounts, error) {
	allowedGateways := make(map[string]struct{}, len(manifest.Gateways))
	for _, gateway := range manifest.Gateways {
		allowedGateways[gateway.GatewayID] = struct{}{}
	}

	source, err := newPilotSourceReader(sourcePath, allowedGateways)
	if err != nil {
		return pilotLedgerCounts{}, err
	}
	defer source.close()
	recovery, err := newPilotDeliveredReader(recoveryPath, "recovery", allowedGateways)
	if err != nil {
		return pilotLedgerCounts{}, err
	}
	defer recovery.close()
	destination, err := newPilotDeliveredReader(destinationPath, "destination", allowedGateways)
	if err != nil {
		return pilotLedgerCounts{}, err
	}
	defer destination.close()

	counts := pilotLedgerCounts{ByGateway: map[string]int64{}}
	for {
		sourceRecord, sourceErr := source.next()
		if sourceErr == io.EOF {
			if _, err := recovery.next(); err != io.EOF {
				if err == nil {
					return pilotLedgerCounts{}, fmt.Errorf("recovery projection contains an unexpected identity after source EOF")
				}
				return pilotLedgerCounts{}, err
			}
			if _, err := destination.next(); err != io.EOF {
				if err == nil {
					return pilotLedgerCounts{}, fmt.Errorf("destination projection contains an unexpected identity after source EOF")
				}
				return pilotLedgerCounts{}, err
			}
			break
		}
		if sourceErr != nil {
			return pilotLedgerCounts{}, sourceErr
		}

		recoveryRecord, err := recovery.next()
		if err == io.EOF {
			return pilotLedgerCounts{}, fmt.Errorf("recovery projection is missing %s", pilotIdentityKey(sourceRecord.GatewayID, sourceRecord.Source, sourceRecord.ID))
		}
		if err != nil {
			return pilotLedgerCounts{}, err
		}
		destinationRecord, err := destination.next()
		if err == io.EOF {
			return pilotLedgerCounts{}, fmt.Errorf("destination projection is missing %s", pilotIdentityKey(sourceRecord.GatewayID, sourceRecord.Source, sourceRecord.ID))
		}
		if err != nil {
			return pilotLedgerCounts{}, err
		}

		sourceKey := pilotIdentityKey(sourceRecord.GatewayID, sourceRecord.Source, sourceRecord.ID)
		if recoveryKey := pilotIdentityKey(recoveryRecord.GatewayID, recoveryRecord.Source, recoveryRecord.ID); recoveryKey != sourceKey {
			return pilotLedgerCounts{}, fmt.Errorf("recovery identity mismatch: source=%s recovery=%s", sourceKey, recoveryKey)
		}
		if destinationKey := pilotIdentityKey(destinationRecord.GatewayID, destinationRecord.Source, destinationRecord.ID); destinationKey != sourceKey {
			return pilotLedgerCounts{}, fmt.Errorf("destination identity mismatch: source=%s destination=%s", sourceKey, destinationKey)
		}
		if recoveryRecord.Type != sourceRecord.Type || destinationRecord.Type != sourceRecord.Type {
			return pilotLedgerCounts{}, fmt.Errorf("event type mismatch for %s", sourceKey)
		}
		counts.ByGateway[sourceRecord.GatewayID]++
		counts.Total++
	}
	if counts.Total == 0 {
		return pilotLedgerCounts{}, fmt.Errorf("source projection contains no durable records")
	}
	return counts, nil
}

type pilotSourceReader struct {
	file            *os.File
	scanner         *bufio.Scanner
	line            int
	previousKey     string
	allowedGateways map[string]struct{}
}

func newPilotSourceReader(path string, allowed map[string]struct{}) (*pilotSourceReader, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, fmt.Errorf("open source projection: %w", err)
	}
	scanner := bufio.NewScanner(file)
	scanner.Buffer(make([]byte, 64*1024), pilotMaximumLedgerLine)
	return &pilotSourceReader{file: file, scanner: scanner, allowedGateways: allowed}, nil
}

func (reader *pilotSourceReader) close() { _ = reader.file.Close() }

func (reader *pilotSourceReader) next() (pilotSourceIdentity, error) {
	if !reader.scanner.Scan() {
		if err := reader.scanner.Err(); err != nil {
			return pilotSourceIdentity{}, fmt.Errorf("read source projection: %w", err)
		}
		return pilotSourceIdentity{}, io.EOF
	}
	reader.line++
	var record pilotSourceIdentity
	if err := decodeStrictJSONLine(reader.scanner.Bytes(), &record); err != nil {
		return pilotSourceIdentity{}, fmt.Errorf("source projection line %d: %w", reader.line, err)
	}
	if err := validatePilotSourceIdentity(record, reader.allowedGateways); err != nil {
		return pilotSourceIdentity{}, fmt.Errorf("source projection line %d: %w", reader.line, err)
	}
	key := pilotIdentityKey(record.GatewayID, record.Source, record.ID)
	if reader.previousKey != "" && key <= reader.previousKey {
		return pilotSourceIdentity{}, fmt.Errorf("source projection line %d is not strictly sorted and unique", reader.line)
	}
	reader.previousKey = key
	return record, nil
}

type pilotDeliveredReader struct {
	file            *os.File
	scanner         *bufio.Scanner
	kind            string
	line            int
	previousKey     string
	allowedGateways map[string]struct{}
}

func newPilotDeliveredReader(path, kind string, allowed map[string]struct{}) (*pilotDeliveredReader, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, fmt.Errorf("open %s projection: %w", kind, err)
	}
	scanner := bufio.NewScanner(file)
	scanner.Buffer(make([]byte, 64*1024), pilotMaximumLedgerLine)
	return &pilotDeliveredReader{file: file, scanner: scanner, kind: kind, allowedGateways: allowed}, nil
}

func (reader *pilotDeliveredReader) close() { _ = reader.file.Close() }

func (reader *pilotDeliveredReader) next() (pilotDeliveredIdentity, error) {
	if !reader.scanner.Scan() {
		if err := reader.scanner.Err(); err != nil {
			return pilotDeliveredIdentity{}, fmt.Errorf("read %s projection: %w", reader.kind, err)
		}
		return pilotDeliveredIdentity{}, io.EOF
	}
	reader.line++
	var record pilotDeliveredIdentity
	if err := decodeStrictJSONLine(reader.scanner.Bytes(), &record); err != nil {
		return pilotDeliveredIdentity{}, fmt.Errorf("%s projection line %d: %w", reader.kind, reader.line, err)
	}
	if err := validatePilotDeliveredIdentity(record, reader.allowedGateways); err != nil {
		return pilotDeliveredIdentity{}, fmt.Errorf("%s projection line %d: %w", reader.kind, reader.line, err)
	}
	key := pilotIdentityKey(record.GatewayID, record.Source, record.ID)
	if reader.previousKey != "" && key <= reader.previousKey {
		return pilotDeliveredIdentity{}, fmt.Errorf("%s projection line %d is not strictly sorted and unique", reader.kind, reader.line)
	}
	reader.previousKey = key
	return record, nil
}

func validatePilotSourceIdentity(record pilotSourceIdentity, allowed map[string]struct{}) error {
	if err := validatePilotDeliveredIdentity(pilotDeliveredIdentity{
		GatewayID: record.GatewayID,
		Source:    record.Source,
		ID:        record.ID,
		Type:      record.Type,
	}, allowed); err != nil {
		return err
	}
	if err := validateSafeEvidenceLabel(record.Workspace, "workspace"); err != nil {
		return err
	}
	if record.AcquisitionKind != "ocsf.file" &&
		record.AcquisitionKind != "sandbox.file_log" &&
		record.AcquisitionKind != "nemo_relay.log" {
		return fmt.Errorf("acquisition_kind must be a checkpointed file profile")
	}
	for field, value := range map[string]string{
		"source_instance":    record.SourceInstance,
		"file_path":          record.FilePath,
		"file_path_resolved": record.FilePathResolved,
	} {
		if err := validateSafeEvidenceLabel(value, field); err != nil {
			return err
		}
	}
	if record.RecordOffset < 0 || record.RecordNumber <= 0 {
		return fmt.Errorf("file record_offset must be nonnegative and record_number must be positive")
	}
	if !strings.HasPrefix(record.BodySHA256, "sha256:") {
		return fmt.Errorf("body_sha256 must use sha256")
	}
	bodyHash := strings.TrimPrefix(record.BodySHA256, "sha256:")
	if err := validateHexIdentifier(bodyHash, 64, "body_sha256"); err != nil {
		return err
	}
	expectedSourcePrefix := "openshell://" + url.PathEscape(record.GatewayID) +
		"/workspaces/" + url.PathEscape(record.Workspace) + "/sandboxes/"
	expectedSourceSuffix := "/sources/" + url.PathEscape(record.AcquisitionKind)
	if !strings.HasPrefix(record.Source, expectedSourcePrefix) || !strings.HasSuffix(record.Source, expectedSourceSuffix) {
		return fmt.Errorf("source is inconsistent with gateway, workspace, or acquisition kind")
	}
	expectedID, err := pilotStableFileID(record, bodyHash)
	if err != nil {
		return err
	}
	if record.ID != expectedID {
		return fmt.Errorf("id does not bind the supplied pre-redaction body hash and file coordinates")
	}
	return nil
}

func validatePilotDeliveredIdentity(record pilotDeliveredIdentity, allowed map[string]struct{}) error {
	if _, ok := allowed[record.GatewayID]; !ok {
		return fmt.Errorf("gateway_id %q is not declared by the manifest", record.GatewayID)
	}
	if !strings.HasPrefix(record.Source, "openshell://") {
		return fmt.Errorf("source must use openshell://")
	}
	if err := validateCloudEventID(record.ID); err != nil {
		return err
	}
	if err := validateSafeEvidenceLabel(record.Type, "type"); err != nil {
		return err
	}
	return nil
}

func pilotStableFileID(record pilotSourceIdentity, bodyHash string) (string, error) {
	identity := map[string]any{
		"gateway_id":             record.GatewayID,
		"workspace":              record.Workspace,
		"kind":                   record.AcquisitionKind,
		"body_hash":              bodyHash,
		"source_instance":        record.SourceInstance,
		"log.file.path":          record.FilePath,
		"log.file.path_resolved": record.FilePathResolved,
		"log.file.record_offset": record.RecordOffset,
		"log.file.record_number": record.RecordNumber,
	}
	encoded, err := json.Marshal(identity)
	if err != nil {
		return "", fmt.Errorf("marshal file identity: %w", err)
	}
	sum := sha256.Sum256(encoded)
	return "sha256:" + hex.EncodeToString(sum[:]), nil
}

func pilotIdentityKey(gatewayID, source, id string) string {
	return gatewayID + "\x00" + source + "\x00" + id
}

func decodeStrictJSONLine(encoded []byte, target any) error {
	decoder := json.NewDecoder(bytes.NewReader(encoded))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(target); err != nil {
		return err
	}
	return requireJSONEOF(decoder)
}

func requireJSONEOF(decoder *json.Decoder) error {
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		if err == nil {
			return fmt.Errorf("multiple JSON values are not allowed")
		}
		return err
	}
	return nil
}

func digestPilotFile(path string) (string, error) {
	file, err := os.Open(path)
	if err != nil {
		return "", err
	}
	hash := sha256.New()
	if _, err := io.Copy(hash, file); err != nil {
		_ = file.Close()
		return "", err
	}
	if err := file.Close(); err != nil {
		return "", err
	}
	return "sha256:" + hex.EncodeToString(hash.Sum(nil)), nil
}
