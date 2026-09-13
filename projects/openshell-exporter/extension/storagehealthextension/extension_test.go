// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package storagehealthextension

import (
	"context"
	"os"
	"path/filepath"
	"sort"
	"syscall"
	"testing"
	"time"

	"go.opentelemetry.io/collector/component"
	"go.opentelemetry.io/collector/extension"
	"go.opentelemetry.io/otel/attribute"
	sdkmetric "go.opentelemetry.io/otel/sdk/metric"
	"go.opentelemetry.io/otel/sdk/metric/metricdata"
	"go.uber.org/zap"
)

func TestLifecycleIsIdempotent(t *testing.T) {
	t.Parallel()
	config := &Config{
		Interval: time.Second, MaxEntries: 1,
		Paths: map[string]string{"checkpoints": t.TempDir()},
	}
	instance, err := newStorageHealthExtension(config, extension.Settings{
		TelemetrySettings: component.TelemetrySettings{Logger: zap.NewNop()},
	})
	if err != nil {
		t.Fatal(err)
	}
	if err := instance.Start(context.Background(), nil); err != nil {
		t.Fatal(err)
	}
	if err := instance.Start(context.Background(), nil); err != nil {
		t.Fatal(err)
	}
	if err := instance.Shutdown(context.Background()); err != nil {
		t.Fatal(err)
	}
	if err := instance.Shutdown(context.Background()); err != nil {
		t.Fatal(err)
	}
}

func TestConfigValidationFailsClosed(t *testing.T) {
	t.Parallel()
	tests := []Config{
		{Interval: time.Second, MaxEntries: 1},
		{Interval: time.Millisecond, MaxEntries: 1, Paths: map[string]string{"checkpoints": "/tmp"}},
		{Interval: time.Second, MaxEntries: 0, Paths: map[string]string{"checkpoints": "/tmp"}},
		{Interval: time.Second, MaxEntries: 1, Paths: map[string]string{"customer-sandbox": "/tmp"}},
		{Interval: time.Second, MaxEntries: 1, Paths: map[string]string{"checkpoints": ""}},
	}
	for index := range tests {
		if err := tests[index].Validate(); err == nil {
			t.Fatalf("test %d unexpectedly passed", index)
		}
	}
	valid := Config{
		Interval: time.Second, MaxEntries: 10,
		Paths: map[string]string{"checkpoints": "/tmp", "recovery": "/tmp/recovery.json"},
	}
	if err := valid.Validate(); err != nil {
		t.Fatal(err)
	}
}

func TestStorageMetricsAreBoundedAndDoNotExposePaths(t *testing.T) {
	t.Parallel()
	root := t.TempDir()
	checkpointDir := filepath.Join(root, "customer-secret", "checkpoints")
	if err := os.MkdirAll(checkpointDir, 0o700); err != nil {
		t.Fatal(err)
	}
	stateFile := filepath.Join(checkpointDir, "state.db")
	if err := os.WriteFile(stateFile, []byte("state"), 0o600); err != nil {
		t.Fatal(err)
	}
	now := time.Now().UTC().Truncate(time.Second)
	if err := os.Chtimes(stateFile, now.Add(-2*time.Minute), now.Add(-2*time.Minute)); err != nil {
		t.Fatal(err)
	}

	reader := sdkmetric.NewManualReader()
	provider := sdkmetric.NewMeterProvider(sdkmetric.WithReader(reader))
	config := &Config{
		Interval: time.Hour, MaxEntries: 10,
		Paths: map[string]string{
			"checkpoints": checkpointDir,
			"recovery":    filepath.Join(root, "customer-secret", "recovery", "events.json"),
		},
	}
	instance, err := newStorageHealthExtension(config, extension.Settings{
		TelemetrySettings: component.TelemetrySettings{
			Logger: zap.NewNop(), MeterProvider: provider,
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	instance.now = func() time.Time { return now }
	instance.sample(context.Background())

	metrics := collectMetrics(t, reader)
	wantNames := []string{
		"openshell.exporter.storage.available",
		"openshell.exporter.storage.configured",
		"openshell.exporter.storage.free_bytes",
		"openshell.exporter.storage.free_inodes",
		"openshell.exporter.storage.scan_truncated",
		"openshell.exporter.storage.state_age_seconds",
		"openshell.exporter.storage.state_present",
	}
	gotNames := make([]string, 0, len(metrics))
	for name := range metrics {
		gotNames = append(gotNames, name)
	}
	sort.Strings(gotNames)
	if !equalStringSlices(gotNames, wantNames) {
		t.Fatalf("metric names=%v, want %v", gotNames, wantNames)
	}
	for name, points := range metrics {
		if len(points) != 2 {
			t.Fatalf("metric %q points=%d, want 2", name, len(points))
		}
		for _, point := range points {
			labels := point.attributes.ToSlice()
			if len(labels) != 1 || labels[0].Key != attribute.Key("storage") {
				t.Fatalf("metric %q labels=%v", name, labels)
			}
			if value := labels[0].Value.AsString(); value != "checkpoints" && value != "recovery" {
				t.Fatalf("metric %q exposed unbounded storage label %q", name, value)
			}
		}
	}
	assertMetricValue(t, metrics, "openshell.exporter.storage.available", "checkpoints", 1)
	assertMetricValue(t, metrics, "openshell.exporter.storage.available", "recovery", 1)
	assertMetricValue(t, metrics, "openshell.exporter.storage.state_present", "checkpoints", 1)
	assertMetricValue(t, metrics, "openshell.exporter.storage.state_present", "recovery", 0)
	assertMetricValue(t, metrics, "openshell.exporter.storage.state_age_seconds", "checkpoints", 120)
}

func TestStateScanIsBounded(t *testing.T) {
	t.Parallel()
	directory := t.TempDir()
	for index := 0; index < 3; index++ {
		if err := os.WriteFile(filepath.Join(directory, string(rune('a'+index))), []byte("x"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	_, present, truncated, err := newestState(directory, 2)
	if err != nil {
		t.Fatal(err)
	}
	if !present || !truncated {
		t.Fatalf("present=%t truncated=%t", present, truncated)
	}
}

func TestEmptyDurableDirectoryIsAvailableWithoutState(t *testing.T) {
	t.Parallel()
	sample, err := samplePath(t.TempDir(), 2, time.Now())
	if err != nil {
		t.Fatal(err)
	}
	if !sample.available || sample.statePresent || sample.truncated {
		t.Fatalf("sample=%+v, want available empty durable directory", sample)
	}
}

func TestSaturatingProduct(t *testing.T) {
	t.Parallel()
	if got := saturatingProduct(2, 3); got != 6 {
		t.Fatalf("saturatingProduct(2, 3)=%d, want 6", got)
	}
	if got := saturatingProduct(^uint64(0), 2); got != int64(^uint64(0)>>1) {
		t.Fatalf("overflow result=%d, want MaxInt64", got)
	}
}

func TestZeroFilesystemCapacityIsReportedExactly(t *testing.T) {
	t.Parallel()
	directory := t.TempDir()
	sample, err := samplePathWithStatfs(directory, 1, time.Now(), func(_ string, filesystem *syscall.Statfs_t) error {
		filesystem.Bavail = 0
		filesystem.Bsize = 4096
		filesystem.Ffree = 0
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	if !sample.available || sample.freeBytes != 0 || sample.freeInodes != 0 {
		t.Fatalf("sample=%+v, want available storage with zero free bytes and inodes", sample)
	}
}

type metricPoint struct {
	attributes attribute.Set
	value      float64
}

func collectMetrics(t *testing.T, reader *sdkmetric.ManualReader) map[string][]metricPoint {
	t.Helper()
	var resourceMetrics metricdata.ResourceMetrics
	if err := reader.Collect(context.Background(), &resourceMetrics); err != nil {
		t.Fatal(err)
	}
	result := map[string][]metricPoint{}
	for _, scope := range resourceMetrics.ScopeMetrics {
		for _, metric := range scope.Metrics {
			switch data := metric.Data.(type) {
			case metricdata.Gauge[int64]:
				for _, point := range data.DataPoints {
					result[metric.Name] = append(result[metric.Name], metricPoint{point.Attributes, float64(point.Value)})
				}
			case metricdata.Gauge[float64]:
				for _, point := range data.DataPoints {
					result[metric.Name] = append(result[metric.Name], metricPoint{point.Attributes, point.Value})
				}
			}
		}
	}
	return result
}

func assertMetricValue(t *testing.T, metrics map[string][]metricPoint, name, storage string, want float64) {
	t.Helper()
	for _, point := range metrics[name] {
		value, ok := point.attributes.Value(attribute.Key("storage"))
		if ok && value.AsString() == storage {
			if point.value != want {
				t.Fatalf("metric %q storage %q value=%v, want %v", name, storage, point.value, want)
			}
			return
		}
	}
	t.Fatalf("metric %q storage %q not found", name, storage)
}

func equalStringSlices(got, want []string) bool {
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
