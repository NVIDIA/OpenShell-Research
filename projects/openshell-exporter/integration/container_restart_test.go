// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package integration

import (
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"sync"
	"testing"
	"time"
)

type containerWebhookFixture struct {
	server *httptest.Server

	mu         sync.Mutex
	status     int
	requests   int
	deliveries int
	events     map[string]struct{}
}

func newContainerWebhookFixture(t *testing.T) *containerWebhookFixture {
	t.Helper()
	fixture := &containerWebhookFixture{
		status: http.StatusServiceUnavailable,
		events: map[string]struct{}{},
	}
	listener, err := net.Listen("tcp4", "0.0.0.0:0")
	if err != nil {
		t.Fatal(err)
	}
	fixture.server = httptest.NewUnstartedServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		fixture.mu.Lock()
		fixture.requests++
		status := fixture.status
		fixture.mu.Unlock()
		if status < 200 || status >= 300 {
			writer.WriteHeader(status)
			return
		}
		var events []struct {
			ID string `json:"id"`
		}
		if err := json.NewDecoder(request.Body).Decode(&events); err != nil {
			writer.WriteHeader(http.StatusBadRequest)
			return
		}
		fixture.mu.Lock()
		defer fixture.mu.Unlock()
		for _, event := range events {
			if event.ID == "" {
				writer.WriteHeader(http.StatusBadRequest)
				return
			}
			fixture.events[event.ID] = struct{}{}
			fixture.deliveries++
		}
		writer.WriteHeader(http.StatusAccepted)
	}))
	fixture.server.Listener = listener
	fixture.server.Start()
	t.Cleanup(fixture.server.Close)
	return fixture
}

func (f *containerWebhookFixture) port(t *testing.T) string {
	t.Helper()
	_, port, err := net.SplitHostPort(f.server.Listener.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	return port
}

func (f *containerWebhookFixture) setStatus(status int) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.status = status
}

func (f *containerWebhookFixture) snapshot() (requests, unique, deliveries int) {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.requests, len(f.events), f.deliveries
}

func TestHardenedContainerRestartDrainsPersistentQueueWithoutSourceReplay(t *testing.T) {
	image := os.Getenv("TEST_EXPORTER_IMAGE")
	if image == "" {
		t.Skip("TEST_EXPORTER_IMAGE is required for container restart qualification")
	}
	if _, err := exec.LookPath("docker"); err != nil {
		t.Skip("docker CLI is required for container restart qualification")
	}
	runDocker(t, "image", "inspect", image)

	root, err := os.MkdirTemp("/tmp", "openshell-container-restart-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_ = os.RemoveAll(root)
	})
	if err := os.Chmod(root, 0o755); err != nil {
		t.Fatal(err)
	}
	for _, directory := range []string{"source", "state", "recovery"} {
		path := filepath.Join(root, directory)
		if err := os.Mkdir(path, 0o777); err != nil {
			t.Fatal(err)
		}
		mode := os.FileMode(0o777)
		if directory == "source" {
			mode = 0o755
		}
		if err := os.Chmod(path, mode); err != nil {
			t.Fatal(err)
		}
	}
	sourcePath := filepath.Join(root, "source", "openshell-ocsf.0.log")
	if err := os.WriteFile(sourcePath, nil, 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(sourcePath, 0o644); err != nil {
		t.Fatal(err)
	}

	webhook := newContainerWebhookFixture(t)
	config := fmt.Sprintf(`extensions:
  file_storage:
    directory: /qualification/state
    create_directory: true
receivers:
  file_log/openshell:
    include: [/qualification/source/openshell-ocsf.0.log]
    start_at: beginning
    storage: file_storage
    include_file_path: true
    include_file_path_resolved: true
    include_file_record_offset: true
    include_file_record_number: true
    on_truncate: read_whole_file
    max_log_size: 8MiB
    max_log_size_behavior: split
    retry_on_failure:
      enabled: true
      initial_interval: 50ms
      max_interval: 500ms
      max_elapsed_time: 0s
    attributes:
      openshell.acquisition.kind: ocsf.file
    operators:
      - type: json_parser
        parse_from: body
        parse_to: body
        on_error: send
processors:
  openshell:
    source_profiles: [ocsf.file]
    gateway_id: container-qualification
    workspace: default
    source_instance: container-jsonl
    default_sandbox_id: sandbox-container
    validation:
      mode: mark
exporters:
  cloudevents:
    endpoint: http://host.docker.internal:%s
    allow_insecure_http: true
    default_source: openshell://container-qualification
    retry_on_failure:
      enabled: true
      initial_interval: 50ms
      max_interval: 500ms
      max_elapsed_time: 0s
    sending_queue:
      enabled: true
      storage: file_storage
      queue_size: 100
      num_consumers: 1
      block_on_overflow: true
      batch:
        flush_timeout: 100ms
        min_size: 1
        max_size: 1
        sizer: items
  file/recovery:
    path: /qualification/recovery/recovery.json
    format: json
service:
  extensions: [file_storage]
  pipelines:
    logs:
      receivers: [file_log/openshell]
      processors: [openshell]
      exporters: [cloudevents, file/recovery]
`, webhook.port(t))
	configPath := filepath.Join(root, "config.yaml")
	if err := os.WriteFile(configPath, []byte(config), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(configPath, 0o644); err != nil {
		t.Fatal(err)
	}

	containerName := "openshell-exporter-restart-" + strconv.FormatInt(time.Now().UnixNano(), 10)
	t.Cleanup(func() {
		if t.Failed() {
			if output, err := exec.Command("docker", "logs", containerName).CombinedOutput(); err == nil {
				t.Logf("container logs:\n%s", output)
			}
		}
		_ = exec.Command("docker", "rm", "--force", containerName).Run()
	})
	runDocker(
		t,
		"run",
		"--detach",
		"--name", containerName,
		"--add-host", "host.docker.internal:host-gateway",
		"--read-only",
		"--tmpfs", "/tmp:rw,noexec,nosuid,size=16m",
		"--cap-drop", "ALL",
		"--security-opt", "no-new-privileges",
		"--pids-limit", "256",
		"--memory", "512m",
		"--cpus", "2",
		"--volume", root+":/qualification:Z",
		image,
		"--config=/qualification/config.yaml",
	)
	time.Sleep(500 * time.Millisecond)
	appendContainerEvent(t, sourcePath, 1)

	recoveryPath := filepath.Join(root, "recovery", "recovery.json")
	waitFor(t, "container retryable rejection and recovery archive", func() bool {
		requests, unique, _ := webhook.snapshot()
		info, err := os.Stat(recoveryPath)
		return requests > 0 && unique == 0 && err == nil && info.Size() > 0
	})
	runDocker(t, "stop", "--time", "5", containerName)
	if err := os.Remove(sourcePath); err != nil {
		t.Fatal(err)
	}

	webhook.setStatus(http.StatusAccepted)
	runDocker(t, "start", containerName)
	waitFor(t, "persistent queue drain after hardened container restart", func() bool {
		_, unique, _ := webhook.snapshot()
		return unique == 1
	})
	_, unique, deliveries := webhook.snapshot()
	if unique != 1 || deliveries != 1 {
		t.Fatalf("container restart delivered unique=%d total=%d, want 1 and 1", unique, deliveries)
	}
}

func appendContainerEvent(t *testing.T, path string, sequence int) {
	t.Helper()
	event := map[string]any{
		"activity_id":  1,
		"category_uid": 4,
		"class_uid":    4001,
		"message":      "CONNECT denied container.example.com:443",
		"metadata": map[string]any{
			"version":            "1.8.0",
			"original_event_uid": fmt.Sprintf("container-%d", sequence),
		},
		"severity_id": 4,
		"time":        time.Now().UnixMilli(),
		"type_uid":    400101,
		"unmapped": map[string]any{
			"sandbox_id": "sandbox-container",
		},
	}
	encoded, err := json.Marshal(event)
	if err != nil {
		t.Fatal(err)
	}
	file, err := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := file.Write(append(encoded, '\n')); err != nil {
		_ = file.Close()
		t.Fatal(err)
	}
	if err := file.Close(); err != nil {
		t.Fatal(err)
	}
}

func runDocker(t *testing.T, arguments ...string) string {
	t.Helper()
	command := exec.Command("docker", arguments...)
	output, err := command.CombinedOutput()
	if err != nil {
		t.Fatalf("docker %v: %v\n%s", arguments, err, output)
	}
	return string(output)
}
