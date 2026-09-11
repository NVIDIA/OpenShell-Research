// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package integration

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"
)

type webhookRequestObservation struct {
	EventCount    int
	EncodedBytes  int
	MaxEventBytes int
	EventIDs      []string
}

type webhookFixture struct {
	server     *httptest.Server
	mu         sync.Mutex
	status     int
	events     map[string]json.RawMessage
	deliveries int
	requests   int
	accepted   []webhookRequestObservation
}

func newWebhookFixture() *webhookFixture {
	fixture := &webhookFixture{
		status: http.StatusAccepted,
		events: map[string]json.RawMessage{},
	}
	fixture.server = httptest.NewServer(http.HandlerFunc(
		func(writer http.ResponseWriter, request *http.Request) {
			fixture.mu.Lock()
			defer fixture.mu.Unlock()
			fixture.requests++
			if fixture.status < 200 || fixture.status >= 300 {
				writer.WriteHeader(fixture.status)
				return
			}
			encoded, err := io.ReadAll(request.Body)
			if err != nil {
				writer.WriteHeader(http.StatusBadRequest)
				return
			}
			var events []json.RawMessage
			if err := json.Unmarshal(encoded, &events); err != nil {
				writer.WriteHeader(http.StatusBadRequest)
				return
			}
			observation := webhookRequestObservation{
				EventCount:   len(events),
				EncodedBytes: len(encoded),
				EventIDs:     make([]string, 0, len(events)),
			}
			for _, encodedEvent := range events {
				var event map[string]json.RawMessage
				if err := json.Unmarshal(encodedEvent, &event); err != nil {
					writer.WriteHeader(http.StatusBadRequest)
					return
				}
				var id string
				_ = json.Unmarshal(event["id"], &id)
				fixture.events[id] = event["data"]
				fixture.deliveries++
				observation.EventIDs = append(observation.EventIDs, id)
				if len(encodedEvent) > observation.MaxEventBytes {
					observation.MaxEventBytes = len(encodedEvent)
				}
			}
			fixture.accepted = append(fixture.accepted, observation)
			writer.WriteHeader(http.StatusAccepted)
		},
	))
	return fixture
}

func (f *webhookFixture) close() {
	f.server.Close()
}

func (f *webhookFixture) setStatus(status int) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.status = status
}

func (f *webhookFixture) eventCount() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return len(f.events)
}

func (f *webhookFixture) deliveryCount() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.deliveries
}

func (f *webhookFixture) requestCount() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.requests
}

func (f *webhookFixture) requestObservations() []webhookRequestObservation {
	f.mu.Lock()
	defer f.mu.Unlock()
	observations := make([]webhookRequestObservation, len(f.accepted))
	for index, observation := range f.accepted {
		observations[index] = observation
		observations[index].EventIDs = append([]string(nil), observation.EventIDs...)
	}
	return observations
}

func (f *webhookFixture) allValidationStatus(status string) bool {
	f.mu.Lock()
	defer f.mu.Unlock()
	if len(f.events) == 0 {
		return false
	}
	for _, encoded := range f.events {
		var envelope struct {
			Security struct {
				Validation struct {
					Status string `json:"status"`
				} `json:"validation"`
			} `json:"security"`
		}
		if json.Unmarshal(encoded, &envelope) != nil || envelope.Security.Validation.Status != status {
			return false
		}
	}
	return true
}

func (f *webhookFixture) allAcquisition(
	kind string,
	durability string,
	profile string,
) bool {
	f.mu.Lock()
	defer f.mu.Unlock()
	if len(f.events) == 0 {
		return false
	}
	for _, encoded := range f.events {
		var envelope struct {
			Acquisition struct {
				Kind       string `json:"kind"`
				Durability string `json:"durability"`
				Transport  string `json:"transport"`
				Coverage   struct {
					SourceProfile string `json:"source_profile"`
				} `json:"coverage"`
			} `json:"acquisition"`
		}
		if json.Unmarshal(encoded, &envelope) != nil ||
			envelope.Acquisition.Kind != kind ||
			envelope.Acquisition.Durability != durability ||
			envelope.Acquisition.Transport != "file" ||
			envelope.Acquisition.Coverage.SourceProfile != profile {
			return false
		}
	}
	return true
}

type exporterHarness struct {
	t              *testing.T
	binary         string
	configPath     string
	root           string
	logPath        string
	checkpointPath string
	queuePath      string
	recoveryPath   string
	command        *exec.Cmd
	cancel         context.CancelFunc
}

type exporterHarnessOptions struct {
	queueSize             int
	sendBatchSize         int
	batchTimeout          time.Duration
	retryMaxInterval      time.Duration
	sandboxOperationalLog bool
	metricsPort           int
	queueMaxSizeBytes     int64
}

func startExporterHarness(
	t *testing.T,
	webhookURL string,
	queueSize int,
) *exporterHarness {
	return startExporterHarnessWithOptions(t, webhookURL, exporterHarnessOptions{
		queueSize:        queueSize,
		sendBatchSize:    1,
		batchTimeout:     100 * time.Millisecond,
		retryMaxInterval: 200 * time.Millisecond,
	})
}

func startExporterHarnessWithOptions(
	t *testing.T,
	webhookURL string,
	options exporterHarnessOptions,
) *exporterHarness {
	t.Helper()
	if options.queueSize <= 0 || options.sendBatchSize <= 0 || options.batchTimeout <= 0 || options.retryMaxInterval <= 0 {
		t.Fatal("exporter harness queue, batch size, batch timeout, and retry interval must be positive")
	}
	binary := os.Getenv("TEST_EXPORTER_BINARY")
	if binary == "" {
		t.Skip("TEST_EXPORTER_BINARY is required for Collector integration")
	}
	absoluteBinary, err := filepath.Abs(binary)
	if err != nil {
		t.Fatal(err)
	}
	root := t.TempDir()
	logFilename := "openshell-ocsf.0.log"
	logPattern := "openshell-ocsf.*.log"
	acquisitionKind := "ocsf.file"
	sourceProfile := "ocsf.file"
	sourceInstance := "integration-jsonl"
	operators := `    operators:
      - type: json_parser
        parse_from: body
        parse_to: body
        on_error: send`
	if options.sandboxOperationalLog {
		logFilename = "openshell.2026-08-21.log"
		logPattern = "openshell.*.log"
		acquisitionKind = "sandbox.file_log"
		sourceProfile = "openshell.log"
		sourceInstance = "sandbox-operational-log"
		operators = ""
	}
	logPath := filepath.Join(root, logFilename)
	if err := os.WriteFile(logPath, nil, 0o600); err != nil {
		t.Fatal(err)
	}
	configPath := filepath.Join(root, "config.yaml")
	recoveryPath := filepath.Join(root, "recovery.json")
	checkpointPath := filepath.Join(root, "checkpoints")
	queuePath := filepath.Join(root, "queue")
	queueMaxSize := ""
	if options.queueMaxSizeBytes > 0 {
		queueMaxSize = fmt.Sprintf("    max_size: %d\n", options.queueMaxSizeBytes)
	}
	// Tests that do not inspect metrics must not bind the Collector's shared
	// default port; a developer may already have a local exporter on 8888.
	telemetry := "  telemetry:\n    metrics:\n      level: none\n"
	if options.metricsPort > 0 {
		telemetry = fmt.Sprintf(`  telemetry:
    metrics:
      level: normal
      readers:
        - pull:
            exporter:
              prometheus:
                host: 127.0.0.1
                port: %d
`, options.metricsPort)
	}
	config := fmt.Sprintf(`extensions:
  file_storage/checkpoints:
    directory: %s
    create_directory: true
  file_storage/queue:
    directory: %s
    create_directory: true
%s
  storagehealth:
    interval: 1s
    max_entries: 64
    paths:
      checkpoints: %s
      cloudevents_queue: %s
      recovery: %s
receivers:
  file_log/openshell:
    include: [%s/%s]
    start_at: beginning
    storage: file_storage/checkpoints
    retry_on_failure:
      enabled: true
      initial_interval: 50ms
      max_interval: 100ms
      max_elapsed_time: 0s
    include_file_path: true
    include_file_path_resolved: true
    include_file_record_offset: true
    include_file_record_number: true
    on_truncate: read_whole_file
    max_log_size: 8MiB
    max_log_size_behavior: split
    attributes:
      openshell.acquisition.kind: %s
%s
processors:
  openshell:
    source_profiles: [%s]
    gateway_id: integration
    workspace: default
    source_instance: %s
    default_sandbox_id: sandbox-integration
    validation:
      mode: mark
exporters:
  cloudevents:
    endpoint: %s
    allow_insecure_http: true
    default_source: openshell://integration
    max_events: 500
    max_event_bytes: 1048576
    max_request_bytes: 4194304
    retry_on_failure:
      enabled: true
      initial_interval: 50ms
      max_interval: %s
      max_elapsed_time: 0s
    sending_queue:
      enabled: true
      storage: file_storage/queue
      queue_size: %d
      num_consumers: 1
      block_on_overflow: true
      batch:
        flush_timeout: %s
        min_size: %d
        max_size: %d
        sizer: items
  file/recovery:
    path: %s
    format: json
service:
%s
  extensions: [file_storage/checkpoints, file_storage/queue, storagehealth]
  pipelines:
    logs:
      receivers: [file_log/openshell]
      processors: [openshell]
      exporters: [cloudevents, file/recovery]
`, checkpointPath, queuePath, queueMaxSize, checkpointPath, queuePath, recoveryPath, root, logPattern, acquisitionKind, operators, sourceProfile, sourceInstance,
		webhookURL, options.retryMaxInterval, options.queueSize, options.batchTimeout, options.sendBatchSize, options.sendBatchSize,
		recoveryPath, telemetry)
	if err := os.WriteFile(configPath, []byte(config), 0o600); err != nil {
		t.Fatal(err)
	}
	harness := &exporterHarness{
		t:              t,
		binary:         absoluteBinary,
		configPath:     configPath,
		root:           root,
		logPath:        logPath,
		checkpointPath: checkpointPath,
		queuePath:      queuePath,
		recoveryPath:   recoveryPath,
	}
	harness.start()
	t.Cleanup(harness.stop)
	time.Sleep(500 * time.Millisecond)
	return harness
}

func (h *exporterHarness) start() {
	h.t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	command := exec.CommandContext(ctx, h.binary, "--config", h.configPath)
	command.Stdout = os.Stdout
	command.Stderr = os.Stderr
	if err := command.Start(); err != nil {
		cancel()
		h.t.Fatal(err)
	}
	h.command = command
	h.cancel = cancel
}

func (h *exporterHarness) stop() {
	if h.command == nil || h.command.Process == nil {
		return
	}
	_ = h.command.Process.Signal(os.Interrupt)
	done := make(chan struct{})
	go func() {
		_ = h.command.Wait()
		close(done)
	}()
	select {
	case <-done:
	case <-time.After(5 * time.Second):
		h.cancel()
		<-done
	}
	h.command = nil
	h.cancel = nil
}

func (h *exporterHarness) appendRaw(body string) {
	h.t.Helper()
	file, err := os.OpenFile(h.logPath, os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		h.t.Fatal(err)
	}
	defer func() {
		if err := file.Close(); err != nil {
			h.t.Error(err)
		}
	}()
	if _, err := file.WriteString(body + "\n"); err != nil {
		h.t.Fatal(err)
	}
}

func (h *exporterHarness) appendEvent(sequence int) {
	h.appendEventWithPayload(sequence, 0)
}

func (h *exporterHarness) appendEventWithPayload(sequence, payloadBytes int) {
	h.t.Helper()
	unmapped := map[string]any{
		"sandbox_id": "sandbox-integration",
	}
	if payloadBytes > 0 {
		unmapped["padding"] = strings.Repeat("x", payloadBytes)
	}
	event := map[string]any{
		"activity_id":  1,
		"category_uid": 4,
		"class_uid":    4001,
		"message":      "CONNECT denied api.example.com:443",
		"metadata": map[string]any{
			"version":            "1.8.0",
			"original_event_uid": "integration-" + strconv.Itoa(sequence),
		},
		"severity_id": 4,
		"time":        time.Now().UnixMilli() + int64(sequence),
		"type_uid":    400101,
		"unmapped":    unmapped,
	}
	encoded, err := json.Marshal(event)
	if err != nil {
		h.t.Fatal(err)
	}
	file, err := os.OpenFile(h.logPath, os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		h.t.Fatal(err)
	}
	defer func() {
		if err := file.Close(); err != nil {
			h.t.Error(err)
		}
	}()
	if _, err := file.Write(append(encoded, '\n')); err != nil {
		h.t.Fatal(err)
	}
}

func waitFor(t *testing.T, description string, condition func() bool) {
	t.Helper()
	deadline := time.Now().Add(15 * time.Second)
	for time.Now().Before(deadline) {
		if condition() {
			return
		}
		time.Sleep(100 * time.Millisecond)
	}
	t.Fatalf("timed out waiting for %s", description)
}
