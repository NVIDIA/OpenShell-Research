// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package openshellprocessor

import (
	"context"
	"sort"
	"strings"
	"testing"

	"go.opentelemetry.io/otel/attribute"
	sdkmetric "go.opentelemetry.io/otel/sdk/metric"
	"go.opentelemetry.io/otel/sdk/metric/metricdata"
)

func TestSourceCapabilityMetricsDescribeEveryBoundedLane(t *testing.T) {
	t.Parallel()
	reader := sdkmetric.NewManualReader()
	provider := sdkmetric.NewMeterProvider(sdkmetric.WithReader(reader))
	metrics, err := newProcessorMetrics(provider)
	if err != nil {
		t.Fatal(err)
	}
	capabilities := sourceCapabilitySummaries(supportedSourceProfiles)
	metrics.capabilitiesConfigured(context.Background(), supportedSourceProfiles, capabilities)

	collected := collectInt64Metrics(t, reader)
	wantSources := []string{
		"driver_otlp",
		"gateway_otlp",
		"kubernetes_context",
		"ocsf_files",
		"operational_files",
		"policy_reconciliation",
		"relay_files",
		"relay_otlp",
		"watch_events",
		"watchsandbox",
	}
	for _, name := range []string{
		"openshell.exporter.source.configured",
		"openshell.exporter.source.observed",
		"openshell.exporter.source.health_class",
		"openshell.exporter.source.capability_info",
	} {
		points := collected[name]
		if got := metricSources(points); !equalStrings(got, wantSources) {
			t.Fatalf("metric %q sources=%v, want %v", name, got, wantSources)
		}
	}

	assertMetricPointValue(t, collected["openshell.exporter.source.configured"], "watch_events", 0)
	assertMetricPointValue(t, collected["openshell.exporter.source.observed"], "watch_events", 0)
	assertMetricPointValue(t, collected["openshell.exporter.source.health_class"], "watch_events", sourceHealthClassUnavailable)
	for _, source := range wantSources {
		if source == "watch_events" {
			continue
		}
		assertMetricPointValue(t, collected["openshell.exporter.source.configured"], source, 1)
		assertMetricPointValue(t, collected["openshell.exporter.source.observed"], source, 0)
		assertMetricPointValue(t, collected["openshell.exporter.source.health_class"], source, sourceHealthClassUnobserved)
	}

	for _, point := range collected["openshell.exporter.source.capability_info"] {
		keys := make([]string, 0, point.Attributes.Len())
		for _, item := range point.Attributes.ToSlice() {
			keys = append(keys, string(item.Key))
		}
		sort.Strings(keys)
		if !equalStrings(keys, []string{"durability", "limitation", "source"}) {
			t.Fatalf("capability labels=%v, want bounded metadata labels", keys)
		}
		for _, key := range []string{"durability", "limitation", "source"} {
			value, ok := point.Attributes.Value(attribute.Key(key))
			if !ok || value.AsString() == "" {
				t.Fatalf("capability label %q is missing", key)
			}
		}
	}
}

func TestSourceObservationMetricsDistinguishHealthyAndGap(t *testing.T) {
	t.Parallel()
	reader := sdkmetric.NewManualReader()
	provider := sdkmetric.NewMeterProvider(sdkmetric.WithReader(reader))
	metrics, err := newProcessorMetrics(provider)
	if err != nil {
		t.Fatal(err)
	}
	metrics.observeSource(context.Background(), "ocsf.file")
	metrics.observeSource(context.Background(), "stream.warning")
	metrics.observeSource(context.Background(), "customer-controlled-unknown")

	collected := collectInt64Metrics(t, reader)
	observed := collected["openshell.exporter.source.observed"]
	health := collected["openshell.exporter.source.health_class"]
	if got := metricSources(observed); !equalStrings(got, []string{"ocsf_files", "watchsandbox"}) {
		t.Fatalf("observed sources=%v", got)
	}
	assertMetricPointValue(t, observed, "ocsf_files", 1)
	assertMetricPointValue(t, observed, "watchsandbox", 1)
	assertMetricPointValue(t, health, "ocsf_files", sourceHealthClassHealthy)
	assertMetricPointValue(t, health, "watchsandbox", sourceHealthClassGapReported)
}

func TestProcessorMetricSourceLabelsFailClosed(t *testing.T) {
	t.Parallel()
	reader := sdkmetric.NewManualReader()
	provider := sdkmetric.NewMeterProvider(sdkmetric.WithReader(reader))
	metrics, err := newProcessorMetrics(provider)
	if err != nil {
		t.Fatal(err)
	}

	const untrustedKind = "https://customer.example/sandboxes/secret-session?token=must-not-leak"
	metrics.record(context.Background(), untrustedKind, validationResult{Status: validationInvalid}, 1, "default")

	collected := collectInt64Metrics(t, reader)
	for _, name := range []string{
		"openshell.exporter.records.received",
		"openshell.exporter.records.invalid",
		"openshell.exporter.redactions",
		"openshell.exporter.source.last_success_unixtime",
	} {
		points := collected[name]
		if len(points) != 1 {
			t.Fatalf("metric %q points=%d, want 1", name, len(points))
		}
		source, ok := points[0].Attributes.Value(attribute.Key("source"))
		if !ok || source.AsString() != "unsupported" {
			t.Fatalf("metric %q source=%q, want unsupported", name, source.AsString())
		}
		if strings.Contains(points[0].Attributes.Encoded(attribute.DefaultEncoder()), untrustedKind) {
			t.Fatalf("metric %q leaked untrusted source label", name)
		}
	}
}

func collectInt64Metrics(t *testing.T, reader *sdkmetric.ManualReader) map[string][]metricdata.DataPoint[int64] {
	t.Helper()
	var resourceMetrics metricdata.ResourceMetrics
	if err := reader.Collect(context.Background(), &resourceMetrics); err != nil {
		t.Fatal(err)
	}
	result := map[string][]metricdata.DataPoint[int64]{}
	for _, scope := range resourceMetrics.ScopeMetrics {
		for _, metric := range scope.Metrics {
			switch data := metric.Data.(type) {
			case metricdata.Gauge[int64]:
				result[metric.Name] = append(result[metric.Name], data.DataPoints...)
			case metricdata.Sum[int64]:
				result[metric.Name] = append(result[metric.Name], data.DataPoints...)
			}
		}
	}
	return result
}

func metricSources(points []metricdata.DataPoint[int64]) []string {
	result := make([]string, 0, len(points))
	for _, point := range points {
		value, ok := point.Attributes.Value(attribute.Key("source"))
		if ok {
			result = append(result, value.AsString())
		}
	}
	sort.Strings(result)
	return result
}

func assertMetricPointValue(t *testing.T, points []metricdata.DataPoint[int64], source string, want int64) {
	t.Helper()
	for _, point := range points {
		value, ok := point.Attributes.Value(attribute.Key("source"))
		if ok && value.AsString() == source {
			if point.Value != want {
				t.Fatalf("source %q value=%d, want %d", source, point.Value, want)
			}
			return
		}
	}
	t.Fatalf("source %q not found", source)
}

func equalStrings(got, want []string) bool {
	if len(got) != len(want) {
		return false
	}
	for index := range got {
		if got[index] != want[index] {
			return false
		}
	}
	return true
}
