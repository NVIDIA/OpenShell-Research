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
	"sort"
	"strings"
	"testing"
	"time"
)

func TestVerifyCanaryReconciliation(t *testing.T) {
	finished := time.Now().UTC().Add(-time.Hour)
	manifest := validPilotManifest("canary", finished.Add(-7*24*time.Hour), finished, []string{"gateway-a"})
	source := []pilotSourceIdentity{pilotSourceFixture(t, "gateway-a", 1)}
	manifest.Gateways[0].ExpectedDurableRecords = int64(len(source))

	manifestPath, sourcePath, recoveryPath, destinationPath := writePilotFixture(t, manifest, source, source, source)
	verification, err := verifyCanaryPilotReconciliation(
		manifestPath,
		sourcePath,
		recoveryPath,
		destinationPath,
		fixtureCandidateCommit,
	)
	if err != nil {
		t.Fatal(err)
	}
	if verification.Result != "passed_canary_reconciliation" ||
		verification.DurableSourceRecords != 1 ||
		verification.RecoveryRecords != 1 ||
		verification.DestinationRecords != 1 ||
		len(verification.Gateways) != 1 {
		t.Fatalf("unexpected canary verification: %+v", verification)
	}
	for field, value := range map[string]string{
		"manifest":    verification.ManifestSHA256,
		"source":      verification.SourceProjectionSHA256,
		"recovery":    verification.RecoveryProjectionSHA256,
		"destination": verification.DestinationProjectionSHA256,
	} {
		if !strings.HasPrefix(value, "sha256:") || len(value) != 71 {
			t.Fatalf("%s digest = %q", field, value)
		}
	}
}

func TestPilotStableFileIDMatchesProductionGolden(t *testing.T) {
	record := pilotSourceFixture(t, "gateway-a", 1)
	const expected = "sha256:28a1b7abca7378184dba5dbc629107951088c88a23a8b6d99cc43d48e54014db"
	if record.ID != expected {
		t.Fatalf("pilot file identity = %q, want production golden %q", record.ID, expected)
	}
}

func TestPilotReconciliationAcceptsSandboxOperationalFiles(t *testing.T) {
	record := pilotSourceFixture(t, "gateway-a", 1)
	record.AcquisitionKind = "sandbox.file_log"
	record.SourceInstance = "sandbox-operational-log"
	record.FilePath = "/var/log/openshell/openshell.2026-08-21.log"
	record.FilePathResolved = record.FilePath
	record.Source = "openshell://gateway-a/workspaces/default/sandboxes/sandbox-a/sources/sandbox.file_log"
	record.Type = "com.nvidia.openshell.sandbox.file_log.v1"
	identifier, err := pilotStableFileID(
		record,
		strings.TrimPrefix(record.BodySHA256, "sha256:"),
	)
	if err != nil {
		t.Fatal(err)
	}
	record.ID = identifier
	if err := validatePilotSourceIdentity(record, map[string]struct{}{"gateway-a": {}}); err != nil {
		t.Fatalf("sandbox operational file was not accepted as durable evidence: %v", err)
	}
}

func TestVerifyPilotReconciliationAcrossGateways(t *testing.T) {
	finished := time.Now().UTC().Add(-time.Hour)
	manifest := validPilotManifest("pilot", finished.Add(-30*24*time.Hour), finished, []string{"gateway-a", "gateway-b"})
	source := []pilotSourceIdentity{
		pilotSourceFixture(t, "gateway-a", 1),
		pilotSourceFixture(t, "gateway-b", 1),
	}
	for index := range manifest.Gateways {
		manifest.Gateways[index].ExpectedDurableRecords = 1
	}
	manifest.Gateways[0].Monitoring.StreamGapMetricDelta = 2
	manifest.Gateways[0].Monitoring.StreamWarningEvents = 2

	manifestPath, sourcePath, recoveryPath, destinationPath := writePilotFixture(t, manifest, source, source, source)
	verification, err := verifyCanaryPilotReconciliation(
		manifestPath,
		sourcePath,
		recoveryPath,
		destinationPath,
		fixtureCandidateCommit,
	)
	if err != nil {
		t.Fatal(err)
	}
	if verification.Result != "passed_pilot_reconciliation" ||
		verification.DurableSourceRecords != 2 || len(verification.Gateways) != 2 {
		t.Fatalf("unexpected pilot verification: %+v", verification)
	}
	if verification.Gateways[0].Monitoring.StreamGapMetricDelta != 2 ||
		verification.Gateways[0].Monitoring.StreamWarningEvents != 2 {
		t.Fatalf("reported stream gaps were not retained: %+v", verification.Gateways[0])
	}
}

func TestPilotManifestRejectsIncompleteQualification(t *testing.T) {
	finished := time.Now().UTC().Add(-time.Hour)
	validCanary := validPilotManifest("canary", finished.Add(-7*24*time.Hour), finished, []string{"gateway-a"})
	validPilot := validPilotManifest("pilot", finished.Add(-30*24*time.Hour), finished, []string{"gateway-a", "gateway-b"})

	tests := map[string]pilotManifest{
		"wrong candidate": mutatePilotManifest(validCanary, func(candidate *pilotManifest) {
			candidate.CandidateCommit = strings.Repeat("b", 40)
		}),
		"short canary": mutatePilotManifest(validCanary, func(candidate *pilotManifest) {
			candidate.StartedAt = finished.Add(-6 * 24 * time.Hour).Format(time.RFC3339Nano)
		}),
		"future completion": mutatePilotManifest(validCanary, func(candidate *pilotManifest) {
			candidate.FinishedAt = time.Now().UTC().Add(time.Hour).Format(time.RFC3339Nano)
		}),
		"one gateway pilot": mutatePilotManifest(validPilot, func(candidate *pilotManifest) {
			candidate.Gateways = candidate.Gateways[:1]
		}),
		"queue saturation": mutatePilotManifest(validCanary, func(candidate *pilotManifest) {
			candidate.Gateways[0].Monitoring.MaxQueueUtilization = 1
		}),
		"memory limit exceeded": mutatePilotManifest(validCanary, func(candidate *pilotManifest) {
			candidate.Gateways[0].Monitoring.MaxRSSBytes = candidate.Gateways[0].Monitoring.MemoryLimitBytes + 1
		}),
		"queue not drained": mutatePilotManifest(validCanary, func(candidate *pilotManifest) {
			candidate.Gateways[0].Monitoring.FinalQueueUtilization = 0.06
		}),
		"insufficient monitoring": mutatePilotManifest(validCanary, func(candidate *pilotManifest) {
			candidate.Gateways[0].Monitoring.SampleCount = 1
		}),
		"missing durable evidence": mutatePilotManifest(validCanary, func(candidate *pilotManifest) {
			candidate.Gateways[0].ExpectedDurableRecords = 0
		}),
		"unreported stream gap": mutatePilotManifest(validCanary, func(candidate *pilotManifest) {
			candidate.Gateways[0].Monitoring.StreamGapMetricDelta = 1
		}),
		"unexplained stream gap": mutatePilotManifest(validCanary, func(candidate *pilotManifest) {
			candidate.Gateways[0].Monitoring.StreamGapMetricDelta = 1
			candidate.Gateways[0].Monitoring.StreamWarningEvents = 1
			candidate.Gateways[0].Monitoring.UnexplainedStreamGaps = 1
		}),
		"oom kill": mutatePilotManifest(validCanary, func(candidate *pilotManifest) {
			candidate.Gateways[0].Monitoring.OOMKills = 1
		}),
		"unresolved alert": mutatePilotManifest(validCanary, func(candidate *pilotManifest) {
			candidate.Gateways[0].Monitoring.UnresolvedOperationalAlerts = 1
		}),
	}

	for name, manifest := range tests {
		t.Run(name, func(t *testing.T) {
			if _, _, err := validatePilotManifest(manifest, fixtureCandidateCommit); err == nil {
				t.Fatal("accepted incomplete pilot manifest")
			}
		})
	}
}

func TestPilotReconciliationRejectsLedgerMismatch(t *testing.T) {
	finished := time.Now().UTC().Add(-time.Hour)
	baseManifest := validPilotManifest("canary", finished.Add(-7*24*time.Hour), finished, []string{"gateway-a"})
	baseSource := []pilotSourceIdentity{pilotSourceFixture(t, "gateway-a", 1)}
	baseManifest.Gateways[0].ExpectedDurableRecords = 1

	tests := map[string]struct {
		source      []pilotSourceIdentity
		recovery    []pilotSourceIdentity
		destination []pilotSourceIdentity
	}{
		"missing recovery": {
			source: baseSource, recovery: nil, destination: baseSource,
		},
		"missing destination": {
			source: baseSource, recovery: baseSource, destination: nil,
		},
		"unexpected destination": {
			source: baseSource, recovery: baseSource,
			destination: []pilotSourceIdentity{baseSource[0], pilotSourceFixture(t, "gateway-a", 2)},
		},
		"duplicate recovery": {
			source:      baseSource,
			recovery:    []pilotSourceIdentity{baseSource[0], baseSource[0]},
			destination: baseSource,
		},
		"destination type mismatch": {
			source: baseSource, recovery: baseSource,
			destination: []pilotSourceIdentity{mutatePilotSource(baseSource[0], func(candidate *pilotSourceIdentity) {
				candidate.Type = "com.nvidia.openshell.ocsf.9999.v1"
			})},
		},
		"body hash not bound to id": {
			source: []pilotSourceIdentity{mutatePilotSource(baseSource[0], func(candidate *pilotSourceIdentity) {
				candidate.BodySHA256 = "sha256:" + strings.Repeat("f", 64)
			})},
			recovery: baseSource, destination: baseSource,
		},
	}

	for name, fixture := range tests {
		t.Run(name, func(t *testing.T) {
			manifestPath, sourcePath, recoveryPath, destinationPath := writePilotFixture(
				t,
				baseManifest,
				fixture.source,
				fixture.recovery,
				fixture.destination,
			)
			if _, err := verifyCanaryPilotReconciliation(
				manifestPath,
				sourcePath,
				recoveryPath,
				destinationPath,
				fixtureCandidateCommit,
			); err == nil {
				t.Fatal("accepted mismatched pilot ledgers")
			}
		})
	}
}

func TestPilotSourceProjectionRequiresStrictSortedJSONL(t *testing.T) {
	finished := time.Now().UTC().Add(-time.Hour)
	manifest := validPilotManifest("canary", finished.Add(-7*24*time.Hour), finished, []string{"gateway-a"})
	first := pilotSourceFixture(t, "gateway-a", 1)
	second := pilotSourceFixture(t, "gateway-a", 2)
	manifest.Gateways[0].ExpectedDurableRecords = 2

	for name, source := range map[string][]pilotSourceIdentity{
		"duplicate":    {first, first},
		"out of order": {second, first},
	} {
		t.Run(name, func(t *testing.T) {
			manifestPath, sourcePath, recoveryPath, destinationPath := writePilotFixtureRawOrder(
				t, manifest, source, source, source,
			)
			if _, err := verifyCanaryPilotReconciliation(
				manifestPath,
				sourcePath,
				recoveryPath,
				destinationPath,
				fixtureCandidateCommit,
			); err == nil {
				t.Fatal("accepted non-unique or unsorted source projection")
			}
		})
	}
}

func validPilotManifest(mode string, started, finished time.Time, gateways []string) pilotManifest {
	duration := finished.Sub(started)
	minimumSamples := int64(duration/(15*time.Minute)) + 1
	manifest := pilotManifest{
		SchemaVersion:    pilotManifestSchemaVersion,
		Mode:             mode,
		RunID:            fmt.Sprintf("%s-%d", mode, finished.Unix()),
		CandidateCommit:  fixtureCandidateCommit,
		ImageDigest:      "sha256:" + strings.Repeat("c", 64),
		StartedAt:        started.Format(time.RFC3339Nano),
		FinishedAt:       finished.Format(time.RFC3339Nano),
		ExternalReceiver: "external-staging-observability",
	}
	for _, gatewayID := range gateways {
		manifest.Gateways = append(manifest.Gateways, pilotGatewayManifest{
			GatewayID:              gatewayID,
			ExpectedDurableRecords: 1,
			Monitoring: pilotMonitoring{
				SampleCount:             minimumSamples,
				MaxSampleGapSeconds:     900,
				MemoryLimitBytes:        1024 * 1024 * 1024,
				MaxRSSBytes:             512 * 1024 * 1024,
				MaxQueueUtilization:     0.8,
				FinalQueueUtilization:   0,
				MinDiskFreeBytes:        10 * 1024 * 1024 * 1024,
				MaxCheckpointAgeSeconds: 30,
			},
		})
	}
	return manifest
}

func mutatePilotManifest(original pilotManifest, mutate func(*pilotManifest)) pilotManifest {
	candidate := original
	candidate.Gateways = append([]pilotGatewayManifest(nil), original.Gateways...)
	mutate(&candidate)
	return candidate
}

func pilotSourceFixture(t *testing.T, gatewayID string, recordNumber int64) pilotSourceIdentity {
	t.Helper()
	bodyValue := fmt.Sprintf("body-%s-%d", gatewayID, recordNumber)
	canonicalBody, err := json.Marshal(bodyValue)
	if err != nil {
		t.Fatal(err)
	}
	body := sha256.Sum256(canonicalBody)
	record := pilotSourceIdentity{
		GatewayID:        gatewayID,
		Workspace:        "default",
		AcquisitionKind:  "ocsf.file",
		SourceInstance:   "openshell-ocsf",
		FilePath:         "/var/log/openshell/openshell-ocsf.jsonl",
		FilePathResolved: "/var/log/openshell/openshell-ocsf.jsonl",
		RecordOffset:     (recordNumber - 1) * 1024,
		RecordNumber:     recordNumber,
		BodySHA256:       "sha256:" + hex.EncodeToString(body[:]),
		Source: "openshell://" + gatewayID +
			"/workspaces/default/sandboxes/sandbox-a/sources/ocsf.file",
		Type: "com.nvidia.openshell.ocsf.4001.v1",
	}
	identifier, err := pilotStableFileID(record, strings.TrimPrefix(record.BodySHA256, "sha256:"))
	if err != nil {
		t.Fatal(err)
	}
	record.ID = identifier
	return record
}

func mutatePilotSource(original pilotSourceIdentity, mutate func(*pilotSourceIdentity)) pilotSourceIdentity {
	candidate := original
	mutate(&candidate)
	return candidate
}

func writePilotFixture(
	t *testing.T,
	manifest pilotManifest,
	source []pilotSourceIdentity,
	recovery []pilotSourceIdentity,
	destination []pilotSourceIdentity,
) (string, string, string, string) {
	t.Helper()
	sort.Slice(source, func(left, right int) bool {
		return pilotIdentityKey(source[left].GatewayID, source[left].Source, source[left].ID) <
			pilotIdentityKey(source[right].GatewayID, source[right].Source, source[right].ID)
	})
	sort.Slice(recovery, func(left, right int) bool {
		return pilotIdentityKey(recovery[left].GatewayID, recovery[left].Source, recovery[left].ID) <
			pilotIdentityKey(recovery[right].GatewayID, recovery[right].Source, recovery[right].ID)
	})
	sort.Slice(destination, func(left, right int) bool {
		return pilotIdentityKey(destination[left].GatewayID, destination[left].Source, destination[left].ID) <
			pilotIdentityKey(destination[right].GatewayID, destination[right].Source, destination[right].ID)
	})
	return writePilotFixtureRawOrder(t, manifest, source, recovery, destination)
}

func writePilotFixtureRawOrder(
	t *testing.T,
	manifest pilotManifest,
	source []pilotSourceIdentity,
	recovery []pilotSourceIdentity,
	destination []pilotSourceIdentity,
) (string, string, string, string) {
	t.Helper()
	directory := t.TempDir()
	manifestPath := filepath.Join(directory, "manifest.json")
	sourcePath := filepath.Join(directory, "source.jsonl")
	recoveryPath := filepath.Join(directory, "recovery.jsonl")
	destinationPath := filepath.Join(directory, "destination.jsonl")
	writePilotJSON(t, manifestPath, manifest)
	writePilotJSONL(t, sourcePath, source, true)
	writePilotJSONL(t, recoveryPath, recovery, false)
	writePilotJSONL(t, destinationPath, destination, false)
	return manifestPath, sourcePath, recoveryPath, destinationPath
}

func writePilotJSON(t *testing.T, path string, value any) {
	t.Helper()
	encoded, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, append(encoded, '\n'), 0o600); err != nil {
		t.Fatal(err)
	}
}

func writePilotJSONL(t *testing.T, path string, values []pilotSourceIdentity, source bool) {
	t.Helper()
	file, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if err != nil {
		t.Fatal(err)
	}
	defer func() {
		if closeErr := file.Close(); closeErr != nil {
			t.Errorf("close pilot JSONL fixture: %v", closeErr)
		}
	}()
	for _, value := range values {
		var encoded []byte
		if source {
			encoded, err = json.Marshal(value)
		} else {
			encoded, err = json.Marshal(pilotDeliveredIdentity{
				GatewayID: value.GatewayID,
				Source:    value.Source,
				ID:        value.ID,
				Type:      value.Type,
			})
		}
		if err != nil {
			t.Fatal(err)
		}
		if _, err := file.Write(append(encoded, '\n')); err != nil {
			t.Fatal(err)
		}
	}
}
