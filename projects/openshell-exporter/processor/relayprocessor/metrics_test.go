// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package relayprocessor

import (
	"context"
	"testing"

	"go.opentelemetry.io/otel/attribute"
	sdkmetric "go.opentelemetry.io/otel/sdk/metric"
	"go.opentelemetry.io/otel/sdk/metric/metricdata"
)

func TestRelayMetricSourceLabelsFailClosed(t *testing.T) {
	t.Parallel()
	reader := sdkmetric.NewManualReader()
	provider := sdkmetric.NewMeterProvider(sdkmetric.WithReader(reader))
	metrics, err := newRelayMetrics(provider)
	if err != nil {
		t.Fatal(err)
	}
	metrics.observeSource(context.Background(), "https://customer.example/secret-session?token=must-not-leak")

	var resourceMetrics metricdata.ResourceMetrics
	if err := reader.Collect(context.Background(), &resourceMetrics); err != nil {
		t.Fatal(err)
	}
	found := 0
	for _, scope := range resourceMetrics.ScopeMetrics {
		for _, metric := range scope.Metrics {
			if metric.Name != "openshell.exporter.source.observed" &&
				metric.Name != "openshell.exporter.source.health_class" &&
				metric.Name != "openshell.exporter.source.last_success_unixtime" {
				continue
			}
			data, ok := metric.Data.(metricdata.Gauge[int64])
			if !ok || len(data.DataPoints) != 1 {
				t.Fatalf("metric %q data=%T points=%d", metric.Name, metric.Data, len(data.DataPoints))
			}
			source, ok := data.DataPoints[0].Attributes.Value(attribute.Key("source"))
			if !ok || source.AsString() != "other_otlp" {
				t.Fatalf("metric %q source=%q, want other_otlp", metric.Name, source.AsString())
			}
			producer, ok := data.DataPoints[0].Attributes.Value(attribute.Key("producer"))
			if !ok || producer.AsString() != relayMetricProducer {
				t.Fatalf("metric %q producer=%q, want %q", metric.Name, producer.AsString(), relayMetricProducer)
			}
			found++
		}
	}
	if found != 3 {
		t.Fatalf("found %d source metrics, want 3", found)
	}
}

func TestRelayMetricSourceVocabularyIsBounded(t *testing.T) {
	t.Parallel()
	for _, source := range []string{
		"relay_otlp", "gateway_otlp", "driver_otlp", "native_otlp_unclassified", "other_otlp",
	} {
		if got := boundedOTLPMetricSource(source); got != source {
			t.Fatalf("source %q bounded to %q", source, got)
		}
	}
	for _, source := range []string{"", "secret-session", "https://customer.example/token"} {
		if got := boundedOTLPMetricSource(source); got != "other_otlp" {
			t.Fatalf("untrusted source %q bounded to %q", source, got)
		}
	}
}
