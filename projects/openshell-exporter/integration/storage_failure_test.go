// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package integration

import (
	"bytes"
	"context"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"
)

func TestBoundedQueueStorageExhaustionRetainsEveryIdentityAcrossRecoveryPaths(t *testing.T) {
	webhook := newWebhookFixture()
	defer webhook.close()
	webhook.setStatus(http.StatusServiceUnavailable)
	metricsPort := availableLoopbackPort(t)
	harness := startExporterHarnessWithOptions(t, webhook.server.URL, exporterHarnessOptions{
		queueSize:         100,
		queueMaxSizeBytes: 128 << 10,
		sendBatchSize:     1,
		batchTimeout:      50 * time.Millisecond,
		retryMaxInterval:  100 * time.Millisecond,
		metricsPort:       metricsPort,
	})

	const events = 24
	for sequence := 500; sequence < 500+events; sequence++ {
		harness.appendEventWithPayload(sequence, 24<<10)
		time.Sleep(250 * time.Millisecond)
	}

	waitFor(t, "bounded queue storage enqueue failure", func() bool {
		return metricHasPositiveValueWithLabel(
			scrapePrometheus(metricsPort),
			"otelcol_exporter_enqueue_failed_log_records",
			`exporter="cloudevents"`,
		)
	})
	if count := webhook.eventCount(); count != 0 {
		t.Fatalf("outage unexpectedly delivered %d events", count)
	}
	if size := soleRegularFileSize(t, harness.queuePath); size > 128<<10 {
		t.Fatalf("queue database grew to %d bytes, exceeding configured 128 KiB limit", size)
	}

	harness.stop()
	recoverySnapshot, err := os.ReadFile(harness.recoveryPath)
	if err != nil {
		t.Fatal(err)
	}
	replaceConfigValue(t, harness.configPath, "    max_size: 131072\n", "    max_size: 8388608\n")
	webhook.setStatus(http.StatusAccepted)
	harness.start()
	waitFor(t, "every source identity at the destination or in recovery", func() bool {
		return reconciledIdentityCount(recoverySnapshot, webhook, 500, events) == events
	})
	if webhook.eventCount() == events {
		t.Fatal("forced restart unexpectedly delivered every record; test did not exercise recovery reconciliation")
	}
	if count := reconciledIdentityCount(recoverySnapshot, webhook, 500, events); count != events {
		t.Fatalf("reconciled identities=%d, want %d", count, events)
	}
}

func TestCorruptPersistentQueueFailsClosedAndRestoresWithoutLoss(t *testing.T) {
	webhook := newWebhookFixture()
	defer webhook.close()
	webhook.setStatus(http.StatusServiceUnavailable)
	harness := startExporterHarness(t, webhook.server.URL, 100)
	harness.appendEvent(600)
	waitFor(t, "queued evidence before corruption", func() bool {
		return webhook.requestCount() > 0 && soleRegularFileSizeNoFail(harness.queuePath) > 0
	})
	harness.stop()

	queueFile := soleRegularFile(t, harness.queuePath)
	backup, err := os.ReadFile(queueFile)
	if err != nil {
		t.Fatal(err)
	}
	corrupt := bytes.Repeat([]byte("not-a-bbolt-database\n"), 256)
	if err := os.WriteFile(queueFile, corrupt, 0o600); err != nil {
		t.Fatal(err)
	}

	output, startErr := runExporterUntilExit(t, harness.binary, harness.configPath, 10*time.Second)
	if startErr == nil {
		t.Fatal("exporter started with a corrupt persistent queue; want fail-closed startup")
	}
	if !strings.Contains(output, "invalid database") &&
		!strings.Contains(output, "checksum error") &&
		!strings.Contains(output, "file size too small") {
		t.Fatalf("startup did not report bounded corruption diagnosis: %s", output)
	}
	gotCorrupt, err := os.ReadFile(queueFile)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(gotCorrupt, corrupt) {
		t.Fatal("failed startup modified or replaced the corrupt queue database")
	}
	if entries, err := os.ReadDir(harness.queuePath); err != nil || len(entries) != 1 {
		t.Fatalf("queue directory entries=%d err=%v, want the single preserved corrupt database", len(entries), err)
	}

	if err := os.WriteFile(queueFile, backup, 0o600); err != nil {
		t.Fatal(err)
	}
	webhook.setStatus(http.StatusAccepted)
	harness.start()
	waitFor(t, "restored queue delivery", func() bool { return webhook.eventCount() == 1 })
	if webhook.deliveryCount() != 1 {
		t.Fatalf("restored queue delivered %d records, want 1", webhook.deliveryCount())
	}
}

func replaceConfigValue(t *testing.T, path, old, replacement string) {
	t.Helper()
	encoded, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if count := bytes.Count(encoded, []byte(old)); count != 1 {
		t.Fatalf("config replacement target occurs %d times, want exactly one", count)
	}
	encoded = bytes.Replace(encoded, []byte(old), []byte(replacement), 1)
	if err := os.WriteFile(path, encoded, 0o600); err != nil {
		t.Fatal(err)
	}
}

func reconciledIdentityCount(recovery []byte, webhook *webhookFixture, first, count int) int {
	webhook.mu.Lock()
	destination := make([]byte, 0)
	for _, event := range webhook.events {
		destination = append(destination, event...)
	}
	webhook.mu.Unlock()

	covered := 0
	for sequence := first; sequence < first+count; sequence++ {
		uid := []byte("integration-" + strconv.Itoa(sequence))
		if bytes.Contains(recovery, uid) || bytes.Contains(destination, uid) {
			covered++
		}
	}
	return covered
}

func scrapePrometheus(port int) string {
	client := &http.Client{Timeout: time.Second}
	request, err := http.NewRequestWithContext(context.Background(), http.MethodGet, "http://127.0.0.1:"+strconv.Itoa(port)+"/metrics", nil)
	if err != nil {
		return ""
	}
	response, err := client.Do(request)
	if err != nil {
		return ""
	}
	defer func() { _ = response.Body.Close() }()
	if response.StatusCode != http.StatusOK {
		return ""
	}
	encoded, err := io.ReadAll(io.LimitReader(response.Body, 4<<20))
	if err != nil {
		return ""
	}
	return string(encoded)
}

func metricHasPositiveValueWithLabel(scrape, name, label string) bool {
	for _, line := range strings.Split(scrape, "\n") {
		if !strings.HasPrefix(line, name) || !strings.Contains(line, label) {
			continue
		}
		fields := strings.Fields(line)
		if len(fields) != 2 {
			continue
		}
		value, err := strconv.ParseFloat(fields[1], 64)
		if err == nil && value > 0 {
			return true
		}
	}
	return false
}

func soleRegularFileSize(t *testing.T, directory string) int64 {
	t.Helper()
	path := soleRegularFile(t, directory)
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	return info.Size()
}

func soleRegularFileSizeNoFail(directory string) int64 {
	entries, err := os.ReadDir(directory)
	if err != nil || len(entries) != 1 || entries[0].IsDir() {
		return 0
	}
	info, err := entries[0].Info()
	if err != nil {
		return 0
	}
	return info.Size()
}

func soleRegularFile(t *testing.T, directory string) string {
	t.Helper()
	entries, err := os.ReadDir(directory)
	if err != nil {
		t.Fatal(err)
	}
	if len(entries) != 1 || entries[0].IsDir() {
		t.Fatalf("queue directory has %d entries, want one regular database", len(entries))
	}
	return filepath.Join(directory, entries[0].Name())
}

func runExporterUntilExit(t *testing.T, binary, config string, timeout time.Duration) (string, error) {
	t.Helper()
	ctx, cancel := context.WithTimeout(t.Context(), timeout)
	defer cancel()
	command := exec.CommandContext(ctx, binary, "--config", config)
	var output bytes.Buffer
	command.Stdout = &output
	command.Stderr = &output
	err := command.Run()
	if ctx.Err() != nil {
		t.Fatalf("exporter did not fail closed within %s; output: %s", timeout, output.String())
	}
	return output.String(), err
}
