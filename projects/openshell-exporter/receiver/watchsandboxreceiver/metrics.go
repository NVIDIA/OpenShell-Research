// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package watchsandboxreceiver

import (
	"context"
	"sync/atomic"
	"time"

	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/metric"
	"go.opentelemetry.io/otel/metric/noop"
)

const receiverMeterName = "github.com/NVIDIA-dev/OpenShell-exporter/watchsandbox"

const watchMetricProducer = "watchsandboxreceiver"

const (
	policyConfiguredMetricName       = "openshell.exporter.policy.reconciliation.configured"
	policyLastSuccessMetricName      = "openshell.exporter.policy.reconciliation.last_success_unixtime"
	policyAgeMetricName              = "openshell.exporter.policy.reconciliation.age_seconds"
	policyQueueUtilizationMetricName = "openshell.exporter.policy.reconciliation.queue_utilization"
	policyConsistencyGapsMetricName  = "openshell.exporter.policy.reconciliation.consistency_gaps"
)

type watchMetrics struct {
	discoveryFailures      metric.Int64Counter
	reconnects             metric.Int64Counter
	gapWarnings            metric.Int64Counter
	backpressure           metric.Int64Counter
	sourceEnabled          metric.Int64Gauge
	lastSuccess            metric.Int64Gauge
	policyConfigured       metric.Int64Gauge
	policyLastSuccess      metric.Int64Gauge
	policyAge              metric.Int64Gauge
	policyQueueUtilization metric.Float64Gauge
	policyReconciliations  metric.Int64Counter
	policyFailures         metric.Int64Counter
	policyGapWarnings      metric.Int64Counter
	policyConsistencyGaps  metric.Int64Counter
	policyConfiguredAt     atomic.Int64
	policyLastSuccessAt    atomic.Int64
	now                    func() time.Time
}

func newWatchMetrics(provider metric.MeterProvider) *watchMetrics {
	if provider == nil {
		provider = noop.NewMeterProvider()
	}
	meter := provider.Meter(receiverMeterName)
	discoveryFailures, _ := meter.Int64Counter("openshell.exporter.discovery.failures", metric.WithUnit("{failure}"))
	reconnects, _ := meter.Int64Counter("openshell.exporter.stream.reconnects", metric.WithUnit("{reconnect}"))
	gapWarnings, _ := meter.Int64Counter("openshell.exporter.stream.gap_warnings", metric.WithUnit("{warning}"))
	backpressure, _ := meter.Int64Counter("openshell.exporter.stream.backpressure_retries", metric.WithUnit("{retry}"))
	sourceEnabled, _ := meter.Int64Gauge("openshell.exporter.source.enabled", metric.WithDescription("Configured source profile capability; one means enabled."), metric.WithUnit("1"))
	lastSuccess, _ := meter.Int64Gauge("openshell.exporter.source.last_success_unixtime", metric.WithDescription("Unix time of the last normalized record by bounded source kind."), metric.WithUnit("s"))
	policyConfigured, _ := meter.Int64Gauge(policyConfiguredMetricName, metric.WithDescription("One when read-only policy reconciliation is configured."), metric.WithUnit("1"))
	policyLastSuccess, _ := meter.Int64Gauge(policyLastSuccessMetricName, metric.WithDescription("Unix time of the last successful complete policy reconciliation."), metric.WithUnit("s"))
	policyAge, _ := meter.Int64Gauge(policyAgeMetricName, metric.WithDescription("Seconds since policy reconciliation was configured or last succeeded."), metric.WithUnit("s"))
	policyQueueUtilization, _ := meter.Float64Gauge(policyQueueUtilizationMetricName, metric.WithDescription("Current bounded policy reconciliation work-queue utilization from zero to one."), metric.WithUnit("1"))
	policyReconciliations, _ := meter.Int64Counter("openshell.exporter.policy.reconciliations", metric.WithUnit("{reconciliation}"))
	policyFailures, _ := meter.Int64Counter("openshell.exporter.policy.reconciliation_failures", metric.WithUnit("{failure}"))
	policyGapWarnings, _ := meter.Int64Counter("openshell.exporter.policy.gap_warnings", metric.WithUnit("{warning}"))
	policyConsistencyGaps, _ := meter.Int64Counter(policyConsistencyGapsMetricName, metric.WithDescription("Policy notification and gateway snapshot version differences by bounded state."), metric.WithUnit("{gap}"))
	return &watchMetrics{
		discoveryFailures:      discoveryFailures,
		reconnects:             reconnects,
		gapWarnings:            gapWarnings,
		backpressure:           backpressure,
		sourceEnabled:          sourceEnabled,
		lastSuccess:            lastSuccess,
		policyConfigured:       policyConfigured,
		policyLastSuccess:      policyLastSuccess,
		policyAge:              policyAge,
		policyQueueUtilization: policyQueueUtilization,
		policyReconciliations:  policyReconciliations,
		policyFailures:         policyFailures,
		policyGapWarnings:      policyGapWarnings,
		policyConsistencyGaps:  policyConsistencyGaps,
		now:                    time.Now,
	}
}

func (m *watchMetrics) enabled(ctx context.Context) {
	if m.sourceEnabled != nil {
		m.sourceEnabled.Record(ctx, 1, metric.WithAttributes(
			attribute.String("source", "watchsandbox"),
			attribute.String("durability", "non_resumable"),
			attribute.String("producer", watchMetricProducer),
		))
	}
}

func (m *watchMetrics) discoveryFailed(ctx context.Context) {
	if m.discoveryFailures != nil {
		m.discoveryFailures.Add(ctx, 1)
	}
}

func (m *watchMetrics) reconnect(ctx context.Context, reason string) {
	if m.reconnects != nil {
		m.reconnects.Add(ctx, 1, metric.WithAttributes(attribute.String("reason", reason)))
	}
}

func (m *watchMetrics) gap(ctx context.Context, reason string) {
	if m.gapWarnings != nil {
		m.gapWarnings.Add(ctx, 1, metric.WithAttributes(attribute.String("reason", reason)))
	}
}

func (m *watchMetrics) backpressured(ctx context.Context) {
	if m.backpressure != nil {
		m.backpressure.Add(ctx, 1)
	}
}

func (m *watchMetrics) succeeded(ctx context.Context) {
	if m.lastSuccess != nil {
		m.lastSuccess.Record(ctx, time.Now().Unix(), metric.WithAttributes(
			attribute.String("source", "watchsandbox"),
			attribute.String("producer", watchMetricProducer),
		))
	}
}

func (m *watchMetrics) policyEnabled(ctx context.Context) {
	if m.sourceEnabled != nil {
		m.sourceEnabled.Record(ctx, 1, metric.WithAttributes(
			attribute.String("source", "policy_reconciliation"),
			attribute.String("durability", "checkpointed_api_snapshot"),
			attribute.String("producer", watchMetricProducer),
		))
	}
	now := m.now().Unix()
	m.policyConfiguredAt.Store(now)
	if m.policyConfigured != nil {
		m.policyConfigured.Record(ctx, 1)
	}
	m.reconciliationState(ctx, 0, 1)
}

func (m *watchMetrics) policyDisabled(ctx context.Context) {
	if m.policyConfigured != nil {
		m.policyConfigured.Record(ctx, 0)
	}
}

func (m *watchMetrics) reconciliationSucceeded(ctx context.Context) {
	if m.policyReconciliations != nil {
		m.policyReconciliations.Add(ctx, 1)
	}
	now := m.now().Unix()
	m.policyLastSuccessAt.Store(now)
	if m.lastSuccess != nil {
		m.lastSuccess.Record(ctx, now, metric.WithAttributes(
			attribute.String("source", "policy_reconciliation"),
			attribute.String("producer", watchMetricProducer),
		))
	}
	if m.policyLastSuccess != nil {
		m.policyLastSuccess.Record(ctx, now)
	}
	m.recordReconciliationAge(ctx, now)
}

func (m *watchMetrics) reconciliationFailed(ctx context.Context, reason string) {
	if m.policyFailures != nil {
		m.policyFailures.Add(ctx, 1, metric.WithAttributes(attribute.String("reason", boundedPolicyFailureReason(reason))))
	}
	m.recordReconciliationAge(ctx, m.now().Unix())
}

func (m *watchMetrics) reconciliationGap(ctx context.Context, reason string) {
	if m.policyGapWarnings != nil {
		m.policyGapWarnings.Add(ctx, 1, metric.WithAttributes(attribute.String("reason", boundedPolicyGapReason(reason))))
	}
}

func (m *watchMetrics) reconciliationConsistencyGap(ctx context.Context, state string) {
	if m.policyConsistencyGaps != nil {
		m.policyConsistencyGaps.Add(ctx, 1, metric.WithAttributes(attribute.String("state", boundedPolicyConsistencyState(state))))
	}
}

func (m *watchMetrics) reconciliationState(ctx context.Context, queueLength, queueCapacity int) {
	if queueCapacity > 0 && m.policyQueueUtilization != nil {
		m.policyQueueUtilization.Record(ctx, float64(queueLength)/float64(queueCapacity))
	}
	m.recordReconciliationAge(ctx, m.now().Unix())
}

func (m *watchMetrics) recordReconciliationAge(ctx context.Context, now int64) {
	base := m.policyLastSuccessAt.Load()
	if base == 0 {
		base = m.policyConfiguredAt.Load()
	}
	if base == 0 || m.policyAge == nil {
		return
	}
	m.policyAge.Record(ctx, max(now-base, 0))
}

func boundedPolicyFailureReason(reason string) string {
	switch reason {
	case "state_read", "state_write", "conversion_failed", "delivery_failed",
		policyRevisionLimitExceeded, policyRevisionPaginationDrift, policyRevisionPaginationRead,
		"Canceled", "Unknown", "InvalidArgument", "DeadlineExceeded", "NotFound",
		"AlreadyExists", "PermissionDenied", "ResourceExhausted", "FailedPrecondition",
		"Aborted", "OutOfRange", "Unimplemented", "Internal", "Unavailable",
		"DataLoss", "Unauthenticated":
		return reason
	default:
		return "other"
	}
}

func boundedPolicyGapReason(reason string) string {
	switch reason {
	case "queue_full", "snapshot_reset", policyRevisionLimitExceeded,
		policyRevisionPaginationDrift, policyRevisionPaginationRead:
		return reason
	default:
		return "other"
	}
}

func boundedPolicyConsistencyState(state string) string {
	switch state {
	case "gateway_ahead", "notification_ahead":
		return state
	default:
		return "other"
	}
}
