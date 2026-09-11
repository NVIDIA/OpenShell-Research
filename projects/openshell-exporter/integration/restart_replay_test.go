// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package integration

import (
	"os"
	"strings"
	"testing"
)

func TestProcessRestartReusesCheckpointWithoutDurableFileLoss(t *testing.T) {
	webhook := newWebhookFixture()
	defer webhook.close()
	harness := startExporterHarness(t, webhook.server.URL, 100)

	harness.appendEvent(21)
	waitFor(t, "pre-restart file event", func() bool {
		return webhook.eventCount() == 1
	})
	harness.stop()
	harness.appendEvent(22)
	harness.start()
	waitFor(t, "post-restart checkpoint replay", func() bool {
		return webhook.eventCount() == 2
	})
	if webhook.deliveryCount() != 2 {
		t.Fatalf("checkpoint restart delivered %d records, want 2", webhook.deliveryCount())
	}
}

func TestMalformedFileRecordIsInvalidRedactedRecoveryEvidence(t *testing.T) {
	webhook := newWebhookFixture()
	defer webhook.close()
	harness := startExporterHarness(t, webhook.server.URL, 100)

	harness.appendRaw("authorization=Bearer abc.def.ghi")
	waitFor(t, "malformed retained event", func() bool {
		return webhook.eventCount() == 1
	})
	if !webhook.allValidationStatus("invalid") {
		t.Fatal("malformed record was not marked invalid")
	}
	waitFor(t, "malformed recovery evidence", func() bool {
		encoded, err := os.ReadFile(harness.recoveryPath)
		return err == nil && len(encoded) > 0 &&
			strings.Contains(string(encoded), "[REDACTED]") &&
			!strings.Contains(string(encoded), "abc.def.ghi")
	})
}
