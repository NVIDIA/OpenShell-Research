// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package alerts

import (
	"bytes"
	"os"
	"strings"
	"testing"

	"go.yaml.in/yaml/v3"
)

type prometheusRuleFile struct {
	Groups []struct {
		Rules []prometheusRule `yaml:"rules"`
	} `yaml:"groups"`
}

type prometheusRule struct {
	Record      string            `yaml:"record"`
	Alert       string            `yaml:"alert"`
	Expr        string            `yaml:"expr"`
	For         string            `yaml:"for"`
	Annotations map[string]string `yaml:"annotations"`
}

func TestPolicyReconciliationRecordingAndAlertRules(t *testing.T) {
	t.Parallel()
	rules := readPrometheusRules(t)

	recordings := map[string]string{
		"openshell_exporter:policy_reconciliation_age_seconds":       "max(openshell_exporter_policy_reconciliation_age_seconds)",
		"openshell_exporter:policy_reconciliation_failures_10m":      "sum(increase(openshell_exporter_policy_reconciliation_failures[10m]))",
		"openshell_exporter:policy_reconciliation_queue_utilization": "max(openshell_exporter_policy_reconciliation_queue_utilization)",
	}
	alerts := map[string]struct {
		expr    string
		forTime string
		runbook string
	}{
		"OpenShellExporterPolicyReconciliationStale": {
			expr:    "max(openshell_exporter_policy_reconciliation_configured) == 1 and openshell_exporter:policy_reconciliation_age_seconds > 300",
			forTime: "5m", runbook: "docs/operations.md#stale-policy-reconciliation",
		},
		"OpenShellExporterPolicyReconciliationFailing": {
			expr:    "openshell_exporter:policy_reconciliation_failures_10m > 2",
			forTime: "5m", runbook: "docs/operations.md#repeated-policy-reconciliation-failures",
		},
		"OpenShellExporterPolicyReconciliationQueueHigh": {
			expr:    "openshell_exporter:policy_reconciliation_queue_utilization > 0.8",
			forTime: "5m", runbook: "docs/operations.md#policy-reconciliation-queue-saturation",
		},
	}

	foundRecordings := make(map[string]bool, len(recordings))
	foundAlerts := make(map[string]bool, len(alerts))
	for _, rule := range rules {
		if expected, ok := recordings[rule.Record]; ok {
			if rule.Expr != expected {
				t.Fatalf("recording %q expression=%q, want %q", rule.Record, rule.Expr, expected)
			}
			foundRecordings[rule.Record] = true
		}
		if expected, ok := alerts[rule.Alert]; ok {
			if rule.Expr != expected.expr || rule.For != expected.forTime || rule.Annotations["runbook"] != expected.runbook {
				t.Fatalf("alert %q contract=%#v, want %#v", rule.Alert, rule, expected)
			}
			foundAlerts[rule.Alert] = true
		}
		for _, forbidden := range []string{"sandbox", "workspace", "gateway", "request", "trace", "customer", "url"} {
			if rule.Record != "" && strings.Contains(strings.ToLower(rule.Expr), forbidden) {
				t.Fatalf("recording %q contains high-cardinality identifier %q", rule.Record, forbidden)
			}
		}
	}
	for name := range recordings {
		if !foundRecordings[name] {
			t.Fatalf("recording rule %q is missing", name)
		}
	}
	for name := range alerts {
		if !foundAlerts[name] {
			t.Fatalf("alert rule %q is missing", name)
		}
	}

	runbook, err := os.ReadFile("../../docs/operations.md")
	if err != nil {
		t.Fatal(err)
	}
	for _, heading := range []string{"Stale policy reconciliation", "Repeated policy reconciliation failures", "Policy reconciliation queue saturation"} {
		if !bytes.Contains(runbook, []byte("### "+heading)) {
			t.Fatalf("runbook heading %q is missing", heading)
		}
	}
}

func TestDurableStorageAlertRules(t *testing.T) {
	t.Parallel()
	rules := readPrometheusRules(t)
	alerts := map[string]struct {
		expr    string
		forTime string
		runbook string
	}{
		"OpenShellExporterStorageUnavailable": {
			expr:    "openshell_exporter_storage_available == 0",
			forTime: "1m", runbook: "docs/operations.md#durable-storage-unavailable-or-low",
		},
		"OpenShellExporterStorageLowBytes": {
			expr:    "openshell_exporter_storage_available == 1 and openshell_exporter_storage_free_bytes < 1073741824",
			forTime: "5m", runbook: "docs/operations.md#durable-storage-unavailable-or-low",
		},
		"OpenShellExporterStorageLowInodes": {
			expr:    "openshell_exporter_storage_available == 1 and openshell_exporter_storage_free_inodes < 10000",
			forTime: "5m", runbook: "docs/operations.md#durable-storage-unavailable-or-low",
		},
		"OpenShellExporterCheckpointStateStale": {
			expr:    "openshell_exporter_storage_state_present{storage=\"checkpoints\"} == 1 and openshell_exporter_storage_state_age_seconds{storage=\"checkpoints\"} > 600 and on (instance, job) (time() - max by (instance, job) (openshell_exporter_source_last_success_unixtime{source=~\"ocsf_files|operational_files|relay_files\"}) < 300)",
			forTime: "5m", runbook: "docs/operations.md#stale-checkpoint-state",
		},
		"OpenShellExporterStorageStateScanTruncated": {
			expr:    "openshell_exporter_storage_scan_truncated == 1",
			forTime: "5m", runbook: "docs/operations.md#storage-state-scan-truncated",
		},
		"OpenShellExporterQueueEnqueueFailure": {
			expr:    "sum by (exporter) (increase(otelcol_exporter_enqueue_failed_log_records[5m])) > 0",
			forTime: "0m", runbook: "docs/operations.md#queue-enqueue-failure",
		},
	}

	found := make(map[string]bool, len(alerts))
	for _, rule := range rules {
		expected, ok := alerts[rule.Alert]
		if !ok {
			continue
		}
		if rule.Expr != expected.expr || rule.For != expected.forTime || rule.Annotations["runbook"] != expected.runbook {
			t.Fatalf("alert %q contract=%#v, want %#v", rule.Alert, rule, expected)
		}
		found[rule.Alert] = true
	}
	for name := range alerts {
		if !found[name] {
			t.Errorf("alert rule %q is missing", name)
		}
	}

	runbook, err := os.ReadFile("../../docs/operations.md")
	if err != nil {
		t.Fatal(err)
	}
	for _, heading := range []string{"Durable storage unavailable or low", "Stale checkpoint state", "Storage state scan truncated", "Queue enqueue failure"} {
		if !bytes.Contains(runbook, []byte("### "+heading)) {
			t.Errorf("runbook heading %q is missing", heading)
		}
	}
}

func readPrometheusRules(t *testing.T) []prometheusRule {
	t.Helper()
	encoded, err := os.ReadFile("exporter.yaml")
	if err != nil {
		t.Fatal(err)
	}
	for _, forbidden := range []string{"otelcol_openshell_exporter_", "openshell.exporter."} {
		if bytes.Contains(encoded, []byte(forbidden)) {
			t.Fatalf("Prometheus rules contain an impossible custom metric name %q", forbidden)
		}
	}
	var file prometheusRuleFile
	if err := yaml.Unmarshal(encoded, &file); err != nil {
		t.Fatal(err)
	}
	var rules []prometheusRule
	for _, group := range file.Groups {
		rules = append(rules, group.Rules...)
	}
	return rules
}
