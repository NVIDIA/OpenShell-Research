// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package integration

import (
	"bufio"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/santhosh-tekuri/jsonschema/v6"
)

type capacityFixture struct {
	server *httptest.Server

	mu                sync.Mutex
	idHashes          []uint64
	unique            int
	deliveries        int
	duplicateDelivery int
	invalid           int
	status            int
	rejectedRequests  int
	latency           capacityLatencyHistogram
}

func newCapacityFixture(expected int) *capacityFixture {
	fixture := &capacityFixture{idHashes: make([]uint64, expected), status: http.StatusAccepted}
	fixture.server = httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		fixture.mu.Lock()
		status := fixture.status
		if status < 200 || status >= 300 {
			fixture.rejectedRequests++
			fixture.mu.Unlock()
			writer.WriteHeader(status)
			return
		}
		fixture.mu.Unlock()
		var events []struct {
			ID   string    `json:"id"`
			Time time.Time `json:"time"`
			Data struct {
				Original struct {
					Metadata struct {
						OriginalEventUID string `json:"original_event_uid"`
					} `json:"metadata"`
				} `json:"original"`
			} `json:"data"`
		}
		if err := json.NewDecoder(request.Body).Decode(&events); err != nil {
			writer.WriteHeader(http.StatusBadRequest)
			return
		}

		fixture.mu.Lock()
		defer fixture.mu.Unlock()
		receivedAt := time.Now()
		for _, event := range events {
			fixture.deliveries++
			sequenceText := strings.TrimPrefix(event.Data.Original.Metadata.OriginalEventUID, "capacity-")
			sequence, err := strconv.Atoi(sequenceText)
			if err != nil || sequence < 0 || sequence >= len(fixture.idHashes) ||
				!strings.HasPrefix(event.ID, "sha256:") || len(event.ID) != 71 {
				fixture.invalid++
				continue
			}
			sum := sha256.Sum256([]byte(event.ID))
			idHash := binary.BigEndian.Uint64(sum[:8])
			if fixture.idHashes[sequence] == 0 {
				fixture.idHashes[sequence] = idHash
				fixture.unique++
				if !event.Time.IsZero() {
					fixture.latency.record(receivedAt.Sub(event.Time))
				}
				continue
			}
			if fixture.idHashes[sequence] != idHash {
				fixture.invalid++
				continue
			}
			fixture.duplicateDelivery++
		}
		writer.WriteHeader(http.StatusAccepted)
	}))
	return fixture
}

func (f *capacityFixture) close() {
	f.server.Close()
}

func (f *capacityFixture) setStatus(status int) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.status = status
}

func (f *capacityFixture) rejectedRequestCount() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.rejectedRequests
}

func (f *capacityFixture) snapshot() (unique, deliveries, duplicates, invalid int) {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.unique, f.deliveries, f.duplicateDelivery, f.invalid
}

func (f *capacityFixture) missing(limit int) []int {
	f.mu.Lock()
	defer f.mu.Unlock()
	missing := make([]int, 0)
	for sequence, idHash := range f.idHashes {
		if idHash == 0 {
			missing = append(missing, sequence)
			if len(missing) == limit {
				break
			}
		}
	}
	return missing
}

func (f *capacityFixture) latencyReport() capacityLatencyReport {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.latency.report()
}

type capacityReport struct {
	SchemaVersion        string                  `json:"schema_version"`
	Result               string                  `json:"result"`
	GateEligible         bool                    `json:"gate_eligible"`
	QualificationKind    string                  `json:"qualification_kind"`
	StartedAt            string                  `json:"started_at"`
	FinishedAt           string                  `json:"finished_at"`
	RequestedRate        int                     `json:"requested_events_per_second"`
	RequestedDuration    string                  `json:"requested_duration"`
	GeneratedEvents      int                     `json:"generated_events"`
	GeneratedBytes       int64                   `json:"generated_bytes"`
	GenerationRate       float64                 `json:"generation_events_per_second"`
	UniqueDelivered      int                     `json:"unique_delivered_events"`
	TotalDeliveries      int                     `json:"total_deliveries"`
	DuplicateDeliveries  int                     `json:"duplicate_deliveries"`
	InvalidDeliveries    int                     `json:"invalid_deliveries"`
	DestinationOutage    bool                    `json:"destination_outage"`
	RejectedRequests     int                     `json:"rejected_requests"`
	OutageDuration       string                  `json:"outage_duration,omitempty"`
	MissingSequences     []int                   `json:"first_missing_sequences,omitempty"`
	DrainDuration        string                  `json:"drain_duration"`
	ExporterVersion      string                  `json:"exporter_version,omitempty"`
	ExporterBinarySHA256 string                  `json:"exporter_binary_sha256,omitempty"`
	ExporterImageDigest  string                  `json:"exporter_image_digest,omitempty"`
	CommitSHA            string                  `json:"commit_sha,omitempty"`
	ReferenceHardware    string                  `json:"reference_hardware,omitempty"`
	Resources            capacityResourceReport  `json:"resources"`
	ResourceBudgets      capacityResourceBudgets `json:"resource_budgets"`
	DeliveryLatency      capacityLatencyReport   `json:"delivery_latency"`
	Notes                string                  `json:"notes"`
}

type capacityResourceBudgets struct {
	Enforced                   bool     `json:"enforced"`
	MaximumRSSBytes            int64    `json:"maximum_rss_bytes"`
	MaximumProcessWrittenBytes uint64   `json:"maximum_process_written_bytes"`
	MaximumCheckpointBytes     int64    `json:"maximum_checkpoint_bytes"`
	MaximumQueueBytes          int64    `json:"maximum_queue_bytes"`
	MaximumRecoveryBytes       int64    `json:"maximum_recovery_bytes"`
	Violations                 []string `json:"violations"`
}

func TestSustainedCapacityQualification(t *testing.T) {
	if os.Getenv("RUN_CAPACITY_QUALIFICATION") != "true" {
		t.Skip("set RUN_CAPACITY_QUALIFICATION=true to execute the sustained capacity gate")
	}
	rate := positiveEnvInt(t, "CAPACITY_EVENTS_PER_SECOND", 1000)
	duration := positiveEnvDuration(t, "CAPACITY_DURATION", time.Hour)
	drainTimeout := positiveEnvDuration(t, "CAPACITY_DRAIN_TIMEOUT", 30*time.Minute)
	destinationOutage := optionalBoolEnv(t, "CAPACITY_DESTINATION_OUTAGE")
	sampleInterval := positiveEnvDuration(t, "CAPACITY_SAMPLE_INTERVAL", 10*time.Second)
	resourceBudgets, err := capacityResourceBudgetsFromEnvironment()
	if err != nil {
		t.Fatal(err)
	}
	expected := int(float64(rate) * duration.Seconds())
	if expected <= 0 {
		t.Fatal("capacity profile must generate at least one event")
	}

	fixture := newCapacityFixture(expected)
	defer fixture.close()
	if destinationOutage {
		fixture.setStatus(http.StatusServiceUnavailable)
	}
	harness := startExporterHarnessWithOptions(t, fixture.server.URL, exporterHarnessOptions{
		queueSize:        20000,
		sendBatchSize:    500,
		batchTimeout:     200 * time.Millisecond,
		retryMaxInterval: 5 * time.Second,
	})
	binaryDigest, err := capacityBinaryDigest(harness.binary)
	if err != nil {
		t.Fatalf("hash exporter binary: %v", err)
	}
	sampler, err := startCapacitySampler(
		harness.command.Process.Pid,
		sampleInterval,
		harness.checkpointPath,
		harness.queuePath,
		harness.recoveryPath,
	)
	if err != nil {
		t.Fatalf("start capacity resource sampler: %v", err)
	}
	samplerStopped := false
	defer func() {
		if !samplerStopped {
			sampler.stop()
		}
	}()

	report := capacityReport{
		SchemaVersion:        "1.2",
		Result:               "failed",
		QualificationKind:    capacityQualificationKind(destinationOutage),
		StartedAt:            time.Now().UTC().Format(time.RFC3339Nano),
		RequestedRate:        rate,
		RequestedDuration:    duration.String(),
		DestinationOutage:    destinationOutage,
		ExporterVersion:      os.Getenv("EXPORTER_VERSION"),
		ExporterBinarySHA256: binaryDigest,
		ExporterImageDigest:  os.Getenv("EXPORTER_IMAGE_DIGEST"),
		CommitSHA:            os.Getenv("GITHUB_SHA"),
		ReferenceHardware:    os.Getenv("QUALIFICATION_REFERENCE_HARDWARE"),
		ResourceBudgets:      resourceBudgets,
		Notes:                "This report proves local file-to-CloudEvents reconciliation only; it does not satisfy real gateway, external destination, signing, canary, or pilot gates.",
	}

	generationStarted := time.Now()
	generated, generatedBytes, generationErr := generateCapacityRecords(
		harness.logPath,
		expected,
		rate,
		duration,
		generationStarted.UnixMilli(),
	)
	generationElapsed := time.Since(generationStarted)
	if destinationOutage {
		report.OutageDuration = generationElapsed.String()
		fixture.setStatus(http.StatusAccepted)
	}
	report.GeneratedEvents = generated
	report.GeneratedBytes = generatedBytes
	if generationElapsed > 0 {
		report.GenerationRate = float64(generated) / generationElapsed.Seconds()
	}

	drainStarted := time.Now()
	if generationErr == nil {
		deadline := time.Now().Add(drainTimeout)
		for time.Now().Before(deadline) {
			unique, _, _, invalid := fixture.snapshot()
			if unique == expected || invalid > 0 {
				break
			}
			time.Sleep(250 * time.Millisecond)
		}
	}
	report.DrainDuration = time.Since(drainStarted).String()
	report.UniqueDelivered, report.TotalDeliveries, report.DuplicateDeliveries, report.InvalidDeliveries = fixture.snapshot()
	report.DeliveryLatency = fixture.latencyReport()
	report.RejectedRequests = fixture.rejectedRequestCount()
	report.MissingSequences = fixture.missing(100)
	report.Resources = sampler.stop()
	report.ResourceBudgets.Violations = report.ResourceBudgets.evaluate(report.Resources)
	samplerStopped = true
	report.FinishedAt = time.Now().UTC().Format(time.RFC3339Nano)
	if generationErr == nil && generated == expected && report.UniqueDelivered == expected &&
		report.InvalidDeliveries == 0 && len(report.MissingSequences) == 0 &&
		(!destinationOutage || report.RejectedRequests > 0) && capacityRuntimeEvidenceIsComplete(report) &&
		len(report.ResourceBudgets.Violations) == 0 {
		report.Result = "passed"
	}
	report.GateEligible = report.Result == "passed" && capacityProfileIsGateEligible(destinationOutage, rate, duration) &&
		report.GenerationRate >= float64(rate)*0.99 && capacityEvidenceIsComplete(report)

	reportPath := os.Getenv("CAPACITY_REPORT_PATH")
	if reportPath == "" {
		reportPath = filepath.Join(t.TempDir(), "capacity-report.json")
	}
	if err := writeCapacityReport(reportPath, report); err != nil {
		t.Fatalf("write capacity report: %v", err)
	}
	t.Logf("capacity qualification report: %s", reportPath)

	if generationErr != nil {
		t.Fatalf("generate capacity records: %v", generationErr)
	}
	if report.Result != "passed" {
		if len(report.ResourceBudgets.Violations) > 0 {
			t.Fatalf("capacity resource budgets exceeded: %s", strings.Join(report.ResourceBudgets.Violations, "; "))
		}
		t.Fatalf(
			"capacity reconciliation failed: generated=%d unique=%d deliveries=%d duplicates=%d invalid=%d missing(first 100)=%v",
			report.GeneratedEvents,
			report.UniqueDelivered,
			report.TotalDeliveries,
			report.DuplicateDeliveries,
			report.InvalidDeliveries,
			report.MissingSequences,
		)
	}
}

func capacityEvidenceIsComplete(report capacityReport) bool {
	return report.ReferenceHardware != "" && report.ExporterVersion != "" &&
		validHexIdentifier(report.CommitSHA, "", 20) &&
		validHexIdentifier(report.ExporterBinarySHA256, "sha256:", sha256.Size) &&
		(report.ExporterImageDigest == "" || validHexIdentifier(report.ExporterImageDigest, "sha256:", sha256.Size)) &&
		report.ResourceBudgets.Enforced && len(report.ResourceBudgets.Violations) == 0 &&
		capacityRuntimeEvidenceIsComplete(report)
}

func capacityResourceBudgetsFromEnvironment() (capacityResourceBudgets, error) {
	budget := capacityResourceBudgets{Violations: []string{}}
	type signedBudget struct {
		name   string
		target *int64
	}
	signed := []signedBudget{
		{name: "CAPACITY_MAX_RSS_BYTES", target: &budget.MaximumRSSBytes},
		{name: "CAPACITY_MAX_CHECKPOINT_BYTES", target: &budget.MaximumCheckpointBytes},
		{name: "CAPACITY_MAX_QUEUE_BYTES", target: &budget.MaximumQueueBytes},
		{name: "CAPACITY_MAX_RECOVERY_BYTES", target: &budget.MaximumRecoveryBytes},
	}
	configured := 0
	for _, candidate := range signed {
		value := os.Getenv(candidate.name)
		if value == "" {
			continue
		}
		parsed, err := strconv.ParseInt(value, 10, 64)
		if err != nil || parsed <= 0 {
			return capacityResourceBudgets{}, fmt.Errorf("%s must be a positive base-10 integer", candidate.name)
		}
		*candidate.target = parsed
		configured++
	}
	if value := os.Getenv("CAPACITY_MAX_PROCESS_WRITTEN_BYTES"); value != "" {
		parsed, err := strconv.ParseUint(value, 10, 64)
		if err != nil || parsed == 0 {
			return capacityResourceBudgets{}, fmt.Errorf("CAPACITY_MAX_PROCESS_WRITTEN_BYTES must be a positive base-10 integer")
		}
		budget.MaximumProcessWrittenBytes = parsed
		configured++
	}
	if configured != 0 && configured != len(signed)+1 {
		return capacityResourceBudgets{}, fmt.Errorf("capacity resource budgets must configure all five CAPACITY_MAX_* variables or none")
	}
	budget.Enforced = configured > 0
	return budget, nil
}

func (b capacityResourceBudgets) evaluate(resources capacityResourceReport) []string {
	violations := []string{}
	if !b.Enforced {
		return violations
	}
	if resources.MaximumRSSBytes > b.MaximumRSSBytes {
		violations = append(violations, fmt.Sprintf("maximum RSS %d > %d bytes", resources.MaximumRSSBytes, b.MaximumRSSBytes))
	}
	if resources.ProcessWrittenBytes > b.MaximumProcessWrittenBytes {
		violations = append(violations, fmt.Sprintf("process writes %d > %d bytes", resources.ProcessWrittenBytes, b.MaximumProcessWrittenBytes))
	}
	if resources.MaximumCheckpointBytes > b.MaximumCheckpointBytes {
		violations = append(violations, fmt.Sprintf("checkpoint storage %d > %d bytes", resources.MaximumCheckpointBytes, b.MaximumCheckpointBytes))
	}
	if resources.MaximumQueueBytes > b.MaximumQueueBytes {
		violations = append(violations, fmt.Sprintf("queue storage %d > %d bytes", resources.MaximumQueueBytes, b.MaximumQueueBytes))
	}
	if resources.MaximumRecoveryBytes > b.MaximumRecoveryBytes {
		violations = append(violations, fmt.Sprintf("recovery storage %d > %d bytes", resources.MaximumRecoveryBytes, b.MaximumRecoveryBytes))
	}
	return violations
}

func capacityRuntimeEvidenceIsComplete(report capacityReport) bool {
	return report.Resources.Supported && report.Resources.Samples >= 2 && report.Resources.SampleErrors == 0 &&
		report.Resources.MeanRSSBytes > 0 && report.Resources.MaximumRSSBytes >= report.Resources.MeanRSSBytes &&
		report.Resources.MaximumCPUPercent >= report.Resources.MeanCPUPercent &&
		report.Resources.MaximumCheckpointBytes > 0 && report.Resources.MaximumRecoveryBytes > 0 &&
		(!report.DestinationOutage || report.Resources.MaximumQueueBytes > 0) &&
		report.DeliveryLatency.Count == report.UniqueDelivered && report.DeliveryLatency.Count > 0
}

func validHexIdentifier(value string, prefix string, size int) bool {
	encoded := strings.TrimPrefix(value, prefix)
	if prefix != "" && encoded == value {
		return false
	}
	decoded, err := hex.DecodeString(encoded)
	return err == nil && len(decoded) == size && encoded == strings.ToLower(encoded)
}

func capacityBinaryDigest(path string) (string, error) {
	file, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer func() { _ = file.Close() }()
	hash := sha256.New()
	if _, err := io.Copy(hash, file); err != nil {
		return "", err
	}
	return fmt.Sprintf("sha256:%x", hash.Sum(nil)), nil
}

func generateCapacityRecords(
	path string,
	expected int,
	rate int,
	duration time.Duration,
	baseMillis int64,
) (int, int64, error) {
	file, err := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		return 0, 0, err
	}
	defer func() {
		_ = file.Close()
	}()
	writer := bufio.NewWriterSize(file, 1024*1024)
	started := time.Now()
	generated := 0
	var generatedBytes int64
	ticker := time.NewTicker(50 * time.Millisecond)
	defer ticker.Stop()

	writeThrough := func(target int) error {
		for generated < target {
			targetBytes := 2048
			if generated%100 == 99 {
				targetBytes = 64 * 1024
			}
			record, recordErr := capacityRecord(generated, baseMillis+int64(generated*1000/rate), targetBytes)
			if recordErr != nil {
				return recordErr
			}
			written, writeErr := writer.Write(record)
			generatedBytes += int64(written)
			if writeErr != nil {
				return writeErr
			}
			if writeErr := writer.WriteByte('\n'); writeErr != nil {
				return writeErr
			}
			generatedBytes++
			generated++
		}
		return writer.Flush()
	}

	for generated < expected {
		elapsed := time.Since(started)
		target := int(elapsed.Seconds() * float64(rate))
		if target > expected {
			target = expected
		}
		if err := writeThrough(target); err != nil {
			return generated, generatedBytes, err
		}
		if elapsed >= duration {
			break
		}
		<-ticker.C
	}
	if err := writeThrough(expected); err != nil {
		return generated, generatedBytes, err
	}
	if err := file.Sync(); err != nil {
		return generated, generatedBytes, err
	}
	return generated, generatedBytes, nil
}

func capacityRecord(sequence int, eventMillis int64, targetBytes int) ([]byte, error) {
	event := map[string]any{
		"activity_id":  1,
		"category_uid": 4,
		"class_uid":    4001,
		"message":      "CONNECT denied capacity.example.com:443",
		"metadata": map[string]any{
			"version":            "1.8.0",
			"original_event_uid": fmt.Sprintf("capacity-%d", sequence),
		},
		"severity_id": 4,
		"time":        eventMillis,
		"type_uid":    400101,
		"unmapped": map[string]any{
			"sandbox_id": "sandbox-capacity",
			"padding":    "",
		},
	}
	encoded, err := json.Marshal(event)
	if err != nil {
		return nil, err
	}
	if len(encoded) > targetBytes {
		return nil, fmt.Errorf("capacity record base is %d bytes, exceeds target %d", len(encoded), targetBytes)
	}
	event["unmapped"].(map[string]any)["padding"] = strings.Repeat("x", targetBytes-len(encoded))
	encoded, err = json.Marshal(event)
	if err != nil {
		return nil, err
	}
	if len(encoded) != targetBytes {
		return nil, fmt.Errorf("capacity record is %d bytes, want %d", len(encoded), targetBytes)
	}
	return encoded, nil
}

func writeCapacityReport(path string, report capacityReport) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		return err
	}
	encoded, err := json.MarshalIndent(report, "", "  ")
	if err != nil {
		return err
	}
	if err := validateCapacityReport(encoded); err != nil {
		return fmt.Errorf("validate capacity report: %w", err)
	}
	return os.WriteFile(path, append(encoded, '\n'), 0o600)
}

func validateCapacityReport(encoded []byte) error {
	var reportDocument any
	if err := json.Unmarshal(encoded, &reportDocument); err != nil {
		return err
	}
	schema, err := os.ReadFile("qualification/capacity-report-v1.schema.json")
	if err != nil {
		return err
	}
	var schemaDocument any
	if err := json.Unmarshal(schema, &schemaDocument); err != nil {
		return err
	}
	compiler := jsonschema.NewCompiler()
	compiler.AssertFormat()
	if err := compiler.AddResource("urn:openshell:capacity-report:1.2", schemaDocument); err != nil {
		return err
	}
	compiled, err := compiler.Compile("urn:openshell:capacity-report:1.2")
	if err != nil {
		return err
	}
	return compiled.Validate(reportDocument)
}

func TestCapacityReportSchemaIsStrict(t *testing.T) {
	now := time.Now().UTC().Format(time.RFC3339Nano)
	report := capacityReport{
		SchemaVersion:     "1.2",
		Result:            "passed",
		QualificationKind: "local durable-file sustained-delivery",
		StartedAt:         now,
		FinishedAt:        now,
		RequestedRate:     1,
		RequestedDuration: time.Second.String(),
		GeneratedEvents:   1,
		GeneratedBytes:    2048,
		GenerationRate:    1,
		UniqueDelivered:   1,
		TotalDeliveries:   1,
		DrainDuration:     time.Millisecond.String(),
		Resources: capacityResourceReport{
			Supported:              true,
			SampleInterval:         (10 * time.Millisecond).String(),
			Samples:                2,
			MeanRSSBytes:           1024,
			MaximumRSSBytes:        1024,
			MaximumCheckpointBytes: 512,
			MaximumRecoveryBytes:   2048,
		},
		ResourceBudgets: capacityResourceBudgets{
			Enforced:                   true,
			MaximumRSSBytes:            2048,
			MaximumProcessWrittenBytes: 4096,
			MaximumCheckpointBytes:     1024,
			MaximumQueueBytes:          1024,
			MaximumRecoveryBytes:       4096,
			Violations:                 []string{},
		},
		DeliveryLatency: capacityLatencyReport{Count: 1},
		Notes:           "short local regression evidence only",
	}
	encoded, err := json.Marshal(report)
	if err != nil {
		t.Fatal(err)
	}
	if err := validateCapacityReport(encoded); err != nil {
		t.Fatalf("valid report rejected: %v", err)
	}

	for name, mutate := range map[string]func(map[string]any){
		"unknown field":   func(document map[string]any) { document["unexpected"] = true },
		"old version":     func(document map[string]any) { document["schema_version"] = "1.1" },
		"bad timestamp":   func(document map[string]any) { document["started_at"] = "not-a-timestamp" },
		"missing budgets": func(document map[string]any) { delete(document, "resource_budgets") },
		"passed with violation": func(document map[string]any) {
			document["resource_budgets"].(map[string]any)["violations"] = []any{"maximum RSS exceeded"}
		},
		"enforced zero budget": func(document map[string]any) {
			document["resource_budgets"].(map[string]any)["maximum_rss_bytes"] = float64(0)
		},
		"unenforced nonzero budget": func(document map[string]any) {
			budgets := document["resource_budgets"].(map[string]any)
			budgets["enforced"] = false
		},
	} {
		t.Run(name, func(t *testing.T) {
			var document map[string]any
			if err := json.Unmarshal(encoded, &document); err != nil {
				t.Fatal(err)
			}
			mutate(document)
			candidate, err := json.Marshal(document)
			if err != nil {
				t.Fatal(err)
			}
			if err := validateCapacityReport(candidate); err == nil {
				t.Fatal("invalid capacity report passed strict schema validation")
			}
		})
	}
}

func positiveEnvInt(t *testing.T, name string, fallback int) int {
	t.Helper()
	value := os.Getenv(name)
	if value == "" {
		return fallback
	}
	parsed, err := strconv.Atoi(value)
	if err != nil || parsed <= 0 {
		t.Fatalf("%s must be a positive integer", name)
	}
	return parsed
}

func TestCapacityResourceBudgetsRequireCompletePositiveSet(t *testing.T) {
	names := []string{
		"CAPACITY_MAX_RSS_BYTES",
		"CAPACITY_MAX_PROCESS_WRITTEN_BYTES",
		"CAPACITY_MAX_CHECKPOINT_BYTES",
		"CAPACITY_MAX_QUEUE_BYTES",
		"CAPACITY_MAX_RECOVERY_BYTES",
	}
	for _, name := range names {
		t.Setenv(name, "")
	}
	budget, err := capacityResourceBudgetsFromEnvironment()
	if err != nil {
		t.Fatal(err)
	}
	if budget.Enforced || budget.Violations == nil {
		t.Fatalf("empty budget environment = %+v", budget)
	}

	for _, name := range names {
		t.Setenv(name, "1024")
	}
	budget, err = capacityResourceBudgetsFromEnvironment()
	if err != nil {
		t.Fatal(err)
	}
	if !budget.Enforced || budget.MaximumRSSBytes != 1024 || budget.MaximumProcessWrittenBytes != 1024 {
		t.Fatalf("complete budget environment = %+v", budget)
	}

	t.Setenv("CAPACITY_MAX_QUEUE_BYTES", "")
	if _, err := capacityResourceBudgetsFromEnvironment(); err == nil {
		t.Fatal("partial resource budget configuration passed")
	}
	t.Setenv("CAPACITY_MAX_QUEUE_BYTES", "not-a-number")
	if _, err := capacityResourceBudgetsFromEnvironment(); err == nil {
		t.Fatal("malformed resource budget passed")
	}
	t.Setenv("CAPACITY_MAX_QUEUE_BYTES", "0")
	if _, err := capacityResourceBudgetsFromEnvironment(); err == nil {
		t.Fatal("zero resource budget passed")
	}
}

func TestCapacityResourceBudgetsReportEveryExceededDimension(t *testing.T) {
	budget := capacityResourceBudgets{
		Enforced:                   true,
		MaximumRSSBytes:            100,
		MaximumProcessWrittenBytes: 100,
		MaximumCheckpointBytes:     100,
		MaximumQueueBytes:          100,
		MaximumRecoveryBytes:       100,
	}
	resources := capacityResourceReport{
		MaximumRSSBytes:        101,
		ProcessWrittenBytes:    101,
		MaximumCheckpointBytes: 101,
		MaximumQueueBytes:      101,
		MaximumRecoveryBytes:   101,
	}
	violations := budget.evaluate(resources)
	if len(violations) != 5 {
		t.Fatalf("budget violations = %v, want all five dimensions", violations)
	}
	budget.MaximumRSSBytes = 101
	budget.MaximumProcessWrittenBytes = 101
	budget.MaximumCheckpointBytes = 101
	budget.MaximumQueueBytes = 101
	budget.MaximumRecoveryBytes = 101
	if violations := budget.evaluate(resources); len(violations) != 0 {
		t.Fatalf("values equal to budgets were rejected: %v", violations)
	}
	budget.Enforced = false
	budget.MaximumRSSBytes = 1
	if violations := budget.evaluate(resources); len(violations) != 0 {
		t.Fatalf("disabled budgets produced violations: %v", violations)
	}
}

func positiveEnvDuration(t *testing.T, name string, fallback time.Duration) time.Duration {
	t.Helper()
	value := os.Getenv(name)
	if value == "" {
		return fallback
	}
	parsed, err := time.ParseDuration(value)
	if err != nil || parsed <= 0 {
		t.Fatalf("%s must be a positive Go duration", name)
	}
	return parsed
}

func TestCapacityRecordHasRequestedSizeAndStableSequence(t *testing.T) {
	for _, size := range []int{2048, 64 * 1024} {
		record, err := capacityRecord(42, 1787080000000, size)
		if err != nil {
			t.Fatal(err)
		}
		if len(record) != size {
			t.Fatalf("capacity record length = %d, want %d", len(record), size)
		}
		var decoded struct {
			Metadata struct {
				OriginalEventUID string `json:"original_event_uid"`
			} `json:"metadata"`
		}
		if err := json.Unmarshal(record, &decoded); err != nil {
			t.Fatal(err)
		}
		if decoded.Metadata.OriginalEventUID != "capacity-42" {
			t.Fatalf("original event UID = %q", decoded.Metadata.OriginalEventUID)
		}
	}
}

func optionalBoolEnv(t *testing.T, name string) bool {
	t.Helper()
	value := os.Getenv(name)
	if value == "" || value == "false" {
		return false
	}
	if value == "true" {
		return true
	}
	t.Fatalf("%s must be true or false", name)
	return false
}

func capacityQualificationKind(destinationOutage bool) string {
	if destinationOutage {
		return "local durable-file destination-outage-and-drain"
	}
	return "local durable-file sustained-delivery"
}

func capacityProfileIsGateEligible(destinationOutage bool, rate int, duration time.Duration) bool {
	if destinationOutage {
		return rate == 1000 && duration >= 30*time.Minute
	}
	return rate == 1000 && duration >= time.Hour
}

func TestCapacityEvidenceRequiresExactTestedArtifact(t *testing.T) {
	report := capacityReport{
		ExporterVersion:      "0.0.2",
		ExporterBinarySHA256: "sha256:" + strings.Repeat("a", 64),
		CommitSHA:            strings.Repeat("b", 40),
		ReferenceHardware:    "4 CPU, 16 GiB, ext4",
		UniqueDelivered:      1,
		Resources: capacityResourceReport{
			Supported:              true,
			Samples:                2,
			MeanRSSBytes:           1024,
			MaximumRSSBytes:        1024,
			MaximumCheckpointBytes: 512,
			MaximumRecoveryBytes:   2048,
		},
		ResourceBudgets: capacityResourceBudgets{
			Enforced:                   true,
			MaximumRSSBytes:            2048,
			MaximumProcessWrittenBytes: 4096,
			MaximumCheckpointBytes:     1024,
			MaximumQueueBytes:          1024,
			MaximumRecoveryBytes:       4096,
			Violations:                 []string{},
		},
		DeliveryLatency: capacityLatencyReport{Count: 1},
	}
	if !capacityEvidenceIsComplete(report) {
		t.Fatal("complete tested-artifact evidence should pass")
	}
	report.ExporterImageDigest = "sha256:" + strings.Repeat("c", 64)
	if !capacityEvidenceIsComplete(report) {
		t.Fatal("valid optional image digest should pass")
	}
	for name, mutate := range map[string]func(*capacityReport){
		"missing version":       func(candidate *capacityReport) { candidate.ExporterVersion = "" },
		"invalid binary digest": func(candidate *capacityReport) { candidate.ExporterBinarySHA256 = "sha256:not-hex" },
		"invalid commit":        func(candidate *capacityReport) { candidate.CommitSHA = "abc123" },
		"missing hardware":      func(candidate *capacityReport) { candidate.ReferenceHardware = "" },
		"invalid image digest":  func(candidate *capacityReport) { candidate.ExporterImageDigest = "latest" },
		"unsupported sampler":   func(candidate *capacityReport) { candidate.Resources.Supported = false },
		"insufficient samples":  func(candidate *capacityReport) { candidate.Resources.Samples = 1 },
		"sampling errors":       func(candidate *capacityReport) { candidate.Resources.SampleErrors = 1 },
		"missing mean rss":      func(candidate *capacityReport) { candidate.Resources.MeanRSSBytes = 0 },
		"missing rss":           func(candidate *capacityReport) { candidate.Resources.MaximumRSSBytes = 0 },
		"rss mean above max":    func(candidate *capacityReport) { candidate.Resources.MeanRSSBytes = 2048 },
		"cpu mean above max":    func(candidate *capacityReport) { candidate.Resources.MeanCPUPercent = 1 },
		"missing checkpoint":    func(candidate *capacityReport) { candidate.Resources.MaximumCheckpointBytes = 0 },
		"missing recovery":      func(candidate *capacityReport) { candidate.Resources.MaximumRecoveryBytes = 0 },
		"outage without queue":  func(candidate *capacityReport) { candidate.DestinationOutage = true },
		"missing latency":       func(candidate *capacityReport) { candidate.DeliveryLatency.Count = 0 },
		"budgets not enforced":  func(candidate *capacityReport) { candidate.ResourceBudgets.Enforced = false },
		"budget violation": func(candidate *capacityReport) {
			candidate.ResourceBudgets.Violations = []string{"maximum RSS exceeded"}
		},
	} {
		t.Run(name, func(t *testing.T) {
			candidate := report
			mutate(&candidate)
			if capacityEvidenceIsComplete(candidate) {
				t.Fatal("incomplete or malformed evidence passed")
			}
		})
	}
}

func TestCapacityGateProfilesAreExplicit(t *testing.T) {
	if !capacityProfileIsGateEligible(false, 1000, time.Hour) {
		t.Fatal("required sustained profile should be gate eligible")
	}
	if capacityProfileIsGateEligible(false, 1000, 59*time.Minute) {
		t.Fatal("short sustained profile must not be gate eligible")
	}
	if !capacityProfileIsGateEligible(true, 1000, 30*time.Minute) {
		t.Fatal("required outage profile should be gate eligible")
	}
	if capacityProfileIsGateEligible(true, 1000, 29*time.Minute) {
		t.Fatal("short outage profile must not be gate eligible")
	}
}
