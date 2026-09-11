// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package integration

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"sort"
	"strings"
	"testing"
	"time"
)

const (
	cloudEventsMaxEvents       = 500
	cloudEventsMaxEventBytes   = 1024 * 1024
	cloudEventsMaxRequestBytes = 4 * 1024 * 1024
)

func TestCollectorCloudEventsSplitsAtEventCountLimit(t *testing.T) {
	webhook := newWebhookFixture()
	defer webhook.close()
	harness := startLimitHarness(t, webhook.server.URL)

	for sequence := 0; sequence < cloudEventsMaxEvents+1; sequence++ {
		harness.appendEvent(10_000 + sequence)
	}
	waitFor(t, "501 CloudEvents delivered through count-limited requests", func() bool {
		return webhook.eventCount() == cloudEventsMaxEvents+1
	})

	observations := webhook.requestObservations()
	if len(observations) != 2 {
		t.Fatalf("accepted requests=%d, want 2", len(observations))
	}
	counts := []int{observations[0].EventCount, observations[1].EventCount}
	sort.Ints(counts)
	if counts[0] != 1 || counts[1] != cloudEventsMaxEvents {
		t.Fatalf("request event counts=%v, want [1 500]", counts)
	}
	assertCloudEventsRequestLimits(t, observations, cloudEventsMaxEvents+1)
}

func TestCollectorCloudEventsSplitsAtEncodedRequestByteLimit(t *testing.T) {
	webhook := newWebhookFixture()
	defer webhook.close()
	harness := startLimitHarness(t, webhook.server.URL)

	const eventCount = 7
	for sequence := 0; sequence < eventCount; sequence++ {
		harness.appendRaw(sizedOCSFEvent(t, 20_000+sequence, strings.Repeat("x", 700*1024)))
	}
	waitFor(t, "large CloudEvents delivered through byte-limited requests", func() bool {
		return webhook.eventCount() == eventCount
	})

	observations := webhook.requestObservations()
	if len(observations) < 2 {
		t.Fatalf("accepted requests=%d, want at least 2 for a payload larger than 4 MiB", len(observations))
	}
	assertCloudEventsRequestLimits(t, observations, eventCount)
}

func TestCollectorOversizedCloudEventSkipsWebhookAndRemainsInRecovery(t *testing.T) {
	webhook := newWebhookFixture()
	defer webhook.close()
	harness := startLimitHarness(t, webhook.server.URL)

	const sequence = 30_000
	harness.appendRaw(sizedOCSFEvent(t, sequence, strings.Repeat("x", 1100*1024)))
	originalUID := []byte(fmt.Sprintf("integration-sized-%d", sequence))
	waitFor(t, "oversized event in independent recovery output", func() bool {
		encoded, err := os.ReadFile(harness.recoveryPath)
		return err == nil && bytes.Contains(encoded, originalUID)
	})

	time.Sleep(500 * time.Millisecond)
	if webhook.requestCount() != 0 || webhook.eventCount() != 0 {
		t.Fatalf("oversized event reached webhook: requests=%d events=%d", webhook.requestCount(), webhook.eventCount())
	}
}

func startLimitHarness(t *testing.T, webhookURL string) *exporterHarness {
	t.Helper()
	return startExporterHarnessWithOptions(t, webhookURL, exporterHarnessOptions{
		queueSize:        2000,
		sendBatchSize:    1000,
		batchTimeout:     time.Second,
		retryMaxInterval: 200 * time.Millisecond,
	})
}

func sizedOCSFEvent(t *testing.T, sequence int, message string) string {
	t.Helper()
	event := map[string]any{
		"activity_id":  1,
		"category_uid": 4,
		"class_uid":    4001,
		"message":      message,
		"metadata": map[string]any{
			"version":            "1.8.0",
			"original_event_uid": fmt.Sprintf("integration-sized-%d", sequence),
		},
		"severity_id": 4,
		"time":        time.Now().UnixMilli() + int64(sequence),
		"type_uid":    400101,
		"unmapped": map[string]any{
			"sandbox_id": "sandbox-integration",
		},
	}
	encoded, err := json.Marshal(event)
	if err != nil {
		t.Fatal(err)
	}
	return string(encoded)
}

func assertCloudEventsRequestLimits(
	t *testing.T,
	observations []webhookRequestObservation,
	expectedEvents int,
) {
	t.Helper()
	total := 0
	ids := make(map[string]struct{}, expectedEvents)
	for index, observation := range observations {
		if observation.EventCount == 0 || observation.EventCount > cloudEventsMaxEvents {
			t.Fatalf("request %d event count=%d, want 1..500", index, observation.EventCount)
		}
		if observation.EncodedBytes > cloudEventsMaxRequestBytes {
			t.Fatalf("request %d encoded bytes=%d, exceeds 4 MiB", index, observation.EncodedBytes)
		}
		if observation.MaxEventBytes > cloudEventsMaxEventBytes {
			t.Fatalf("request %d max event bytes=%d, exceeds 1 MiB", index, observation.MaxEventBytes)
		}
		total += observation.EventCount
		for _, id := range observation.EventIDs {
			if _, duplicate := ids[id]; duplicate {
				t.Fatalf("duplicate event ID %q across accepted requests", id)
			}
			ids[id] = struct{}{}
		}
	}
	if total != expectedEvents || len(ids) != expectedEvents {
		t.Fatalf("accepted events=%d unique IDs=%d, want %d", total, len(ids), expectedEvents)
	}
}
