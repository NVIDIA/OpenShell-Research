// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package integration

import (
	"net/http"
	"os"
	"testing"
)

func TestWebhookOutageRecoversFromPersistentQueue(t *testing.T) {
	webhook := newWebhookFixture()
	defer webhook.close()
	webhook.setStatus(http.StatusServiceUnavailable)
	harness := startExporterHarness(t, webhook.server.URL, 100)
	harness.appendEvent(10)
	if webhook.eventCount() != 0 {
		t.Fatal("outage unexpectedly acknowledged evidence")
	}
	webhook.setStatus(http.StatusAccepted)
	waitFor(t, "queued webhook retry", func() bool {
		return webhook.eventCount() == 1
	})
}

func TestPermanentWebhookRejectionStillWritesRecovery(t *testing.T) {
	webhook := newWebhookFixture()
	defer webhook.close()
	webhook.setStatus(http.StatusBadRequest)
	harness := startExporterHarness(t, webhook.server.URL, 100)
	harness.appendEvent(11)
	waitFor(t, "independent recovery output", func() bool {
		info, err := os.Stat(harness.recoveryPath)
		return err == nil && info.Size() > 0
	})
	if webhook.eventCount() != 0 {
		t.Fatal("permanently rejected event was acknowledged")
	}
}

func TestPersistentWebhookQueueSurvivesExporterRestartDuringOutage(t *testing.T) {
	webhook := newWebhookFixture()
	defer webhook.close()
	webhook.setStatus(http.StatusServiceUnavailable)
	harness := startExporterHarness(t, webhook.server.URL, 100)
	harness.appendEvent(12)

	waitFor(t, "retryable rejection and independent recovery", func() bool {
		info, err := os.Stat(harness.recoveryPath)
		return webhook.requestCount() > 0 && err == nil && info.Size() > 0
	})
	harness.stop()
	if err := os.Remove(harness.logPath); err != nil {
		t.Fatal(err)
	}

	webhook.setStatus(http.StatusAccepted)
	harness.start()
	waitFor(t, "persistent queue delivery after exporter restart", func() bool {
		return webhook.eventCount() == 1
	})
	if webhook.deliveryCount() != 1 {
		t.Fatalf("persistent queue delivered %d records, want 1", webhook.deliveryCount())
	}
}
