// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package integration

import (
	"os"
	"path/filepath"
	"testing"
)

func TestFileRotationAndTruncationKeepDistinctEvidence(t *testing.T) {
	webhook := newWebhookFixture()
	defer webhook.close()
	harness := startExporterHarness(t, webhook.server.URL, 100)

	harness.appendEvent(1)
	waitFor(t, "first file event", func() bool {
		return webhook.eventCount() == 1
	})
	if !webhook.allValidationStatus("valid") {
		t.Fatal("filelog-decoded OCSF event was not structurally valid")
	}
	rotated := filepath.Join(harness.root, "openshell-ocsf.1.log")
	if err := os.Rename(harness.logPath, rotated); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(harness.logPath, nil, 0o600); err != nil {
		t.Fatal(err)
	}
	harness.appendEvent(2)
	waitFor(t, "rotated and new file events", func() bool {
		return webhook.eventCount() == 2
	})

	if err := os.Truncate(harness.logPath, 0); err != nil {
		t.Fatal(err)
	}
	harness.appendEvent(3)
	waitFor(t, "truncated file reread", func() bool {
		return webhook.eventCount() == 3
	})
}
