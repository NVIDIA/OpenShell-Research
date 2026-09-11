// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package integration

import (
	"io"
	"net"
	"net/http"
	"sort"
	"strconv"
	"strings"
	"testing"
	"time"
)

func TestBuiltCollectorPrometheusMetricContract(t *testing.T) {
	port := availableLoopbackPort(t)
	fixture := newWebhookFixture()
	defer fixture.close()
	harness := startExporterHarnessWithOptions(t, fixture.server.URL, exporterHarnessOptions{
		queueSize:        16,
		sendBatchSize:    1,
		batchTimeout:     50 * time.Millisecond,
		retryMaxInterval: 100 * time.Millisecond,
		metricsPort:      port,
	})
	harness.appendEvent(1)
	waitFor(t, "one delivered event", func() bool { return fixture.eventCount() == 1 })

	client := &http.Client{Timeout: time.Second}
	var scrape string
	waitFor(t, "custom Prometheus metrics", func() bool {
		request, err := http.NewRequestWithContext(t.Context(), http.MethodGet, "http://127.0.0.1:"+strconv.Itoa(port)+"/metrics", nil)
		if err != nil {
			t.Fatal(err)
		}
		response, err := client.Do(request)
		if err != nil {
			return false
		}
		defer func() { _ = response.Body.Close() }()
		if response.StatusCode != http.StatusOK {
			return false
		}
		encoded, err := io.ReadAll(io.LimitReader(response.Body, 4<<20))
		if err != nil {
			return false
		}
		scrape = string(encoded)
		return strings.Contains(scrape, "openshell_exporter_delivery_events") &&
			strings.Contains(scrape, "openshell_exporter_storage_available")
	})

	metricNames := prometheusMetricNames(scrape, "openshell_exporter_")
	for _, expected := range []string{
		"openshell_exporter_delivery_events",
		"openshell_exporter_destination_last_success_unixtime",
		"openshell_exporter_destination_latency_bucket",
		"openshell_exporter_records_received",
		"openshell_exporter_source_configured",
		"openshell_exporter_source_health_class",
		"openshell_exporter_source_last_success_unixtime",
		"openshell_exporter_storage_available",
		"openshell_exporter_storage_configured",
		"openshell_exporter_storage_free_bytes",
		"openshell_exporter_storage_free_inodes",
		"openshell_exporter_storage_scan_truncated",
		"openshell_exporter_storage_state_age_seconds",
		"openshell_exporter_storage_state_present",
	} {
		if !containsString(metricNames, expected) {
			t.Errorf("Prometheus scrape is missing %q; emitted custom names: %v", expected, metricNames)
		}
	}
	for _, forbidden := range []string{
		"openshell.exporter.",
		"otelcol_openshell_exporter_",
		harness.root,
	} {
		if strings.Contains(scrape, forbidden) {
			t.Errorf("Prometheus scrape exposes forbidden text %q", forbidden)
		}
	}
	for _, line := range strings.Split(scrape, "\n") {
		if !strings.HasPrefix(line, "openshell_exporter_storage_") || strings.HasPrefix(line, "#") {
			continue
		}
		labelsStart := strings.IndexByte(line, '{')
		labelsEnd := strings.IndexByte(line, '}')
		if labelsStart < 0 || labelsEnd < labelsStart || !strings.HasPrefix(line[labelsStart+1:labelsEnd], `storage="`) {
			t.Errorf("storage metric lacks its bounded storage label: %q", line)
			continue
		}
		if strings.Contains(line[labelsStart+1:labelsEnd], ",") {
			t.Errorf("storage metric contains an unexpected label: %q", line)
		}
	}
}

func availableLoopbackPort(t *testing.T) int {
	t.Helper()
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	port := listener.Addr().(*net.TCPAddr).Port
	if err := listener.Close(); err != nil {
		t.Fatal(err)
	}
	return port
}

func prometheusMetricNames(scrape, prefix string) []string {
	unique := make(map[string]struct{})
	for _, line := range strings.Split(scrape, "\n") {
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		name := strings.Fields(line)[0]
		if index := strings.IndexByte(name, '{'); index >= 0 {
			name = name[:index]
		}
		if strings.HasPrefix(name, prefix) {
			unique[name] = struct{}{}
		}
	}
	names := make([]string, 0, len(unique))
	for name := range unique {
		names = append(names, name)
	}
	sort.Strings(names)
	return names
}

func containsString(values []string, wanted string) bool {
	for _, value := range values {
		if value == wanted {
			return true
		}
	}
	return false
}
