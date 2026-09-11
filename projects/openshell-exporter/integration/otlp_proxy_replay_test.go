// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package integration

import (
	"bytes"
	"compress/gzip"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
)

func TestOTLPProxyPersistentQueueReplaysAfterCrash(t *testing.T) {
	binary := os.Getenv("TEST_OTLP_PROXY_BINARY")
	if binary == "" {
		t.Skip("TEST_OTLP_PROXY_BINARY is required for proxy integration")
	}
	absoluteBinary, err := filepath.Abs(binary)
	if err != nil {
		t.Fatal(err)
	}

	const spanName = "proxy-persistent-replay"
	var available atomic.Bool
	var attempts atomic.Int64
	var deliveredMu sync.Mutex
	delivered := map[string]int{}
	upstream := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.URL.Path != "/v1/traces" {
			http.NotFound(writer, request)
			return
		}
		attempts.Add(1)
		if !available.Load() {
			http.Error(writer, "destination unavailable", http.StatusServiceUnavailable)
			return
		}
		var bodyReader io.Reader = http.MaxBytesReader(writer, request.Body, 4<<20)
		if request.Header.Get("Content-Encoding") == "gzip" {
			compressed, gzipErr := gzip.NewReader(bodyReader)
			if gzipErr != nil {
				http.Error(writer, "invalid gzip body", http.StatusBadRequest)
				return
			}
			defer func() { _ = compressed.Close() }()
			bodyReader = compressed
		}
		body, readErr := io.ReadAll(bodyReader)
		if readErr != nil {
			http.Error(writer, "invalid body", http.StatusBadRequest)
			return
		}
		exportRequest := ptraceotlp.NewExportRequest()
		if unmarshalErr := exportRequest.UnmarshalProto(body); unmarshalErr != nil {
			http.Error(writer, "invalid OTLP protobuf", http.StatusBadRequest)
			return
		}
		deliveredMu.Lock()
		for resourceIndex := 0; resourceIndex < exportRequest.Traces().ResourceSpans().Len(); resourceIndex++ {
			resourceSpans := exportRequest.Traces().ResourceSpans().At(resourceIndex)
			for scopeIndex := 0; scopeIndex < resourceSpans.ScopeSpans().Len(); scopeIndex++ {
				spans := resourceSpans.ScopeSpans().At(scopeIndex).Spans()
				for spanIndex := 0; spanIndex < spans.Len(); spanIndex++ {
					delivered[spans.At(spanIndex).Name()]++
				}
			}
		}
		deliveredMu.Unlock()
		writer.Header().Set("Content-Type", "application/x-protobuf")
		writer.WriteHeader(http.StatusOK)
	}))
	t.Cleanup(upstream.Close)

	root := t.TempDir()
	queuePath := filepath.Join(root, "queue")
	configPath := filepath.Join(root, "config.yaml")
	receiverPort := freeTCPPort(t)
	config := fmt.Sprintf(`extensions:
  file_storage/relay_queue:
    directory: %s
    create_directory: true
receivers:
  otlp/relay:
    protocols:
      http:
        endpoint: 127.0.0.1:%d
        max_request_body_size: 4194304
processors:
  memory_limiter:
    check_interval: 1s
    limit_mib: 96
    spike_limit_mib: 24
exporters:
  otlp_http/upstream:
    endpoint: %s
    sending_queue:
      enabled: true
      storage: file_storage/relay_queue
      queue_size: 16
      num_consumers: 1
      block_on_overflow: true
      batch:
        flush_timeout: 50ms
        min_size: 1
        max_size: 1
        sizer: items
    retry_on_failure:
      enabled: true
      initial_interval: 50ms
      max_interval: 100ms
      max_elapsed_time: 0s
service:
  telemetry:
    metrics:
      level: none
  extensions: [file_storage/relay_queue]
  pipelines:
    traces:
      receivers: [otlp/relay]
      processors: [memory_limiter]
      exporters: [otlp_http/upstream]
`, queuePath, receiverPort, upstream.URL)
	if err := os.WriteFile(configPath, []byte(config), 0o600); err != nil {
		t.Fatal(err)
	}

	var logs bytes.Buffer
	start := func() *exec.Cmd {
		command := exec.Command(absoluteBinary, "--config", configPath)
		command.Stdout = &logs
		command.Stderr = &logs
		if startErr := command.Start(); startErr != nil {
			t.Fatalf("start OTLP proxy: %v", startErr)
		}
		return command
	}
	stop := func(command *exec.Cmd, crash bool) {
		if command == nil || command.Process == nil {
			return
		}
		if crash {
			_ = command.Process.Kill()
		} else {
			_ = command.Process.Signal(os.Interrupt)
		}
		done := make(chan struct{})
		go func() {
			_ = command.Wait()
			close(done)
		}()
		select {
		case <-done:
		case <-time.After(5 * time.Second):
			_ = command.Process.Kill()
			<-done
		}
	}

	current := start()
	t.Cleanup(func() {
		stop(current, false)
		if t.Failed() {
			t.Logf("OTLP proxy logs:\n%s", logs.String())
		}
	})

	payload := proxyReplayTracePayload(t, spanName)
	receiverEndpoint := fmt.Sprintf("http://127.0.0.1:%d/v1/traces", receiverPort)
	client := &http.Client{Timeout: 2 * time.Second}
	waitFor(t, "OTLP proxy admission acknowledgement", func() bool {
		status, requestErr := postRelayTrace(client, receiverEndpoint, "unused-local-token", payload)
		return requestErr == nil && status == http.StatusOK
	})
	waitFor(t, "failed upstream delivery attempt", func() bool {
		return attempts.Load() > 0
	})

	stop(current, true)
	current = nil
	entries, err := os.ReadDir(queuePath)
	if err != nil {
		t.Fatalf("read persisted proxy queue: %v", err)
	}
	if len(entries) == 0 {
		t.Fatal("proxy acknowledged OTLP input without creating persistent queue state")
	}

	available.Store(true)
	current = start()
	waitFor(t, "persisted OTLP trace replay after proxy crash", func() bool {
		deliveredMu.Lock()
		defer deliveredMu.Unlock()
		return delivered[spanName] > 0
	})
	deliveredMu.Lock()
	deliveries := delivered[spanName]
	deliveredMu.Unlock()
	if deliveries != 1 {
		t.Fatalf("successful deliveries for %q = %d, want 1", spanName, deliveries)
	}
	if attempts.Load() < 2 {
		t.Fatalf("upstream attempts = %d, want failed attempt plus replay", attempts.Load())
	}
}

func proxyReplayTracePayload(t *testing.T, name string) []byte {
	t.Helper()
	request := ptraceotlp.NewExportRequest()
	resourceSpans := request.Traces().ResourceSpans().AppendEmpty()
	resourceSpans.Resource().Attributes().PutStr("service.name", "proxy-replay-test")
	scopeSpans := resourceSpans.ScopeSpans().AppendEmpty()
	span := scopeSpans.Spans().AppendEmpty()
	span.SetName(name)
	span.SetTraceID(pcommon.TraceID{0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15})
	span.SetSpanID(pcommon.SpanID{0, 1, 2, 3, 4, 5, 6, 7})
	span.SetStartTimestamp(pcommon.NewTimestampFromTime(time.Now().Add(-time.Millisecond)))
	span.SetEndTimestamp(pcommon.NewTimestampFromTime(time.Now()))
	payload, err := request.MarshalProto()
	if err != nil {
		t.Fatal(err)
	}
	return payload
}
