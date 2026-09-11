// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package cloudeventsexporter

import (
	"context"
	"strconv"
	"time"

	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/metric"
	"go.opentelemetry.io/otel/metric/noop"
)

const exporterMeterName = "github.com/NVIDIA-dev/OpenShell-exporter/cloudevents"

type exporterMetrics struct {
	oversized          metric.Int64Counter
	permanentResponses metric.Int64Counter
	retryableResponses metric.Int64Counter
	deliveredBatches   metric.Int64Counter
	deliveredEvents    metric.Int64Counter
	destinationLatency metric.Float64Histogram
	lastSuccess        metric.Int64Gauge
}

func newExporterMetrics(provider metric.MeterProvider) *exporterMetrics {
	if provider == nil {
		provider = noop.NewMeterProvider()
	}
	meter := provider.Meter(exporterMeterName)
	oversized, _ := meter.Int64Counter("openshell.exporter.events.oversized", metric.WithUnit("{event}"))
	permanent, _ := meter.Int64Counter("openshell.exporter.delivery.permanent_rejections", metric.WithUnit("{response}"))
	retryable, _ := meter.Int64Counter("openshell.exporter.delivery.retryable_failures", metric.WithUnit("{response}"))
	batches, _ := meter.Int64Counter("openshell.exporter.delivery.batches", metric.WithUnit("{batch}"))
	events, _ := meter.Int64Counter("openshell.exporter.delivery.events", metric.WithUnit("{event}"))
	latency, _ := meter.Float64Histogram("openshell.exporter.destination.latency", metric.WithUnit("ms"))
	lastSuccess, _ := meter.Int64Gauge("openshell.exporter.destination.last_success_unixtime", metric.WithUnit("s"))
	return &exporterMetrics{
		oversized:          oversized,
		permanentResponses: permanent,
		retryableResponses: retryable,
		deliveredBatches:   batches,
		deliveredEvents:    events,
		destinationLatency: latency,
		lastSuccess:        lastSuccess,
	}
}

func (m *exporterMetrics) recordOversized(ctx context.Context, count int) {
	if m.oversized != nil {
		m.oversized.Add(ctx, int64(count), metric.WithAttributes(
			attribute.String("destination", "cloudevents"),
		))
	}
}

func (m *exporterMetrics) recordResponse(
	ctx context.Context,
	status int,
	eventCount int,
	elapsed time.Duration,
) {
	attrs := metric.WithAttributes(attribute.String("status_code", strconv.Itoa(status)))
	if status >= 200 && status < 300 {
		if m.deliveredBatches != nil {
			m.deliveredBatches.Add(ctx, 1, attrs)
			m.deliveredEvents.Add(ctx, int64(eventCount), attrs)
			m.lastSuccess.Record(ctx, time.Now().Unix())
		}
	} else if retryableStatus(status) {
		if m.retryableResponses != nil {
			m.retryableResponses.Add(ctx, 1, attrs)
		}
	} else if m.permanentResponses != nil {
		m.permanentResponses.Add(ctx, 1, attrs)
	}
	if m.destinationLatency != nil {
		m.destinationLatency.Record(ctx, float64(elapsed.Microseconds())/1000)
	}
}

func (m *exporterMetrics) recordNetworkFailure(ctx context.Context, elapsed time.Duration) {
	if m.retryableResponses != nil {
		m.retryableResponses.Add(ctx, 1, metric.WithAttributes(
			attribute.String("status_code", "network"),
		))
	}
	if m.destinationLatency != nil {
		m.destinationLatency.Record(ctx, float64(elapsed.Microseconds())/1000)
	}
}
