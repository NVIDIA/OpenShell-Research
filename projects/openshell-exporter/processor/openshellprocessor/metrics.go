// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package openshellprocessor

import (
	"context"
	"time"

	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/metric"
	"go.opentelemetry.io/otel/metric/noop"
)

const processorMeterName = "github.com/NVIDIA-dev/OpenShell-exporter/processor"

type processorMetrics struct {
	sourceEnabled        metric.Int64Gauge
	sourceConfigured     metric.Int64Gauge
	sourceObserved       metric.Int64Gauge
	sourceHealthClass    metric.Int64Gauge
	sourceCapabilityInfo metric.Int64Gauge
	received             metric.Int64Counter
	invalid              metric.Int64Counter
	redactions           metric.Int64Counter
	lastSuccess          metric.Int64Gauge
}

func newProcessorMetrics(provider metric.MeterProvider) (*processorMetrics, error) {
	if provider == nil {
		provider = noop.NewMeterProvider()
	}
	meter := provider.Meter(processorMeterName)
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
		metric.WithDescription("Observed state for each bounded evidence lane; one means at least one record was observed."),
		metric.WithUnit("1"),
	)
	if err != nil {
		return nil, err
	}
	sourceHealthClass, err := meter.Int64Gauge(
		"openshell.exporter.source.health_class",
		metric.WithDescription("Bounded evidence-lane health: 0 disabled, 1 unobserved, 2 healthy, 3 gap reported, 4 unavailable."),
		metric.WithUnit("1"),
	)
	if err != nil {
		return nil, err
	}
	sourceCapabilityInfo, err := meter.Int64Gauge(
		"openshell.exporter.source.capability_info",
		metric.WithDescription("Static bounded durability and limitation metadata for each evidence lane."),
		metric.WithUnit("1"),
	)
	if err != nil {
		return nil, err
	}
	received, err := meter.Int64Counter(
		"openshell.exporter.records.received",
		metric.WithDescription("Source records normalized by the OpenShell processor."),
		metric.WithUnit("{record}"),
	)
	if err != nil {
		return nil, err
	}
	invalid, err := meter.Int64Counter(
		"openshell.exporter.records.invalid",
		metric.WithDescription("Source records retained with structural validation errors."),
		metric.WithUnit("{record}"),
	)
	if err != nil {
		return nil, err
	}
	redactions, err := meter.Int64Counter(
		"openshell.exporter.redactions",
		metric.WithDescription("Secret values replaced by the configured redaction profile."),
		metric.WithUnit("{value}"),
	)
	if err != nil {
		return nil, err
	}
	lastSuccess, err := meter.Int64Gauge(
		"openshell.exporter.source.last_success_unixtime",
		metric.WithDescription("Unix time of the last normalized record by bounded source kind."),
		metric.WithUnit("s"),
	)
	if err != nil {
		return nil, err
	}
	return &processorMetrics{
		sourceEnabled:    sourceEnabled,
		sourceConfigured: sourceConfigured,
		sourceObserved:   sourceObserved, sourceHealthClass: sourceHealthClass,
		sourceCapabilityInfo: sourceCapabilityInfo,
		received:             received, invalid: invalid, redactions: redactions, lastSuccess: lastSuccess,
	}, nil
}

func (m *processorMetrics) capabilitiesConfigured(ctx context.Context, enabledProfiles []string, capabilities []sourceCapabilitySummary) {
	enabled := make(map[string]struct{}, len(enabledProfiles))
	for _, profile := range enabledProfiles {
		enabled[profile] = struct{}{}
	}
	for _, profile := range supportedSourceProfiles {
		value := int64(0)
		if _, ok := enabled[profile]; ok {
			value = 1
		}
		m.sourceEnabled.Record(ctx, value, metric.WithAttributes(
			attribute.String("source", profile),
		))
	}
	for _, capability := range capabilities {
		configured := int64(0)
		if capability.Configured {
			configured = 1
		}
		m.sourceConfigured.Record(ctx, configured, metric.WithAttributes(
			attribute.String("source", capability.Source),
		))
		m.sourceObserved.Record(ctx, 0, metric.WithAttributes(
			attribute.String("source", capability.Source),
		))
		m.sourceHealthClass.Record(ctx, sourceCapabilityHealthClass(capability.Health), metric.WithAttributes(
			attribute.String("source", capability.Source),
		))
		m.sourceCapabilityInfo.Record(ctx, 1, metric.WithAttributes(
			attribute.String("source", capability.Source),
			attribute.String("durability", capability.Durability),
			attribute.String("limitation", capability.LimitationCode),
		))
	}
}

func (m *processorMetrics) observeSource(ctx context.Context, kind string) {
	lane := sourceCapabilityLane(kind)
	if lane == "" {
		return
	}
	health := sourceHealthHealthy
	if sourceKindReportsGap(kind) {
		health = sourceHealthGapReported
	}
	attrs := metric.WithAttributes(attribute.String("source", lane))
	m.sourceObserved.Record(ctx, 1, attrs)
	m.sourceHealthClass.Record(ctx, sourceCapabilityHealthClass(health), attrs)
}

func (m *processorMetrics) record(
	ctx context.Context,
	kind string,
	validation validationResult,
	redactionCount int,
	profileID string,
) {
	metricSource := boundedMetricSource(kind)
	attrs := metric.WithAttributes(
		attribute.String("source", metricSource),
		attribute.String("validation_status", validation.Status),
	)
	m.received.Add(ctx, 1, attrs)
	lastSuccessSource := sourceCapabilityLane(kind)
	if lastSuccessSource == "" {
		lastSuccessSource = metricSource
	}
	m.lastSuccess.Record(ctx, time.Now().Unix(), metric.WithAttributes(
		attribute.String("source", lastSuccessSource),
	))
	if validation.Status == validationInvalid {
		m.invalid.Add(ctx, 1, metric.WithAttributes(attribute.String("source", metricSource)))
	}
	if redactionCount > 0 {
		m.redactions.Add(ctx, int64(redactionCount), metric.WithAttributes(
			attribute.String("source", metricSource),
			attribute.String("profile", profileID),
		))
	}
}
