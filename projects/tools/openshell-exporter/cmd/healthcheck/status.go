// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package main

import (
	"bufio"
	"context"
	"fmt"
	"io"
	"math"
	"net/http"
	"net/url"
	"sort"
	"strconv"
	"strings"
	"time"
)

type statusMetric struct {
	name      string
	label     string
	timestamp bool
}

var statusSections = []struct {
	name    string
	metrics []statusMetric
}{
	{"Sources (observation is not destination delivery)", []statusMetric{
		{"openshell_exporter_source_configured", "Configured profile", false},
		{"openshell_exporter_source_enabled", "Enabled receiver", false},
		{"openshell_exporter_source_observed", "Observed profile", false},
		{"openshell_exporter_source_last_success_unixtime", "Last observation (UTC)", true},
	}},
	{"CloudEvents delivery (HTTP acknowledgement only)", []statusMetric{
		{"openshell_exporter_delivery_events", "Acknowledged events", false},
		{"openshell_exporter_destination_last_success_unixtime", "Last acknowledgement (UTC)", true},
		{"openshell_exporter_delivery_retryable_failures", "Retryable failures", false},
		{"openshell_exporter_delivery_permanent_rejections", "Permanently rejected responses", false},
		{"openshell_exporter_events_oversized", "Oversized events", false},
	}},
	{"Collector queues (units follow configured queue sizer; excludes in-flight sends)", []statusMetric{
		{"otelcol_exporter_queue_size", "Backlog", false},
		{"otelcol_exporter_queue_capacity", "Capacity", false},
		{"otelcol_exporter_enqueue_failed_log_records", "Enqueue failures", false},
		{"otelcol_exporter_send_failed_log_records", "Send failures", false},
	}},
	{"WatchSandbox (non-resumable)", []statusMetric{
		{"openshell_exporter_stream_reconnects", "Reconnects", false},
		{"openshell_exporter_stream_gap_warnings", "Gap risks", false},
		{"openshell_exporter_stream_backpressure_active", "Waiting to retry downstream", false},
		{"openshell_exporter_stream_unacknowledged_records", "Unacknowledged source records", false},
	}},
	{"Storage (availability is a probe, not proof of durable delivery)", []statusMetric{
		{"openshell_exporter_storage_configured", "Configured", false},
		{"openshell_exporter_storage_available", "Available (1=yes, 0=no)", false},
		{"openshell_exporter_storage_free_bytes", "Free bytes", false},
		{"openshell_exporter_storage_free_inodes", "Free inodes", false},
		{"openshell_exporter_storage_scan_truncated", "State scan truncated (1=yes)", false},
	}},
}

// deliveryStatus is deliberately observational: neither zero backlog nor an HTTP
// acknowledgement proves that every source event reached durable destination storage.
func deliveryStatus(ctx context.Context, output io.Writer, endpoint string) error {
	u, err := url.Parse(endpoint)
	if err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" || u.User != nil {
		return fmt.Errorf("status endpoint must be an HTTP(S) metrics URL without embedded credentials")
	}
	ctx, cancel := context.WithTimeout(ctx, 3*time.Second)
	defer cancel()
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return err
	}
	client := &http.Client{CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	response, err := client.Do(request)
	if err != nil {
		return fmt.Errorf("read metrics: %w", err)
	}
	defer func() { _ = response.Body.Close() }()
	if response.StatusCode != http.StatusOK {
		return fmt.Errorf("metrics endpoint returned HTTP %d", response.StatusCode)
	}
	const maxBytes = 4 << 20
	body, err := io.ReadAll(io.LimitReader(response.Body, maxBytes+1))
	if err != nil {
		return fmt.Errorf("read metrics body: %w", err)
	}
	if len(body) > maxBytes {
		return fmt.Errorf("metrics response exceeds %d bytes", maxBytes)
	}
	return renderStatus(output, string(body))
}

func renderStatus(output io.Writer, body string) error {
	wanted := map[string]statusMetric{}
	for _, section := range statusSections {
		for _, m := range section.metrics {
			wanted[m.name] = m
		}
	}
	values := map[string][]string{}
	scanner := bufio.NewScanner(strings.NewReader(body))
	scanner.Buffer(make([]byte, 4096), 1<<20)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		end := strings.IndexAny(line, "{ \t")
		if end < 0 {
			continue
		}
		name := line[:end]
		// Accommodate Prometheus exporters that append the conventional counter suffix.
		name = strings.TrimSuffix(name, "_total")
		m, ok := wanted[name]
		if !ok {
			continue
		}
		rest := strings.TrimSpace(line[end:])
		labels := ""
		if strings.HasPrefix(rest, "{") {
			end = strings.LastIndexByte(rest, '}')
			if end < 0 {
				return fmt.Errorf("malformed labels for %s", name)
			}
			labels, rest = rest[:end+1], rest[end+1:]
		}
		fields := strings.Fields(rest)
		if len(fields) < 1 || len(fields) > 2 {
			return fmt.Errorf("malformed sample for %s", name)
		}
		value, err := strconv.ParseFloat(fields[0], 64)
		if err != nil {
			return fmt.Errorf("invalid value for %s", name)
		}
		formatted := fields[0]
		if math.IsNaN(value) || math.IsInf(value, 0) || value < 0 {
			formatted = "unavailable"
		} else if m.timestamp {
			if value == 0 || value >= float64(math.MaxInt64) {
				formatted = "not recorded"
			} else {
				formatted = time.Unix(int64(value), 0).UTC().Format(time.RFC3339)
			}
		}
		values[name] = append(values[name], fmt.Sprintf("  %s %s: %s\n", m.label, labels, formatted))
	}
	if err := scanner.Err(); err != nil {
		return fmt.Errorf("parse metrics: %w", err)
	}
	var result strings.Builder
	result.WriteString("Delivery snapshot. Counters are cumulative since process start; compare successive snapshots.\nMissing metrics are unavailable, never assumed zero. This is not a delivery health verdict.\n")
	for _, section := range statusSections {
		fmt.Fprintf(&result, "\n%s\n", section.name)
		for _, m := range section.metrics {
			rows := values[m.name]
			if len(rows) == 0 {
				fmt.Fprintf(&result, "  %s: unavailable\n", m.label)
				continue
			}
			sort.Strings(rows)
			for _, row := range rows {
				result.WriteString(row)
			}
		}
	}
	_, err := io.WriteString(output, result.String())
	return err
}
