// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package integration

import (
	"bufio"
	"os"
	"strings"
	"testing"
)

func TestNodeRestartQualificationRequiresRealRebootAndExternalEvidence(t *testing.T) {
	encoded, err := os.ReadFile("qualification/qualify-node-restart.sh")
	if err != nil {
		t.Fatal(err)
	}
	script := string(encoded)
	for _, required := range []string{
		"/proc/sys/kernel/random/boot_id",
		`[ "$boot_after" != "$boot_before" ]`,
		`[ "$restart_policy" = unless-stopped ]`,
		`number_is_zero "$queue_baseline"`,
		`number_greater_than "$queue_before_reboot" 0`,
		`number_greater_than "$retry_before_reboot" "$retry_baseline"`,
		`"$recovery_lines_before_reboot" -gt "$recovery_line_baseline"`,
		`string_attr("cloudevents.source")`,
		`string_attr("cloudevents.id")`,
		`wait_for_automatic_container_recovery`,
		`[ "$image_after" = "$image_before" ]`,
		`number_is_zero "$queue_after"`,
		`NODE_RESTART_RECEIVER_EVIDENCE_FILE`,
		`.deduplicated_rows == 1`,
		`.delivery_attempts >= 1`,
		`"real Linux node restart with persistent CloudEvents queue"`,
		`NODE_RESTART_EVIDENCE_DIR must have mode 0700`,
		`openshell_exporter_delivery_retryable_failures`,
		`openshell_exporter_delivery_events`,
		`openshell_exporter_destination_last_success_unixtime`,
	} {
		if !strings.Contains(script, required) {
			t.Fatalf("node-restart qualification is missing %q", required)
		}
	}
	for _, forbidden := range []string{"otelcol_openshell_exporter_", "openshell_exporter_delivery_events_total"} {
		if strings.Contains(script, forbidden) {
			t.Fatalf("node-restart qualification contains an impossible custom metric name %q", forbidden)
		}
	}
}

func TestNodeRestartQualificationNeverMutatesHostLifecycleOrPersistence(t *testing.T) {
	encoded, err := os.ReadFile("qualification/qualify-node-restart.sh")
	if err != nil {
		t.Fatal(err)
	}
	script := string(encoded)
	for _, forbidden := range []string{
		"docker start",
		"docker restart",
		"docker stop",
		"docker rm",
		"$(compose up",
		"$(compose start",
		"$(compose restart",
		"$(compose stop",
		"$(compose down",
		"rm -rf",
	} {
		if strings.Contains(script, forbidden) {
			t.Fatalf("qualification must not contain destructive action %q", forbidden)
		}
	}
	scanner := bufio.NewScanner(strings.NewReader(script))
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		fields := strings.Fields(line)
		if len(fields) == 0 || strings.HasPrefix(line, "#") {
			continue
		}
		switch fields[0] {
		case "reboot", "shutdown", "poweroff", "halt":
			t.Fatalf("qualification must not execute host lifecycle command: %s", line)
		case "compose":
			if line != `compose ps -q exporter 2>/dev/null || true)` {
				t.Fatalf("qualification may only inspect Compose state, got: %s", line)
			}
		}
		if strings.Contains(line, "rm -rf") {
			t.Fatalf("qualification must never recursively delete persistence: %s", line)
		}
	}
	if err := scanner.Err(); err != nil {
		t.Fatal(err)
	}
}
