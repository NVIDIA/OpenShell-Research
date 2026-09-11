// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package elastic_test

import (
	"encoding/json"
	"os"
	"strings"
	"testing"
)

func TestLocalShowcaseMonitoringContract(t *testing.T) {
	t.Parallel()

	compose := readContractFile(t, "..", "..", "examples", "demo", "real-gateway", "compose.elastic.yaml")
	requireContractStrings(t, compose,
		"docker.io/prom/prometheus:v3.13.1@sha256:3c42b892",
		"docker.io/grafana/grafana:13.1.3@sha256:ab5cb380",
		"127.0.0.1:9090:9090",
		"127.0.0.1:3000:3000",
		"prometheus-data:/prometheus",
		"grafana-data:/var/lib/grafana",
		"read_only: true",
		"no-new-privileges:true",
	)

	prometheus := readContractFile(t, "..", "..", "examples", "demo", "real-gateway", "monitoring", "prometheus.yaml")
	requireContractStrings(t, prometheus,
		"job_name: openshell-event-exporter",
		"targets: [\"exporter:8888\"]",
		"scrape_interval: 5s",
		"PrometheusText0.0.4",
	)

	dashboardPath := "../../examples/demo/real-gateway/monitoring/grafana/dashboards/openshell-exporter-operations.json"
	encoded, err := os.ReadFile(dashboardPath)
	if err != nil {
		t.Fatal(err)
	}
	var dashboard struct {
		UID    string `json:"uid"`
		Title  string `json:"title"`
		Panels []struct {
			Title       string `json:"title"`
			Description string `json:"description"`
			Type        string `json:"type"`
		} `json:"panels"`
	}
	if err := json.Unmarshal(encoded, &dashboard); err != nil {
		t.Fatal(err)
	}
	if dashboard.UID != "openshell-exporter-operations" ||
		dashboard.Title != "OpenShell Exporter — Operations Command Center" ||
		len(dashboard.Panels) < 12 {
		t.Fatalf("incomplete operations dashboard: %+v", dashboard)
	}
	requiredPanels := map[string]bool{
		"Healthy evidence lanes":             false,
		"Evidence in selected window":        false,
		"Delivery success · 5m":              false,
		"Stalest source activity":            false,
		"Evidence intake rate by source":     false,
		"Persistent queue utilization":       false,
		"Source capability health":           false,
		"Destination delivery rate":          false,
		"Destination latency percentiles":    false,
		"Stream continuity and backpressure": false,
		"Validation and redaction controls":  false,
		"NeMo Relay span flow":               false,
		"NeMo Relay privacy and correlation": false,
		"Exporter process resources":         false,
		"Durable storage capacity":           false,
		"Durable storage health":             false,
		"Durable state freshness":            false,
		"Policy snapshot freshness":          false,
		"Policy work queue":                  false,
		"Policy reconciliation outcomes":     false,
	}
	for _, panel := range dashboard.Panels {
		if _, ok := requiredPanels[panel.Title]; ok {
			requiredPanels[panel.Title] = true
			if panel.Description == "" {
				t.Errorf("operations panel %q has no human-readable description", panel.Title)
			}
		}
	}
	for panel, found := range requiredPanels {
		if !found {
			t.Errorf("operations dashboard is missing %q", panel)
		}
	}

	requireContractStrings(t, string(encoded),
		"openshell_exporter_source_configured",
		"openshell_exporter_source_health_class",
		`openshell\\\\.exporter\\\\.source\\\\.(configured|enabled)`,
		`openshell\\\\.exporter\\\\.source\\\\.health_class`,
		"== bool 2",
		"Waiting for first record",
		"Gap reported",
		"WatchEvents remains explicitly unavailable",
		`source=~\"$source\"`,
		`exporter=~\"$exporter\"`,
		`storage=~\"$storage\"`,
		"otelcol_receiver_accepted_spans",
		`receiver=~\"otlp.*\"`,
		"openshell_exporter_storage_free_bytes",
		"openshell_exporter_storage_free_inodes",
		"openshell_exporter_storage_available",
		"openshell_exporter_storage_state_age_seconds",
		"openshell_exporter_storage_scan_truncated",
		`openshell\\\\.exporter\\\\.storage\\\\.available`,
		"openshell_exporter_policy_reconciliation_age_seconds",
		"openshell_exporter_policy_reconciliation_queue_utilization",
		"openshell_exporter_policy_reconciliation_failures",
		"openshell_exporter_policy_reconciliation_consistency_gaps",
		"openshell_exporter_records_received|openshell",
		"openshell_exporter_relay_spans|openshell",
		"label_values({__name__=~",
		"label_values(otelcol_exporter_queue_capacity, exporter)",
		`label_values({__name__=~\"openshell_exporter_storage_available|openshell\\\\.exporter\\\\.storage\\\\.available\"}, storage)`,
		"OpenShell SOC - Analyst Triage and Investigation",
	)
	for _, forbidden := range []string{
		"otelcol_openshell_exporter_",
	} {
		if strings.Contains(string(encoded), forbidden) {
			t.Errorf("operations dashboard contains an impossible custom metric name %q", forbidden)
		}
	}
	headless := readContractFile(t, "..", "..", "examples", "demo", "real-gateway", "exporter.elastic-headless.yaml")
	requireContractStrings(t, headless, "cloudevents/elastic_logstash", "otlphttp/elastic_otlp", "file/recovery_events", "file/recovery_traces")
	headlessCompose := readContractFile(t, "..", "..", "examples", "demo", "real-gateway", "compose.elastic-headless.yaml")
	if !strings.Contains(headlessCompose, "depends_on: !override") {
		t.Fatal("headless topology does not remove the conformance receiver dependency")
	}
	if strings.Contains(headless, "cloudevents/dashboard") || strings.Contains(headless, "otlphttp/dashboard") {
		t.Fatal("headless Elastic presentation still exports to the development receiver")
	}

	baseCompose := readContractFile(t, "..", "..", "examples", "demo", "real-gateway", "compose.yaml")
	requireContractStrings(t, baseCompose, "conformance-receiver:", "conformance-receiver.demo.internal")
	if strings.Contains(baseCompose, "  webapp:\n") {
		t.Fatal("development receiver still has a product-facing webapp service name")
	}
}
