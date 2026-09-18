// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package relayprocessor

import (
	"context"
	"time"

	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/metric"
	"go.opentelemetry.io/otel/metric/noop"
)

const relayMeterName = "github.com/NVIDIA-dev/OpenShell-exporter/relayprocessor"

const relayMetricProducer = "relayprocessor"

type relayMetrics struct {
	spans                metric.Int64Counter
	attributesRemoved    metric.Int64Counter
	sourceEnabled        metric.Int64Gauge
	sourceConfigured     metric.Int64Gauge
	sourceObserved       metric.Int64Gauge
	sourceHealthClass    metric.Int64Gauge
	sourceCapabilityInfo metric.Int64Gauge
	lastSuccess          metric.Int64Gauge
}

func newRelayMetrics(provider metric.MeterProvider) (*relayMetrics, error) {
	if provider == nil {
		provider = noop.NewMeterProvider()
	}
	meter := provider.Meter(relayMeterName)
	spans, err := meter.Int64Counter(
		"openshell.exporter.relay.spans",
		metric.WithDescription("Relay spans processed by bounded correlation status and privacy mode."),
		metric.WithUnit("{span}"),
	)
	if err != nil {
		return nil, err
	}
	attributesRemoved, err := meter.Int64Counter(
		"openshell.exporter.relay.attributes_removed",
		metric.WithDescription("Relay attributes removed by privacy filtering by bounded telemetry scope."),
		metric.WithUnit("{attribute}"),
	)
	if err != nil {
		return nil, err
	}
	sourceEnabled, err := meter.Int64Gauge(
		"openshell.exporter.source.enabled",
		metric.WithDescription("Configured source profile capability; one means enabled."),
		metric.WithUnit("1"),
	)
	if err != nil {
		return nil, err
	}
	sourceConfigured, err := meter.Int64Gauge(
		"openshell.exporter.source.configured",
		metric.WithDescription("Configured state for each bounded evidence lane; one means configured."),
		metric.WithUnit("1"),
	)
	if err != nil {
		return nil, err
	}
	sourceObserved, err := meter.Int64Gauge(
		"openshell.exporter.source.observed",
		metric.WithDescription("Observed state for each bounded OTLP evidence lane; one means at least one resource was observed."),
		metric.WithUnit("1"),
	)
	if err != nil {
		return nil, err
	}
	sourceCapabilityInfo, err := meter.Int64Gauge(
		"openshell.exporter.source.capability_info",
		metric.WithDescription("Static bounded evidence-lane capability metadata."),
		metric.WithUnit("1"),
	)
	if err != nil {
		return nil, err
	}
	sourceHealthClass, err := meter.Int64Gauge(
		"openshell.exporter.source.health_class",
		metric.WithDescription("Bounded evidence-lane health: 2 means healthy."),
		metric.WithUnit("1"),
	)
	if err != nil {
		return nil, err
	}
	lastSuccess, err := meter.Int64Gauge(
		"openshell.exporter.source.last_success_unixtime",
		metric.WithDescription("Unix time of the last processed resource by bounded OTLP source."),
		metric.WithUnit("s"),
	)
	if err != nil {
		return nil, err
	}
	return &relayMetrics{
		spans: spans, attributesRemoved: attributesRemoved,
		sourceEnabled: sourceEnabled, sourceConfigured: sourceConfigured,
		sourceObserved: sourceObserved, sourceHealthClass: sourceHealthClass,
		sourceCapabilityInfo: sourceCapabilityInfo, lastSuccess: lastSuccess,
	}, nil
}

func (m *relayMetrics) capabilitiesConfigured(ctx context.Context, capabilities []relaySourceCapability) {
	for _, capability := range capabilities {
		attrs := metric.WithAttributes(
			attribute.String("source", capability.Source),
			attribute.String("producer", relayMetricProducer),
		)
		m.sourceEnabled.Record(ctx, 1, metric.WithAttributes(
			attribute.String("source", capability.Profile),
			attribute.String("producer", relayMetricProducer),
		))
		m.sourceConfigured.Record(ctx, 1, attrs)
		m.sourceObserved.Record(ctx, 0, attrs)
		m.sourceHealthClass.Record(ctx, 1, attrs)
		m.sourceCapabilityInfo.Record(ctx, 1, metric.WithAttributes(
			attribute.String("source", capability.Source),
			attribute.String("durability", capability.Durability),
			attribute.String("limitation", capability.Limitation),
			attribute.String("producer", relayMetricProducer),
		))
	}
}

func (m *relayMetrics) recordSpan(ctx context.Context, status, privacyMode string) {
	m.spans.Add(ctx, 1, metric.WithAttributes(
		attribute.String("correlation_status", status),
		attribute.String("privacy_mode", privacyMode),
	))
}

func (m *relayMetrics) observeSource(ctx context.Context, source string) {
	attrs := metric.WithAttributes(
		attribute.String("source", boundedOTLPMetricSource(source)),
		attribute.String("producer", relayMetricProducer),
	)
	m.sourceObserved.Record(ctx, 1, attrs)
	m.sourceHealthClass.Record(ctx, 2, attrs)
	m.lastSuccess.Record(ctx, time.Now().Unix(), attrs)
}

func boundedOTLPMetricSource(source string) string {
	switch source {
	case "relay_otlp", "gateway_otlp", "driver_otlp", "native_otlp_unclassified", "other_otlp":
		return source
	default:
		return "other_otlp"
	}
}

func (m *relayMetrics) recordRemoved(ctx context.Context, scope string, count int) {
	if count == 0 {
		return
	}
	m.attributesRemoved.Add(ctx, int64(count), metric.WithAttributes(
		attribute.String("scope", scope),
	))
}
