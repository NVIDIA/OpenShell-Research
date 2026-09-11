// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package main

import (
	"bufio"
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net/url"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"syscall"
	"time"

	"go.yaml.in/yaml/v3"
)

const (
	schemaVersion  = "1.0"
	maximumLine    = 8 << 20
	maximumMissing = 100
)

type fileManifest struct {
	Filename         string       `json:"filename"`
	LocalPath        string       `json:"local_path"`
	FilePath         string       `json:"file_path"`
	FilePathResolved string       `json:"file_path_resolved"`
	Device           uint64       `json:"device"`
	Inode            uint64       `json:"inode"`
	Size             int64        `json:"size"`
	ModifiedAt       string       `json:"modified_at"`
	ChangedAt        string       `json:"changed_at"`
	RecordCount      int64        `json:"record_count"`
	Records          []fileRecord `json:"records"`
}

type fileRecord struct {
	RecordNumber int64  `json:"record_number"`
	RecordOffset int64  `json:"record_offset"`
	EncodedBytes int64  `json:"encoded_bytes"`
	BodySHA256   string `json:"body_sha256"`
	Source       string `json:"source"`
	ID           string `json:"id"`
	Type         string `json:"type"`
}

type sourceManifest struct {
	SchemaVersion     string         `json:"schema_version"`
	CapturedAt        string         `json:"captured_at"`
	GatewayID         string         `json:"gateway_id"`
	Workspace         string         `json:"workspace"`
	SandboxID         string         `json:"sandbox_id"`
	AcquisitionKind   string         `json:"acquisition_kind"`
	SourceInstance    string         `json:"source_instance"`
	SourceDirectory   string         `json:"source_directory"`
	FilePattern       string         `json:"file_pattern"`
	LogicalDirectory  string         `json:"logical_directory"`
	ResolvedDirectory string         `json:"resolved_directory"`
	Files             []fileManifest `json:"files"`
	TotalRecords      int64          `json:"total_records"`
}

type metricPoint struct {
	CapturedAt        string  `json:"captured_at"`
	QueueSize         float64 `json:"queue_size"`
	QueueCapacity     float64 `json:"queue_capacity"`
	RetryableFailures float64 `json:"retryable_failures"`
	DeliveredEvents   float64 `json:"delivered_events"`
	MetricsSHA256     string  `json:"metrics_sha256"`
}

type storageRole struct {
	Path           string `json:"path"`
	Device         uint64 `json:"device"`
	Inode          uint64 `json:"inode"`
	FileCount      int64  `json:"file_count"`
	Bytes          int64  `json:"bytes"`
	ManifestSHA256 string `json:"manifest_sha256"`
}

type storageSnapshot struct {
	CapturedAt string                 `json:"captured_at"`
	Roles      map[string]storageRole `json:"roles"`
}

type runtimeObservations struct {
	ExporterInstanceBefore   string `json:"exporter_instance_before"`
	ExporterInstanceAfter    string `json:"exporter_instance_after"`
	DestinationOutageStatus  int    `json:"destination_outage_status"`
	DestinationRestoreStatus int    `json:"destination_restore_status"`
}

type runMetadata struct {
	SchemaVersion string `json:"schema_version"`
	RunID         string `json:"run_id"`
	StartedAt     string `json:"started_at"`
	Candidate     struct {
		Commit          string `json:"commit"`
		WorktreeClean   bool   `json:"worktree_clean"`
		ExporterVersion string `json:"exporter_version"`
		ExporterImage   string `json:"exporter_image"`
		ConfigSHA256    string `json:"config_sha256"`
	} `json:"candidate"`
	Gateway struct {
		Version string `json:"version"`
		Image   string `json:"image"`
	} `json:"gateway"`
	Hooks       map[string]string `json:"hook_sha256"`
	Limitations []string          `json:"limitations"`
}

type lifecycleReport struct {
	SchemaVersion string `json:"schema_version"`
	GeneratedAt   string `json:"generated_at"`
	Result        string `json:"result"`
	Complete      bool   `json:"complete"`
	RunID         string `json:"run_id"`
	StartedAt     string `json:"started_at"`
	FinishedAt    string `json:"finished_at"`
	Candidate     struct {
		Commit          string `json:"commit"`
		WorktreeClean   bool   `json:"worktree_clean"`
		Bound           bool   `json:"bound"`
		ExporterVersion string `json:"exporter_version"`
		ExporterImage   string `json:"exporter_image"`
		ConfigSHA256    string `json:"config_sha256"`
	} `json:"candidate"`
	Gateway struct {
		Version string `json:"version"`
		Image   string `json:"image"`
	} `json:"gateway"`
	SourceManifests struct {
		Before       sourceManifest `json:"before"`
		After        sourceManifest `json:"after"`
		BeforeSHA256 string         `json:"before_sha256"`
		AfterSHA256  string         `json:"after_sha256"`
	} `json:"source_manifests"`
	Lifecycle struct {
		NaturalDailyRotation struct {
			Observed              bool     `json:"observed"`
			NewFiles              []string `json:"new_files"`
			PreviousFile          string   `json:"previous_file"`
			PreviousFileRetained  bool     `json:"previous_file_retained"`
			PreviousFilesRetained []string `json:"previous_files_retained"`
			PreviousFilesMissing  []string `json:"previous_files_missing"`
			RetentionExpiredFiles []string `json:"retention_expired_files"`
			PreviousInodesStable  bool     `json:"previous_inodes_stable"`
			NewInodesDistinct     bool     `json:"new_inodes_distinct"`
			NewFilesHaveRecords   bool     `json:"new_files_have_records"`
		} `json:"natural_daily_rotation"`
		ExporterRestart struct {
			InstanceBefore             string `json:"instance_before"`
			InstanceAfter              string `json:"instance_after"`
			Observed                   bool   `json:"observed"`
			CheckpointStoragePreserved bool   `json:"checkpoint_storage_preserved"`
			QueueStoragePreserved      bool   `json:"queue_storage_preserved"`
			RecoveryStoragePreserved   bool   `json:"recovery_storage_preserved"`
		} `json:"exporter_restart"`
		DestinationOutage struct {
			OutageStatus          int     `json:"outage_status"`
			RestoreStatus         int     `json:"restore_status"`
			HTTP503Exercised      bool    `json:"http_503_exercised"`
			QueueBaseline         float64 `json:"queue_baseline"`
			QueueDuringOutage     float64 `json:"queue_during_outage"`
			QueueAfterRestart     float64 `json:"queue_after_restart"`
			QueueFinal            float64 `json:"queue_final"`
			RetryableFailureDelta float64 `json:"retryable_failure_delta"`
			Drained               bool    `json:"drained"`
		} `json:"destination_outage"`
	} `json:"lifecycle"`
	Metrics map[string]metricPoint `json:"metrics"`
	Storage struct {
		BeforeRestart storageSnapshot `json:"before_restart"`
		AfterRestart  storageSnapshot `json:"after_restart"`
	} `json:"storage"`
	Reconciliation struct {
		RetainedSourceRecords  int64    `json:"retained_source_records"`
		UniqueDeliveredRecords int64    `json:"unique_delivered_records"`
		TotalDeliveries        int64    `json:"total_deliveries"`
		DuplicateDeliveries    int64    `json:"duplicate_deliveries"`
		MissingDurableRecords  int64    `json:"missing_durable_records"`
		IdentityCollisions     int64    `json:"identity_collisions"`
		UnexpectedDeliveries   int64    `json:"unexpected_deliveries"`
		ReplayKeysStable       bool     `json:"replay_keys_stable"`
		MissingKeys            []string `json:"missing_keys"`
	} `json:"reconciliation"`
	HookSHA256  map[string]string `json:"hook_sha256"`
	Limitations []string          `json:"limitations"`
}

type deliveredEvent struct {
	Source string `json:"source"`
	ID     string `json:"id"`
	Type   string `json:"type"`
}

func main() {
	if len(os.Args) < 2 {
		fatalf("usage: %s config-source-instance|snapshot|reconcile [flags]", os.Args[0])
	}
	var err error
	switch os.Args[1] {
	case "config-source-instance":
		err = runConfigSourceInstance(os.Args[2:])
	case "snapshot":
		err = runSnapshot(os.Args[2:])
	case "reconcile":
		err = runReconcile(os.Args[2:])
	default:
		err = fmt.Errorf("unknown command %q", os.Args[1])
	}
	if err != nil {
		fatalf("%v", err)
	}
}

func runConfigSourceInstance(arguments []string) error {
	flags := flag.NewFlagSet("config-source-instance", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	configPath := flags.String("config", "", "Collector configuration path")
	if err := flags.Parse(arguments); err != nil {
		return err
	}
	if *configPath == "" {
		return errors.New("--config is required")
	}
	instance, err := sourceInstanceFromConfig(*configPath)
	if err != nil {
		return err
	}
	fmt.Println(instance)
	return nil
}

func sourceInstanceFromConfig(path string) (string, error) {
	encoded, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	var document struct {
		Processors map[string]map[string]any `yaml:"processors"`
	}
	if err := yaml.Unmarshal(encoded, &document); err != nil {
		return "", fmt.Errorf("parse Collector configuration: %w", err)
	}
	instances := map[string]struct{}{}
	for name, settings := range document.Processors {
		if name != "openshell" && !strings.HasPrefix(name, "openshell/") {
			continue
		}
		value, ok := settings["source_instance"].(string)
		if !ok || strings.TrimSpace(value) == "" {
			return "", fmt.Errorf("processor %q has no literal source_instance", name)
		}
		instances[value] = struct{}{}
	}
	if len(instances) != 1 {
		return "", fmt.Errorf("qualification requires exactly one openshell processor source_instance, found %d", len(instances))
	}
	for instance := range instances {
		return instance, nil
	}
	return "", errors.New("openshell processor source_instance is unavailable")
}

func fatalf(format string, values ...any) {
	fmt.Fprintf(os.Stderr, "OCSF file lifecycle qualification: "+format+"\n", values...)
	os.Exit(1)
}

func runSnapshot(arguments []string) error {
	flags := flag.NewFlagSet("snapshot", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	sourceDirectory := flags.String("source-dir", "", "mounted source directory")
	pattern := flags.String("pattern", "openshell-ocsf.*.log", "allow-listed filename pattern")
	logicalDirectory := flags.String("logical-dir", "/var/log", "exporter-visible logical directory")
	resolvedDirectory := flags.String("resolved-dir", "/var/log", "exporter-visible resolved directory")
	gatewayID := flags.String("gateway-id", "", "stable gateway ID")
	workspace := flags.String("workspace", "", "OpenShell workspace")
	sandboxID := flags.String("sandbox-id", "", "OpenShell sandbox ID")
	sourceInstance := flags.String("source-instance", "", "configured source instance")
	output := flags.String("output", "", "new manifest path")
	if err := flags.Parse(arguments); err != nil {
		return err
	}
	for name, value := range map[string]string{
		"source-dir":      *sourceDirectory,
		"logical-dir":     *logicalDirectory,
		"resolved-dir":    *resolvedDirectory,
		"gateway-id":      *gatewayID,
		"workspace":       *workspace,
		"sandbox-id":      *sandboxID,
		"source-instance": *sourceInstance,
		"output":          *output,
	} {
		if value == "" {
			return fmt.Errorf("--%s is required", name)
		}
	}
	if *pattern != "openshell-ocsf.*.log" {
		return fmt.Errorf("--pattern must be the supported openshell-ocsf.*.log profile")
	}
	manifest, err := captureSourceManifest(
		*sourceDirectory,
		*pattern,
		*logicalDirectory,
		*resolvedDirectory,
		*gatewayID,
		*workspace,
		*sandboxID,
		*sourceInstance,
	)
	if err != nil {
		return err
	}
	return writeNewRestrictedJSON(*output, manifest)
}

func captureSourceManifest(sourceDirectory, pattern, logicalDirectory, resolvedDirectory, gatewayID, workspace, sandboxID, sourceInstance string) (sourceManifest, error) {
	root, err := filepath.Abs(sourceDirectory)
	if err != nil {
		return sourceManifest{}, err
	}
	root, err = filepath.EvalSymlinks(root)
	if err != nil {
		return sourceManifest{}, fmt.Errorf("resolve source directory: %w", err)
	}
	info, err := os.Stat(root)
	if err != nil || !info.IsDir() {
		return sourceManifest{}, fmt.Errorf("source directory is not readable: %s", root)
	}
	matches, err := filepath.Glob(filepath.Join(root, pattern))
	if err != nil {
		return sourceManifest{}, err
	}
	sort.Strings(matches)
	if len(matches) == 0 {
		return sourceManifest{}, fmt.Errorf("no supported OCSF files matched %s", filepath.Join(root, pattern))
	}
	manifest := sourceManifest{
		SchemaVersion:     schemaVersion,
		CapturedAt:        time.Now().UTC().Format(time.RFC3339Nano),
		GatewayID:         gatewayID,
		Workspace:         workspace,
		SandboxID:         sandboxID,
		AcquisitionKind:   "ocsf.file",
		SourceInstance:    sourceInstance,
		SourceDirectory:   root,
		FilePattern:       pattern,
		LogicalDirectory:  filepath.Clean(logicalDirectory),
		ResolvedDirectory: filepath.Clean(resolvedDirectory),
		Files:             make([]fileManifest, 0, len(matches)),
	}
	for _, match := range matches {
		resolved, err := filepath.EvalSymlinks(match)
		if err != nil {
			return sourceManifest{}, fmt.Errorf("resolve %s: %w", match, err)
		}
		if filepath.Dir(resolved) != root {
			return sourceManifest{}, fmt.Errorf("source file escapes the allow-listed directory: %s", match)
		}
		file, err := captureFile(
			resolved,
			filepath.Join(manifest.LogicalDirectory, filepath.Base(match)),
			filepath.Join(manifest.ResolvedDirectory, filepath.Base(resolved)),
			gatewayID,
			workspace,
			sandboxID,
			sourceInstance,
		)
		if err != nil {
			return sourceManifest{}, err
		}
		manifest.TotalRecords += file.RecordCount
		manifest.Files = append(manifest.Files, file)
	}
	if manifest.TotalRecords == 0 {
		return sourceManifest{}, errors.New("supported OCSF files contained no records")
	}
	return manifest, nil
}

func captureFile(localPath, logicalPath, resolvedPath, gatewayID, workspace, sandboxID, sourceInstance string) (fileManifest, error) {
	opened, err := os.Open(localPath)
	if err != nil {
		return fileManifest{}, err
	}
	defer func() { _ = opened.Close() }()
	info, err := opened.Stat()
	if err != nil {
		return fileManifest{}, err
	}
	device, inode, changedAt, err := statIdentity(info)
	if err != nil {
		return fileManifest{}, fmt.Errorf("stat %s: %w", localPath, err)
	}
	result := fileManifest{
		Filename:         filepath.Base(localPath),
		LocalPath:        localPath,
		FilePath:         filepath.ToSlash(logicalPath),
		FilePathResolved: filepath.ToSlash(resolvedPath),
		Device:           device,
		Inode:            inode,
		Size:             info.Size(),
		ModifiedAt:       info.ModTime().UTC().Format(time.RFC3339Nano),
		ChangedAt:        changedAt.UTC().Format(time.RFC3339Nano),
		Records:          []fileRecord{},
	}
	reader := bufio.NewReaderSize(opened, 64*1024)
	var offset int64
	for {
		line, readErr := reader.ReadBytes('\n')
		if len(line) > maximumLine {
			return fileManifest{}, fmt.Errorf("%s record %d exceeds the 8 MiB input cap", localPath, result.RecordCount+1)
		}
		if len(line) > 0 {
			body := bytes.TrimSuffix(line, []byte{'\n'})
			body = bytes.TrimSuffix(body, []byte{'\r'})
			record, err := buildRecord(body, offset, result.RecordCount+1, int64(len(line)), logicalPath, resolvedPath, gatewayID, workspace, sandboxID, sourceInstance)
			if err != nil {
				return fileManifest{}, fmt.Errorf("%s record %d: %w", localPath, result.RecordCount+1, err)
			}
			result.Records = append(result.Records, record)
			result.RecordCount++
			offset += int64(len(line))
		}
		if readErr != nil {
			if errors.Is(readErr, io.EOF) {
				break
			}
			return fileManifest{}, readErr
		}
	}
	afterInfo, err := opened.Stat()
	if err != nil {
		return fileManifest{}, err
	}
	afterDevice, afterInode, afterChangedAt, err := statIdentity(afterInfo)
	if err != nil {
		return fileManifest{}, err
	}
	if offset != afterInfo.Size() || device != afterDevice || inode != afterInode || !info.ModTime().Equal(afterInfo.ModTime()) || !changedAt.Equal(afterChangedAt) {
		return fileManifest{}, fmt.Errorf("%s changed while it was read; retry the snapshot", localPath)
	}
	return result, nil
}

func buildRecord(body []byte, offset, number, encodedBytes int64, logicalPath, resolvedPath, gatewayID, workspace, sandboxID, sourceInstance string) (fileRecord, error) {
	var original any
	if err := json.Unmarshal(body, &original); err != nil {
		original = string(body)
	}
	canonical, err := json.Marshal(original)
	if err != nil {
		return fileRecord{}, err
	}
	bodySum := sha256.Sum256(canonical)
	bodyHash := hex.EncodeToString(bodySum[:])
	recordSandboxID := sandboxIDFromOriginal(original, sandboxID)
	source := fmt.Sprintf(
		"openshell://%s/workspaces/%s/sandboxes/%s/sources/%s",
		url.PathEscape(gatewayID), url.PathEscape(workspace), url.PathEscape(recordSandboxID), url.PathEscape("ocsf.file"),
	)
	identity := map[string]any{
		"gateway_id":             gatewayID,
		"workspace":              workspace,
		"kind":                   "ocsf.file",
		"body_hash":              bodyHash,
		"source_instance":        sourceInstance,
		"log.file.path":          filepath.ToSlash(logicalPath),
		"log.file.path_resolved": filepath.ToSlash(resolvedPath),
		"log.file.record_offset": offset,
		"log.file.record_number": number,
	}
	encodedIdentity, err := json.Marshal(identity)
	if err != nil {
		return fileRecord{}, err
	}
	idSum := sha256.Sum256(encodedIdentity)
	eventType := "com.nvidia.openshell.ocsf.unknown.v1"
	if object, ok := original.(map[string]any); ok {
		if classUID, integerErr := positiveInteger(object["class_uid"]); integerErr == nil {
			eventType = "com.nvidia.openshell.ocsf." + strconv.FormatInt(classUID, 10) + ".v1"
		}
	}
	return fileRecord{
		RecordNumber: number,
		RecordOffset: offset,
		EncodedBytes: encodedBytes,
		BodySHA256:   "sha256:" + bodyHash,
		Source:       source,
		ID:           "sha256:" + hex.EncodeToString(idSum[:]),
		Type:         eventType,
	}, nil
}

func sandboxIDFromOriginal(original any, fallback string) string {
	object, ok := original.(map[string]any)
	if !ok {
		return fallback
	}
	for _, path := range []string{"sandbox.id", "sandbox_id", "unmapped.sandbox_id"} {
		current := any(object)
		found := true
		for _, segment := range strings.Split(path, ".") {
			values, objectOK := current.(map[string]any)
			if !objectOK {
				found = false
				break
			}
			current, found = values[segment]
			if !found {
				break
			}
		}
		if found {
			value := fmt.Sprint(current)
			if value != "" && value != "<nil>" {
				return value
			}
		}
	}
	if fallback != "" {
		return fallback
	}
	return "unknown"
}

func positiveInteger(value any) (int64, error) {
	number, ok := value.(float64)
	if !ok || number <= 0 || number != float64(int64(number)) {
		return 0, errors.New("must be a positive integer")
	}
	return int64(number), nil
}

func statIdentity(info os.FileInfo) (uint64, uint64, time.Time, error) {
	stat, ok := info.Sys().(*syscall.Stat_t)
	if !ok {
		return 0, 0, time.Time{}, errors.New("linux stat identity is unavailable")
	}
	return uint64(stat.Dev), stat.Ino, time.Unix(stat.Ctim.Sec, stat.Ctim.Nsec), nil
}

func runReconcile(arguments []string) error {
	flags := flag.NewFlagSet("reconcile", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	metadataPath := flags.String("metadata", "", "run metadata JSON")
	beforePath := flags.String("before", "", "before source manifest")
	afterPath := flags.String("after", "", "after source manifest")
	deliveriesPath := flags.String("deliveries", "", "fresh destination CloudEvents ledger")
	metricsPath := flags.String("metrics", "", "metrics observations JSON")
	storageBeforePath := flags.String("storage-before-restart", "", "storage snapshot before restart")
	storageAfterPath := flags.String("storage-after-restart", "", "storage snapshot after restart")
	runtimePath := flags.String("runtime", "", "runtime observations JSON")
	output := flags.String("output", "", "new report path")
	requireComplete := flags.Bool("require-complete", false, "return failure unless every gate passed")
	if err := flags.Parse(arguments); err != nil {
		return err
	}
	for name, value := range map[string]string{
		"metadata":               *metadataPath,
		"before":                 *beforePath,
		"after":                  *afterPath,
		"deliveries":             *deliveriesPath,
		"metrics":                *metricsPath,
		"storage-before-restart": *storageBeforePath,
		"storage-after-restart":  *storageAfterPath,
		"runtime":                *runtimePath,
		"output":                 *output,
	} {
		if value == "" {
			return fmt.Errorf("--%s is required", name)
		}
	}
	report, err := reconcileRun(*metadataPath, *beforePath, *afterPath, *deliveriesPath, *metricsPath, *storageBeforePath, *storageAfterPath, *runtimePath)
	if err != nil {
		return err
	}
	if err := writeNewRestrictedJSON(*output, report); err != nil {
		return err
	}
	if *requireComplete && !report.Complete {
		return fmt.Errorf("qualification is incomplete or failed; report preserved at %s", *output)
	}
	return nil
}

func reconcileRun(metadataPath, beforePath, afterPath, deliveriesPath, metricsPath, storageBeforePath, storageAfterPath, runtimePath string) (lifecycleReport, error) {
	var metadata runMetadata
	var before, after sourceManifest
	var metrics map[string]metricPoint
	var storageBefore, storageAfter storageSnapshot
	var runtime runtimeObservations
	for path, target := range map[string]any{
		metadataPath:      &metadata,
		beforePath:        &before,
		afterPath:         &after,
		metricsPath:       &metrics,
		storageBeforePath: &storageBefore,
		storageAfterPath:  &storageAfter,
		runtimePath:       &runtime,
	} {
		if err := readStrictJSON(path, target); err != nil {
			return lifecycleReport{}, err
		}
	}
	if metadata.SchemaVersion != schemaVersion || before.SchemaVersion != schemaVersion || after.SchemaVersion != schemaVersion {
		return lifecycleReport{}, errors.New("unsupported qualification input schema")
	}
	if before.GatewayID != after.GatewayID || before.Workspace != after.Workspace || before.SandboxID != after.SandboxID || before.SourceInstance != after.SourceInstance {
		return lifecycleReport{}, errors.New("before and after source scopes differ")
	}
	for _, name := range []string{"baseline", "outage", "after_restart", "drained"} {
		if _, ok := metrics[name]; !ok {
			return lifecycleReport{}, fmt.Errorf("metrics point %q is missing", name)
		}
	}
	beforeDigest, err := digestFile(beforePath)
	if err != nil {
		return lifecycleReport{}, err
	}
	afterDigest, err := digestFile(afterPath)
	if err != nil {
		return lifecycleReport{}, err
	}

	report := lifecycleReport{
		SchemaVersion: schemaVersion,
		GeneratedAt:   time.Now().UTC().Format(time.RFC3339Nano),
		Result:        "failed",
		RunID:         metadata.RunID,
		StartedAt:     metadata.StartedAt,
		FinishedAt:    time.Now().UTC().Format(time.RFC3339Nano),
		Metrics:       metrics,
		HookSHA256:    metadata.Hooks,
		Limitations:   append([]string(nil), metadata.Limitations...),
	}
	report.Candidate.Commit = metadata.Candidate.Commit
	report.Candidate.WorktreeClean = metadata.Candidate.WorktreeClean
	report.Candidate.Bound = metadata.Candidate.WorktreeClean && isCommit(metadata.Candidate.Commit) && isDigestReference(metadata.Candidate.ExporterImage)
	report.Candidate.ExporterVersion = metadata.Candidate.ExporterVersion
	report.Candidate.ExporterImage = metadata.Candidate.ExporterImage
	report.Candidate.ConfigSHA256 = metadata.Candidate.ConfigSHA256
	report.Gateway = metadata.Gateway
	report.SourceManifests.Before = before
	report.SourceManifests.After = after
	report.SourceManifests.BeforeSHA256 = beforeDigest
	report.SourceManifests.AfterSHA256 = afterDigest
	report.Storage.BeforeRestart = storageBefore
	report.Storage.AfterRestart = storageAfter

	rotation := &report.Lifecycle.NaturalDailyRotation
	rotation.NewFiles = []string{}
	rotation.PreviousFilesRetained = []string{}
	rotation.PreviousFilesMissing = []string{}
	rotation.RetentionExpiredFiles = []string{}
	beforeFiles := make(map[string]fileManifest, len(before.Files))
	afterFiles := make(map[string]fileManifest, len(after.Files))
	beforeInodes := map[string]struct{}{}
	for _, file := range before.Files {
		beforeFiles[file.Filename] = file
		beforeInodes[fmt.Sprintf("%d:%d", file.Device, file.Inode)] = struct{}{}
		if file.Filename > rotation.PreviousFile {
			rotation.PreviousFile = file.Filename
		}
	}
	rotation.PreviousInodesStable = false
	rotation.NewInodesDistinct = true
	rotation.NewFilesHaveRecords = true
	for _, file := range after.Files {
		afterFiles[file.Filename] = file
		if previous, ok := beforeFiles[file.Filename]; ok {
			rotation.PreviousFilesRetained = append(rotation.PreviousFilesRetained, file.Filename)
			if file.Filename == rotation.PreviousFile {
				rotation.PreviousFileRetained = true
				rotation.PreviousInodesStable = previous.Device == file.Device && previous.Inode == file.Inode && file.Size >= previous.Size
			}
		} else {
			rotation.NewFiles = append(rotation.NewFiles, file.Filename)
			if file.RecordCount == 0 {
				rotation.NewFilesHaveRecords = false
			}
			if _, exists := beforeInodes[fmt.Sprintf("%d:%d", file.Device, file.Inode)]; exists {
				rotation.NewInodesDistinct = false
			}
		}
	}
	for name := range beforeFiles {
		if _, ok := afterFiles[name]; !ok {
			if name == rotation.PreviousFile {
				rotation.PreviousFilesMissing = append(rotation.PreviousFilesMissing, name)
			} else {
				rotation.RetentionExpiredFiles = append(rotation.RetentionExpiredFiles, name)
			}
		}
	}
	sort.Strings(rotation.NewFiles)
	sort.Strings(rotation.PreviousFilesRetained)
	sort.Strings(rotation.PreviousFilesMissing)
	sort.Strings(rotation.RetentionExpiredFiles)
	rotation.Observed = len(rotation.NewFiles) > 0 && rotation.PreviousFileRetained && len(rotation.PreviousFilesMissing) == 0 && rotation.PreviousInodesStable && rotation.NewInodesDistinct && rotation.NewFilesHaveRecords

	restart := &report.Lifecycle.ExporterRestart
	restart.InstanceBefore = runtime.ExporterInstanceBefore
	restart.InstanceAfter = runtime.ExporterInstanceAfter
	restart.CheckpointStoragePreserved = storageRolePreserved(storageBefore, storageAfter, "checkpoints")
	restart.QueueStoragePreserved = storageRolePreserved(storageBefore, storageAfter, "queue")
	restart.RecoveryStoragePreserved = storageRolePreserved(storageBefore, storageAfter, "recovery")
	restart.Observed = restart.InstanceBefore != "" && restart.InstanceAfter != "" && restart.InstanceBefore != restart.InstanceAfter && restart.CheckpointStoragePreserved && restart.QueueStoragePreserved && restart.RecoveryStoragePreserved

	outage := &report.Lifecycle.DestinationOutage
	outage.OutageStatus = runtime.DestinationOutageStatus
	outage.RestoreStatus = runtime.DestinationRestoreStatus
	outage.QueueBaseline = metrics["baseline"].QueueSize
	outage.QueueDuringOutage = metrics["outage"].QueueSize
	outage.QueueAfterRestart = metrics["after_restart"].QueueSize
	outage.QueueFinal = metrics["drained"].QueueSize
	outage.RetryableFailureDelta = metrics["outage"].RetryableFailures - metrics["baseline"].RetryableFailures
	outage.HTTP503Exercised = outage.OutageStatus == 503 && outage.QueueDuringOutage > outage.QueueBaseline && outage.RetryableFailureDelta > 0
	outage.Drained = outage.RestoreStatus >= 200 && outage.RestoreStatus < 300 && outage.QueueFinal == 0 && metrics["drained"].DeliveredEvents > metrics["after_restart"].DeliveredEvents

	expected, collisions := retainedSourceRecords(after)
	deliveries, err := readDeliveries(deliveriesPath)
	if err != nil {
		return lifecycleReport{}, err
	}
	counts := map[string]int64{}
	var unexpected int64
	for _, delivery := range deliveries {
		key := delivery.Source + "\x00" + delivery.ID
		if _, ok := expected[key]; !ok {
			unexpected++
			continue
		}
		counts[key]++
	}
	missing := make([]string, 0)
	var total, duplicates int64
	for key := range expected {
		count := counts[key]
		total += count
		if count == 0 {
			if len(missing) < maximumMissing {
				missing = append(missing, strings.ReplaceAll(key, "\x00", " "))
			}
		} else if count > 1 {
			duplicates += count - 1
		}
	}
	sort.Strings(missing)
	reconciliation := &report.Reconciliation
	reconciliation.RetainedSourceRecords = int64(len(expected))
	reconciliation.UniqueDeliveredRecords = int64(len(expected)) - int64(len(missing))
	reconciliation.TotalDeliveries = total
	reconciliation.DuplicateDeliveries = duplicates
	reconciliation.MissingDurableRecords = int64(len(expected)) - reconciliation.UniqueDeliveredRecords
	reconciliation.IdentityCollisions = collisions
	reconciliation.UnexpectedDeliveries = unexpected
	reconciliation.ReplayKeysStable = duplicates > 0
	reconciliation.MissingKeys = missing

	report.Complete = report.Candidate.Bound && isDigestReference(report.Gateway.Image) && rotation.Observed && restart.Observed && outage.HTTP503Exercised && outage.Drained && reconciliation.MissingDurableRecords == 0 && reconciliation.IdentityCollisions == 0 && reconciliation.ReplayKeysStable
	if report.Complete {
		report.Result = "passed"
	} else if reconciliation.MissingDurableRecords == 0 && reconciliation.IdentityCollisions == 0 {
		report.Result = "incomplete"
	}
	return report, nil
}

func retainedSourceRecords(after sourceManifest) (map[string]fileRecord, int64) {
	records := map[string]fileRecord{}
	fingerprints := map[string]string{}
	var collisions int64
	for _, file := range after.Files {
		for _, record := range file.Records {
			key := record.Source + "\x00" + record.ID
			fingerprint := fmt.Sprintf("%s\x00%d\x00%d\x00%s", file.FilePathResolved, record.RecordNumber, record.RecordOffset, record.BodySHA256)
			if previous, ok := fingerprints[key]; ok && previous != fingerprint {
				collisions++
			}
			fingerprints[key] = fingerprint
			records[key] = record
		}
	}
	return records, collisions
}

func readDeliveries(path string) ([]deliveredEvent, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer func() { _ = file.Close() }()
	decoder := json.NewDecoder(file)
	result := []deliveredEvent{}
	for {
		var value json.RawMessage
		if err := decoder.Decode(&value); err != nil {
			if errors.Is(err, io.EOF) {
				break
			}
			return nil, fmt.Errorf("decode destination ledger: %w", err)
		}
		trimmed := bytes.TrimSpace(value)
		if len(trimmed) == 0 {
			continue
		}
		if trimmed[0] == '[' {
			var batch []deliveredEvent
			if err := json.Unmarshal(trimmed, &batch); err != nil {
				return nil, err
			}
			for _, event := range batch {
				if err := validateDelivery(event); err != nil {
					return nil, err
				}
			}
			result = append(result, batch...)
		} else {
			var event deliveredEvent
			if err := json.Unmarshal(trimmed, &event); err != nil {
				return nil, err
			}
			if err := validateDelivery(event); err != nil {
				return nil, err
			}
			result = append(result, event)
		}
	}
	return result, nil
}

func validateDelivery(event deliveredEvent) error {
	if !strings.HasPrefix(event.Source, "openshell://") || !strings.HasPrefix(event.ID, "sha256:") || len(event.ID) != 71 || event.Type == "" {
		return errors.New("destination ledger contains an invalid CloudEvent identity")
	}
	return nil
}

func storageRolePreserved(before, after storageSnapshot, name string) bool {
	left, leftOK := before.Roles[name]
	right, rightOK := after.Roles[name]
	return leftOK && rightOK && left.Path == right.Path && left.Device == right.Device && left.Inode == right.Inode && left.FileCount > 0 && right.FileCount > 0
}

func isCommit(value string) bool {
	if len(value) != 40 {
		return false
	}
	_, err := hex.DecodeString(value)
	return err == nil
}

func isDigestReference(value string) bool {
	parts := strings.Split(value, "@sha256:")
	if len(parts) != 2 || parts[0] == "" || len(parts[1]) != 64 {
		return false
	}
	_, err := hex.DecodeString(parts[1])
	return err == nil
}

func readStrictJSON(path string, target any) error {
	encoded, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	decoder := json.NewDecoder(bytes.NewReader(encoded))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(target); err != nil {
		return fmt.Errorf("decode %s: %w", path, err)
	}
	var extra any
	if err := decoder.Decode(&extra); !errors.Is(err, io.EOF) {
		return fmt.Errorf("%s contains multiple JSON values", path)
	}
	return nil
}

func writeNewRestrictedJSON(path string, value any) error {
	encoded, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return err
	}
	encoded = append(encoded, '\n')
	file, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if err != nil {
		return err
	}
	if _, err := file.Write(encoded); err != nil {
		_ = file.Close()
		return err
	}
	return file.Close()
}

func digestFile(path string) (string, error) {
	file, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer func() { _ = file.Close() }()
	hash := sha256.New()
	if _, err := io.Copy(hash, file); err != nil {
		return "", err
	}
	return "sha256:" + hex.EncodeToString(hash.Sum(nil)), nil
}
