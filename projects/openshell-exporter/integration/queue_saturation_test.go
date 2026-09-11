// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package integration

import (
	"net/http"
	"testing"
)

func TestQueueSaturationBackpressuresThenDrainsWithoutLoss(t *testing.T) {
	webhook := newWebhookFixture()
	defer webhook.close()
	webhook.setStatus(http.StatusServiceUnavailable)
	harness := startExporterHarness(t, webhook.server.URL, 2)
	for sequence := 100; sequence < 112; sequence++ {
		harness.appendEvent(sequence)
	}
	webhook.setStatus(http.StatusAccepted)
	waitFor(t, "checkpointed input and saturated queue drain", func() bool {
		return webhook.eventCount() == 12
	})
}
