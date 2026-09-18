// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package watchsandboxreceiver

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
	"time"

	"go.opentelemetry.io/collector/component"
	"go.opentelemetry.io/collector/receiver"
	"go.opentelemetry.io/otel/attribute"
	sdkmetric "go.opentelemetry.io/otel/sdk/metric"
	"go.opentelemetry.io/otel/sdk/metric/metricdata"
	"go.uber.org/zap"
	"go.uber.org/zap/zapcore"
	"go.uber.org/zap/zaptest/observer"
)

func TestPolicyReconciliationMetricsAreBoundedAndOperational(t *testing.T) {
	t.Parallel()
	ctx := context.Background()
	reader := sdkmetric.NewManualReader()
	provider := sdkmetric.NewMeterProvider(sdkmetric.WithReader(reader))
	t.Cleanup(func() { _ = provider.Shutdown(ctx) })
	metrics := newWatchMetrics(provider)
	now := time.Unix(1_000, 0)
	metrics.now = func() time.Time { return now }

	metrics.policyEnabled(ctx)
	now = time.Unix(1_010, 0)
	metrics.reconciliationState(ctx, 3, 4)
	metrics.reconciliationFailed(ctx, "Bearer must-not-become-a-label")
	metrics.reconciliationGap(ctx, "https://customer.example/private")
	metrics.reconciliationConsistencyGap(ctx, "gateway_ahead")
	now = time.Unix(1_020, 0)
	metrics.reconciliationSucceeded(ctx)
	now = time.Unix(1_050, 0)
	metrics.reconciliationState(ctx, 3, 4)

	collected := collectMetrics(t, reader)
	assertIntGauge(t, collected, policyConfiguredMetricName, 1)
	assertIntGauge(t, collected, policyLastSuccessMetricName, 1_020)
	assertIntGauge(t, collected, policyAgeMetricName, 30)
	assertFloatGauge(t, collected, policyQueueUtilizationMetricName, 0.75)
	assertIntSum(t, collected, policyConsistencyGapsMetricName, 1, "state", "gateway_ahead")
	assertIntSum(t, collected, "openshell.exporter.policy.reconciliation_failures", 1, "reason", "other")
	assertIntSum(t, collected, "openshell.exporter.policy.gap_warnings", 1, "reason", "other")
	assertIntGaugeLabel(t, collected, "openshell.exporter.source.last_success_unixtime", "producer", watchMetricProducer)

	forbidden := map[string]struct{}{
		"gateway": {}, "workspace": {}, "sandbox": {}, "draft": {}, "chunk": {},
		"request": {}, "trace": {}, "url": {}, "customer": {},
	}
	for _, current := range collected {
		if !strings.HasPrefix(current.Name, "openshell.exporter.policy") {
			continue
		}
		for _, attrs := range metricAttributes(current) {
			for _, item := range attrs.ToSlice() {
				if _, blocked := forbidden[string(item.Key)]; blocked {
					t.Fatalf("metric %q contains forbidden label %q", current.Name, item.Key)
				}
				if strings.Contains(item.Value.String(), "must-not") || strings.Contains(item.Value.String(), "customer.example") {
					t.Fatalf("metric %q contains unbounded or secret label value %q", current.Name, item.Value.String())
				}
			}
		}
	}
}

func TestPolicyReconciliationRecoveryLogsExcludeCredentials(t *testing.T) {
	t.Parallel()
	core, observed := observer.New(zapcore.DebugLevel)
	r := newWatchReceiver(createDefaultConfig().(*Config), receiver.Settings{
		TelemetrySettings: component.TelemetrySettings{Logger: zap.New(core)},
	}, nil)
	r.recordReconciliationFailure(context.Background(), "Bearer must-never-appear")
	r.recordReconciliationFailure(context.Background(), "https://customer.example/private")
	r.recordReconciliationSuccess(context.Background())
	entries := observed.AllUntimed()
	if len(entries) != 2 || entries[0].Message != "policy reconciliation degraded" || entries[1].Message != "policy reconciliation recovered" {
		t.Fatalf("unexpected recovery log transitions: %#v", entries)
	}
	encoded, err := json.Marshal(entries)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(encoded), "must-never") || strings.Contains(string(encoded), "customer.example") {
		t.Fatalf("credential or URL reached structured logs: %s", encoded)
	}
	if entries[0].ContextMap()["reason"] != "other" {
		t.Fatalf("degraded reason=%#v, want bounded other", entries[0].ContextMap()["reason"])
	}
}

func TestPolicyPaginationMetricReasonsAreBoundedAndDistinct(t *testing.T) {
	t.Parallel()
	for _, reason := range []string{
		policyRevisionLimitExceeded,
		policyRevisionPaginationDrift,
		policyRevisionPaginationRead,
	} {
		if got := boundedPolicyFailureReason(reason); got != reason {
			t.Fatalf("failure reason %q became %q", reason, got)
		}
		if got := boundedPolicyGapReason(reason); got != reason {
			t.Fatalf("gap reason %q became %q", reason, got)
		}
	}
	if got := boundedPolicyGapReason("sandbox-secret"); got != "other" {
		t.Fatalf("unbounded gap reason became %q", got)
	}
}

func collectMetrics(t *testing.T, reader *sdkmetric.ManualReader) []metricdata.Metrics {
	t.Helper()
	var resource metricdata.ResourceMetrics
	if err := reader.Collect(context.Background(), &resource); err != nil {
		t.Fatal(err)
	}
	var result []metricdata.Metrics
	for _, scope := range resource.ScopeMetrics {
		result = append(result, scope.Metrics...)
	}
	return result
}

func findMetric(t *testing.T, metrics []metricdata.Metrics, name string) metricdata.Metrics {
	t.Helper()
	for _, current := range metrics {
		if current.Name == name {
			return current
		}
	}
	t.Fatalf("metric %q was not collected", name)
	return metricdata.Metrics{}
}

func assertIntGauge(t *testing.T, metrics []metricdata.Metrics, name string, want int64) {
	t.Helper()
	gauge, ok := findMetric(t, metrics, name).Data.(metricdata.Gauge[int64])
	if !ok || len(gauge.DataPoints) != 1 || gauge.DataPoints[0].Value != want {
		t.Fatalf("gauge %q=%#v, want %d", name, gauge.DataPoints, want)
	}
}

func assertIntGaugeLabel(t *testing.T, metrics []metricdata.Metrics, name, key, want string) {
	t.Helper()
	gauge, ok := findMetric(t, metrics, name).Data.(metricdata.Gauge[int64])
	if !ok || len(gauge.DataPoints) != 1 {
		t.Fatalf("gauge %q=%#v, want one point", name, gauge.DataPoints)
	}
	got, exists := gauge.DataPoints[0].Attributes.Value(attribute.Key(key))
	if !exists || got.AsString() != want {
		t.Fatalf("gauge %q label %s=%v, want %q", name, key, got, want)
	}
}

func assertFloatGauge(t *testing.T, metrics []metricdata.Metrics, name string, want float64) {
	t.Helper()
	gauge, ok := findMetric(t, metrics, name).Data.(metricdata.Gauge[float64])
	if !ok || len(gauge.DataPoints) != 1 || gauge.DataPoints[0].Value != want {
		t.Fatalf("gauge %q=%#v, want %f", name, gauge.DataPoints, want)
	}
}

func assertIntSum(t *testing.T, metrics []metricdata.Metrics, name string, want int64, key, value string) {
	t.Helper()
	sum, ok := findMetric(t, metrics, name).Data.(metricdata.Sum[int64])
	if !ok || len(sum.DataPoints) != 1 || sum.DataPoints[0].Value != want {
		t.Fatalf("sum %q=%#v, want %d", name, sum.DataPoints, want)
	}
	if got, exists := sum.DataPoints[0].Attributes.Value(attribute.Key(key)); !exists || got.AsString() != value {
		t.Fatalf("sum %q label %s=%v, want %q", name, key, got, value)
	}
}

func metricAttributes(current metricdata.Metrics) []attribute.Set {
	switch data := current.Data.(type) {
	case metricdata.Gauge[int64]:
		return pointAttributes(data.DataPoints)
	case metricdata.Gauge[float64]:
		return pointAttributes(data.DataPoints)
	case metricdata.Sum[int64]:
		return pointAttributes(data.DataPoints)
	default:
		return nil
	}
}

func pointAttributes[N int64 | float64](points []metricdata.DataPoint[N]) []attribute.Set {
	result := make([]attribute.Set, 0, len(points))
	for _, point := range points {
		result = append(result, point.Attributes)
	}
	return result
}
