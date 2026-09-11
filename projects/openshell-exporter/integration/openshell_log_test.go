// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package integration

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestSandboxOperationalFileRestartRotationAndRedaction(t *testing.T) {
	webhook := newWebhookFixture()
	defer webhook.close()
	harness := startExporterHarnessWithOptions(t, webhook.server.URL, exporterHarnessOptions{
		queueSize:             100,
		sendBatchSize:         1,
		batchTimeout:          100 * time.Millisecond,
		retryMaxInterval:      200 * time.Millisecond,
		sandboxOperationalLog: true,
	})

	harness.appendRaw("INFO authorization=Bearer operational-secret first")
	waitFor(t, "first sandbox operational event", func() bool {
		return webhook.eventCount() == 1
	})
	if !webhook.allValidationStatus("not_applicable") {
		t.Fatal("sandbox operational record did not remain non-OCSF evidence")
	}
	if !webhook.allAcquisition("sandbox.file_log", "checkpointed_file", "openshell.log") {
		t.Fatal("sandbox operational acquisition metadata is incomplete")
	}
	waitFor(t, "redacted sandbox operational recovery", func() bool {
		encoded, err := os.ReadFile(harness.recoveryPath)
		return err == nil && strings.Contains(string(encoded), "[REDACTED]") &&
			!strings.Contains(string(encoded), "operational-secret")
	})

	harness.stop()
	harness.appendRaw("WARN checkpoint restart second")
	harness.start()
	waitFor(t, "sandbox operational checkpoint restart", func() bool {
		return webhook.eventCount() == 2
	})

	rotated := filepath.Join(harness.root, "openshell.2026-08-20.log")
	if err := os.Rename(harness.logPath, rotated); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(harness.logPath, nil, 0o600); err != nil {
		t.Fatal(err)
	}
	harness.appendRaw("INFO daily rotation third")
	waitFor(t, "sandbox operational daily rotation", func() bool {
		return webhook.eventCount() == 3
	})
}
