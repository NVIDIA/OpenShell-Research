// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/santhosh-tekuri/jsonschema/v6"
)

func TestBuildRecordMatchesProductionIdentityAndUsesBodySandbox(t *testing.T) {
	body := []byte(`{"class_uid":4001,"sandbox":{"id":"sandbox/body id"},"metadata":{"version":"1.8.0"},"secret":"before-redaction"}`)
	record, err := buildRecord(body, 73, 4, int64(len(body)+1), "/var/log/openshell-ocsf.2026-08-30.log", "/logs/openshell-ocsf.2026-08-30.log", "gateway/id", "default", "fallback", "gateway-ocsf-jsonl")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(record.Source, "/sandboxes/sandbox%2Fbody%20id/") {
		t.Fatalf("source did not use the OCSF body sandbox identity: %s", record.Source)
	}
	var original any
	if err := json.Unmarshal(body, &original); err != nil {
		t.Fatal(err)
	}
	canonical, _ := json.Marshal(original)
	bodySum := sha256.Sum256(canonical)
	bodyHash := hex.EncodeToString(bodySum[:])
	identity := map[string]any{
		"gateway_id":             "gateway/id",
		"workspace":              "default",
		"kind":                   "ocsf.file",
		"body_hash":              bodyHash,
		"source_instance":        "gateway-ocsf-jsonl",
		"log.file.path":          "/var/log/openshell-ocsf.2026-08-30.log",
		"log.file.path_resolved": "/logs/openshell-ocsf.2026-08-30.log",
		"log.file.record_offset": int64(73),
		"log.file.record_number": int64(4),
	}
	encoded, _ := json.Marshal(identity)
	expectedSum := sha256.Sum256(encoded)
	expectedID := "sha256:" + hex.EncodeToString(expectedSum[:])
	if record.ID != expectedID {
		t.Fatalf("identity mismatch: got %s want %s", record.ID, expectedID)
	}
	if record.BodySHA256 != "sha256:"+bodyHash {
		t.Fatalf("canonical pre-redaction body hash mismatch: %s", record.BodySHA256)
	}
}

func TestSourceInstanceFromConfig(t *testing.T) {
	path := filepath.Join(t.TempDir(), "collector.yaml")
	config := []byte("processors:\n  memory_limiter: {}\n  openshell/ocsf:\n    source_instance: gateway-ocsf-jsonl\n")
	if err := os.WriteFile(path, config, 0o600); err != nil {
		t.Fatal(err)
	}
	instance, err := sourceInstanceFromConfig(path)
	if err != nil {
		t.Fatal(err)
	}
	if instance != "gateway-ocsf-jsonl" {
		t.Fatalf("source instance = %q", instance)
	}
}

func TestSourceInstanceFromConfigRejectsAmbiguousProcessors(t *testing.T) {
	path := filepath.Join(t.TempDir(), "collector.yaml")
	config := []byte("processors:\n  openshell/first:\n    source_instance: first\n  openshell/second:\n    source_instance: second\n")
	if err := os.WriteFile(path, config, 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := sourceInstanceFromConfig(path); err == nil {
		t.Fatal("ambiguous source instances were accepted")
	}
}

func TestCaptureSourceManifestRecordsCoordinatesAndFileIdentity(t *testing.T) {
	directory := t.TempDir()
	first := []byte(`{"class_uid":4001,"sandbox_id":"sandbox-a"}` + "\n")
	second := []byte(`{"class_uid":6003,"unmapped":{"sandbox_id":"sandbox-b"}}` + "\n")
	path := filepath.Join(directory, "openshell-ocsf.2026-08-30.log")
	if err := os.WriteFile(path, append(first, second...), 0o600); err != nil {
		t.Fatal(err)
	}
	manifest, err := captureSourceManifest(directory, "openshell-ocsf.*.log", "/var/log", "/logs", "gateway", "default", "unknown", "gateway-ocsf-jsonl")
	if err != nil {
		t.Fatal(err)
	}
	if manifest.TotalRecords != 2 || len(manifest.Files) != 1 {
		t.Fatalf("unexpected manifest totals: %#v", manifest)
	}
	file := manifest.Files[0]
	if file.Device == 0 || file.Inode == 0 || file.Size != int64(len(first)+len(second)) {
		t.Fatalf("file identity is incomplete: %#v", file)
	}
	if file.Records[0].RecordNumber != 1 || file.Records[0].RecordOffset != 0 || file.Records[0].EncodedBytes != int64(len(first)) {
		t.Fatalf("first coordinates are wrong: %#v", file.Records[0])
	}
	if file.Records[1].RecordNumber != 2 || file.Records[1].RecordOffset != int64(len(first)) || file.Records[1].EncodedBytes != int64(len(second)) {
		t.Fatalf("second coordinates are wrong: %#v", file.Records[1])
	}
	if !strings.Contains(file.Records[1].Source, "/sandboxes/sandbox-b/") {
		t.Fatalf("second source lacks record sandbox identity: %s", file.Records[1].Source)
	}
}

func TestReconcileRunPassesNaturalRotationRestartAndOutage(t *testing.T) {
	fixture := newReconcileFixture(t)
	report, err := reconcileRun(fixture.metadata, fixture.before, fixture.after, fixture.deliveries, fixture.metrics, fixture.storageBefore, fixture.storageAfter, fixture.runtime)
	if err != nil {
		t.Fatal(err)
	}
	if !report.Complete || report.Result != "passed" {
		t.Fatalf("expected complete passing report: %#v", report)
	}
	if !report.Lifecycle.NaturalDailyRotation.Observed || len(report.Lifecycle.NaturalDailyRotation.NewFiles) != 1 {
		t.Fatalf("natural rotation was not proven: %#v", report.Lifecycle.NaturalDailyRotation)
	}
	if !report.Lifecycle.ExporterRestart.Observed || report.Lifecycle.ExporterRestart.InstanceBefore == report.Lifecycle.ExporterRestart.InstanceAfter {
		t.Fatalf("restart was not proven: %#v", report.Lifecycle.ExporterRestart)
	}
	if !report.Lifecycle.DestinationOutage.HTTP503Exercised || !report.Lifecycle.DestinationOutage.Drained {
		t.Fatalf("outage lifecycle was not proven: %#v", report.Lifecycle.DestinationOutage)
	}
	if report.Reconciliation.MissingDurableRecords != 0 || report.Reconciliation.IdentityCollisions != 0 || report.Reconciliation.DuplicateDeliveries != 1 || !report.Reconciliation.ReplayKeysStable {
		t.Fatalf("reconciliation failed: %#v", report.Reconciliation)
	}
}

func TestReconcileRunAllowsOlderRetentionExpiry(t *testing.T) {
	fixture := newReconcileFixture(t)
	var before sourceManifest
	encoded, err := os.ReadFile(fixture.before)
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(encoded, &before); err != nil {
		t.Fatal(err)
	}
	oldRecord, err := buildRecord([]byte(`{"class_uid":4001,"sandbox_id":"sandbox-old","time":0}`), 0, 1, 55, "/var/log/openshell-ocsf.2026-08-29.log", "/logs/openshell-ocsf.2026-08-29.log", "gateway", "default", "unknown", "gateway-ocsf-jsonl")
	if err != nil {
		t.Fatal(err)
	}
	older := fileManifest{Filename: "openshell-ocsf.2026-08-29.log", LocalPath: "/logs/openshell-ocsf.2026-08-29.log", FilePath: "/var/log/openshell-ocsf.2026-08-29.log", FilePathResolved: "/logs/openshell-ocsf.2026-08-29.log", Device: 10, Inode: 99, Size: 55, ModifiedAt: "2026-08-29T23:59:00Z", ChangedAt: "2026-08-29T23:59:00Z", RecordCount: 1, Records: []fileRecord{oldRecord}}
	before.Files = append([]fileManifest{older}, before.Files...)
	before.TotalRecords++
	writeJSON(t, fixture.before, before)

	report, err := reconcileRun(fixture.metadata, fixture.before, fixture.after, fixture.deliveries, fixture.metrics, fixture.storageBefore, fixture.storageAfter, fixture.runtime)
	if err != nil {
		t.Fatal(err)
	}
	rotation := report.Lifecycle.NaturalDailyRotation
	if !report.Complete || !rotation.Observed || rotation.PreviousFile != "openshell-ocsf.2026-08-30.log" || !rotation.PreviousFileRetained {
		t.Fatalf("retention-aware rotation failed: %#v", rotation)
	}
	if len(rotation.RetentionExpiredFiles) != 1 || rotation.RetentionExpiredFiles[0] != "openshell-ocsf.2026-08-29.log" {
		t.Fatalf("expired files = %#v", rotation.RetentionExpiredFiles)
	}
	if report.Reconciliation.RetainedSourceRecords != 3 || report.Reconciliation.MissingDurableRecords != 0 {
		t.Fatalf("retained reconciliation = %#v", report.Reconciliation)
	}
}

func TestReconcileRunReportsMissingDurableRecord(t *testing.T) {
	fixture := newReconcileFixture(t)
	encoded, err := os.ReadFile(fixture.deliveries)
	if err != nil {
		t.Fatal(err)
	}
	var deliveries []deliveredEvent
	if err := json.Unmarshal(encoded, &deliveries); err != nil {
		t.Fatal(err)
	}
	deliveries = deliveries[:len(deliveries)-2]
	writeJSON(t, fixture.deliveries, deliveries)
	report, err := reconcileRun(fixture.metadata, fixture.before, fixture.after, fixture.deliveries, fixture.metrics, fixture.storageBefore, fixture.storageAfter, fixture.runtime)
	if err != nil {
		t.Fatal(err)
	}
	if report.Complete || report.Result != "failed" || report.Reconciliation.MissingDurableRecords != 1 {
		t.Fatalf("missing durable record was not rejected: %#v", report.Reconciliation)
	}
}

type reconcileFixture struct {
	metadata, before, after, deliveries, metrics, storageBefore, storageAfter, runtime string
}

func newReconcileFixture(t *testing.T) reconcileFixture {
	t.Helper()
	directory := t.TempDir()
	paths := reconcileFixture{
		metadata:      filepath.Join(directory, "metadata.json"),
		before:        filepath.Join(directory, "before.json"),
		after:         filepath.Join(directory, "after.json"),
		deliveries:    filepath.Join(directory, "deliveries.json"),
		metrics:       filepath.Join(directory, "metrics.json"),
		storageBefore: filepath.Join(directory, "storage-before.json"),
		storageAfter:  filepath.Join(directory, "storage-after.json"),
		runtime:       filepath.Join(directory, "runtime.json"),
	}
	build := func(body string, offset, number int64, file string) fileRecord {
		record, err := buildRecord([]byte(body), offset, number, int64(len(body)+1), "/var/log/"+file, "/logs/"+file, "gateway", "default", "unknown", "gateway-ocsf-jsonl")
		if err != nil {
			t.Fatal(err)
		}
		return record
	}
	first := build(`{"class_uid":4001,"sandbox_id":"sandbox-a","time":1}`, 0, 1, "openshell-ocsf.2026-08-30.log")
	second := build(`{"class_uid":4001,"sandbox_id":"sandbox-a","time":2}`, 55, 2, "openshell-ocsf.2026-08-30.log")
	third := build(`{"class_uid":6003,"sandbox_id":"sandbox-b","time":3}`, 0, 1, "openshell-ocsf.2026-08-31.log")
	manifest := func(files []fileManifest, total int64) sourceManifest {
		return sourceManifest{SchemaVersion: schemaVersion, CapturedAt: "2026-08-30T23:59:00Z", GatewayID: "gateway", Workspace: "default", SandboxID: "unknown", AcquisitionKind: "ocsf.file", SourceInstance: "gateway-ocsf-jsonl", SourceDirectory: "/logs", FilePattern: "openshell-ocsf.*.log", LogicalDirectory: "/var/log", ResolvedDirectory: "/logs", Files: files, TotalRecords: total}
	}
	before := manifest([]fileManifest{{Filename: "openshell-ocsf.2026-08-30.log", LocalPath: "/logs/openshell-ocsf.2026-08-30.log", FilePath: "/var/log/openshell-ocsf.2026-08-30.log", FilePathResolved: "/logs/openshell-ocsf.2026-08-30.log", Device: 10, Inode: 100, Size: 55, ModifiedAt: "2026-08-30T23:59:00Z", ChangedAt: "2026-08-30T23:59:00Z", RecordCount: 1, Records: []fileRecord{first}}}, 1)
	after := manifest([]fileManifest{
		{Filename: "openshell-ocsf.2026-08-30.log", LocalPath: "/logs/openshell-ocsf.2026-08-30.log", FilePath: "/var/log/openshell-ocsf.2026-08-30.log", FilePathResolved: "/logs/openshell-ocsf.2026-08-30.log", Device: 10, Inode: 100, Size: 110, ModifiedAt: "2026-08-31T00:00:00Z", ChangedAt: "2026-08-31T00:00:00Z", RecordCount: 2, Records: []fileRecord{first, second}},
		{Filename: "openshell-ocsf.2026-08-31.log", LocalPath: "/logs/openshell-ocsf.2026-08-31.log", FilePath: "/var/log/openshell-ocsf.2026-08-31.log", FilePathResolved: "/logs/openshell-ocsf.2026-08-31.log", Device: 10, Inode: 101, Size: 55, ModifiedAt: "2026-08-31T00:01:00Z", ChangedAt: "2026-08-31T00:01:00Z", RecordCount: 1, Records: []fileRecord{third}},
	}, 3)
	writeJSON(t, paths.before, before)
	writeJSON(t, paths.after, after)
	metadata := runMetadata{SchemaVersion: schemaVersion, RunID: "run-1", StartedAt: "2026-08-30T23:59:00Z", Hooks: map[string]string{"destination_outage": "sha256:" + strings.Repeat("e", 64), "destination_restore": "sha256:" + strings.Repeat("e", 64), "real_activity": "sha256:" + strings.Repeat("e", 64), "exporter_restart": "sha256:" + strings.Repeat("e", 64), "exporter_identity": "sha256:" + strings.Repeat("e", 64)}, Limitations: []string{"test fixture"}}
	metadata.Candidate.Commit = strings.Repeat("a", 40)
	metadata.Candidate.WorktreeClean = true
	metadata.Candidate.ExporterVersion = "0.0.4-rc.2"
	metadata.Candidate.ExporterImage = "registry/exporter@sha256:" + strings.Repeat("b", 64)
	metadata.Candidate.ConfigSHA256 = "sha256:" + strings.Repeat("d", 64)
	metadata.Gateway.Version = "0.0.113"
	metadata.Gateway.Image = "registry/gateway@sha256:" + strings.Repeat("c", 64)
	writeJSON(t, paths.metadata, metadata)
	writeJSON(t, paths.metrics, map[string]metricPoint{
		"baseline":      {CapturedAt: "2026-08-30T23:59:00Z", MetricsSHA256: "sha256:" + strings.Repeat("f", 64), QueueSize: 0, QueueCapacity: 100, RetryableFailures: 2, DeliveredEvents: 10},
		"outage":        {CapturedAt: "2026-08-31T00:00:00Z", MetricsSHA256: "sha256:" + strings.Repeat("f", 64), QueueSize: 3, QueueCapacity: 100, RetryableFailures: 4, DeliveredEvents: 10},
		"after_restart": {CapturedAt: "2026-08-31T00:01:00Z", MetricsSHA256: "sha256:" + strings.Repeat("f", 64), QueueSize: 3, QueueCapacity: 100, RetryableFailures: 0, DeliveredEvents: 0},
		"drained":       {CapturedAt: "2026-08-31T00:02:00Z", MetricsSHA256: "sha256:" + strings.Repeat("f", 64), QueueSize: 0, QueueCapacity: 100, RetryableFailures: 0, DeliveredEvents: 3},
	})
	storage := storageSnapshot{CapturedAt: "2026-08-31T00:01:00Z", Roles: map[string]storageRole{
		"checkpoints": {Path: "/state/checkpoints", Device: 10, Inode: 201, FileCount: 1, ManifestSHA256: "sha256:" + strings.Repeat("1", 64)},
		"queue":       {Path: "/state/queue", Device: 10, Inode: 202, FileCount: 1, ManifestSHA256: "sha256:" + strings.Repeat("1", 64)},
		"recovery":    {Path: "/state/recovery", Device: 10, Inode: 203, FileCount: 1, ManifestSHA256: "sha256:" + strings.Repeat("1", 64)},
	}}
	writeJSON(t, paths.storageBefore, storage)
	writeJSON(t, paths.storageAfter, storage)
	writeJSON(t, paths.runtime, runtimeObservations{ExporterInstanceBefore: "pod-a", ExporterInstanceAfter: "pod-b", DestinationOutageStatus: 503, DestinationRestoreStatus: 204})
	writeJSON(t, paths.deliveries, []deliveredEvent{
		{Source: first.Source, ID: first.ID, Type: first.Type},
		{Source: second.Source, ID: second.ID, Type: second.Type},
		{Source: second.Source, ID: second.ID, Type: second.Type},
		{Source: third.Source, ID: third.ID, Type: third.Type},
	})
	return paths
}

func writeJSON(t *testing.T, path string, value any) {
	t.Helper()
	encoded, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, encoded, 0o600); err != nil {
		t.Fatal(err)
	}
}

func TestPassingReportMatchesQualificationSchema(t *testing.T) {
	fixture := newReconcileFixture(t)
	report, err := reconcileRun(fixture.metadata, fixture.before, fixture.after, fixture.deliveries, fixture.metrics, fixture.storageBefore, fixture.storageAfter, fixture.runtime)
	if err != nil {
		t.Fatal(err)
	}
	encodedSchema, err := os.ReadFile("../../integration/qualification/ocsf-file-lifecycle-report-v1.schema.json")
	if err != nil {
		t.Fatal(err)
	}
	var schemaDocument map[string]any
	if err := json.Unmarshal(encodedSchema, &schemaDocument); err != nil {
		t.Fatal(err)
	}
	compiler := jsonschema.NewCompiler()
	identifier, _ := schemaDocument["$id"].(string)
	if err := compiler.AddResource(identifier, schemaDocument); err != nil {
		t.Fatal(err)
	}
	compiled, err := compiler.Compile(identifier)
	if err != nil {
		t.Fatal(err)
	}
	encodedReport, err := json.Marshal(report)
	if err != nil {
		t.Fatal(err)
	}
	var reportDocument any
	if err := json.Unmarshal(encodedReport, &reportDocument); err != nil {
		t.Fatal(err)
	}
	if err := compiled.Validate(reportDocument); err != nil {
		t.Fatal(err)
	}
}
