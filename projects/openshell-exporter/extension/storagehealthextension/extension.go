// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package storagehealthextension

import (
	"context"
	"errors"
	"io"
	"math"
	"os"
	"path/filepath"
	"sort"
	"sync"
	"syscall"
	"time"

	"go.opentelemetry.io/collector/component"
	"go.opentelemetry.io/collector/extension"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/metric"
	metricnoop "go.opentelemetry.io/otel/metric/noop"
)

const storageMeterName = "github.com/NVIDIA-dev/OpenShell-exporter/storagehealth"

type storageMetrics struct {
	configured   metric.Int64Gauge
	available    metric.Int64Gauge
	freeBytes    metric.Int64Gauge
	freeInodes   metric.Int64Gauge
	statePresent metric.Int64Gauge
	stateAge     metric.Float64Gauge
	truncated    metric.Int64Gauge
}

type storageHealthExtension struct {
	config  *Config
	metrics storageMetrics
	now     func() time.Time

	mutex  sync.Mutex
	cancel context.CancelFunc
	wait   sync.WaitGroup
}

type pathSample struct {
	available    bool
	freeBytes    int64
	freeInodes   int64
	statePresent bool
	stateAge     float64
	truncated    bool
}

func newStorageHealthExtension(config *Config, settings extension.Settings) (*storageHealthExtension, error) {
	meterProvider := settings.MeterProvider
	if meterProvider == nil {
		meterProvider = metricnoop.NewMeterProvider()
	}
	meter := meterProvider.Meter(storageMeterName)
	configured, err := meter.Int64Gauge(
		"openshell.exporter.storage.configured",
		metric.WithDescription("Configured durable exporter paths by bounded storage role."),
		metric.WithUnit("1"),
	)
	if err != nil {
		return nil, err
	}
	available, err := meter.Int64Gauge(
		"openshell.exporter.storage.available",
		metric.WithDescription("Stat availability for each configured durable exporter path."),
		metric.WithUnit("1"),
	)
	if err != nil {
		return nil, err
	}
	freeBytes, err := meter.Int64Gauge(
		"openshell.exporter.storage.free_bytes",
		metric.WithDescription("Filesystem bytes available to each configured durable exporter path."),
		metric.WithUnit("By"),
	)
	if err != nil {
		return nil, err
	}
	freeInodes, err := meter.Int64Gauge(
		"openshell.exporter.storage.free_inodes",
		metric.WithDescription("Filesystem inodes available to each configured durable exporter path."),
		metric.WithUnit("{inode}"),
	)
	if err != nil {
		return nil, err
	}
	statePresent, err := meter.Int64Gauge(
		"openshell.exporter.storage.state_present",
		metric.WithDescription("At least one state file exists at the configured durable exporter path."),
		metric.WithUnit("1"),
	)
	if err != nil {
		return nil, err
	}
	stateAge, err := meter.Float64Gauge(
		"openshell.exporter.storage.state_age_seconds",
		metric.WithDescription("Age of the newest state file at the configured durable exporter path."),
		metric.WithUnit("s"),
	)
	if err != nil {
		return nil, err
	}
	truncated, err := meter.Int64Gauge(
		"openshell.exporter.storage.scan_truncated",
		metric.WithDescription("Durable path contained more entries than the bounded state-age scan limit."),
		metric.WithUnit("1"),
	)
	if err != nil {
		return nil, err
	}
	return &storageHealthExtension{
		config: config,
		metrics: storageMetrics{
			configured: configured, available: available, freeBytes: freeBytes,
			freeInodes: freeInodes, statePresent: statePresent, stateAge: stateAge,
			truncated: truncated,
		},
		now: time.Now,
	}, nil
}

func (extension *storageHealthExtension) Start(_ context.Context, _ component.Host) error {
	extension.mutex.Lock()
	defer extension.mutex.Unlock()
	if extension.cancel != nil {
		return nil
	}
	workerContext, cancel := context.WithCancel(context.Background())
	extension.cancel = cancel
	extension.wait.Add(1)
	go extension.run(workerContext)
	return nil
}

func (extension *storageHealthExtension) Shutdown(ctx context.Context) error {
	extension.mutex.Lock()
	cancel := extension.cancel
	extension.cancel = nil
	extension.mutex.Unlock()
	if cancel != nil {
		cancel()
		done := make(chan struct{})
		go func() {
			extension.wait.Wait()
			close(done)
		}()
		select {
		case <-done:
		case <-ctx.Done():
			return ctx.Err()
		}
	}
	return nil
}

func (extension *storageHealthExtension) run(ctx context.Context) {
	defer extension.wait.Done()
	extension.sample(ctx)
	ticker := time.NewTicker(extension.config.Interval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			extension.sample(ctx)
		}
	}
}

func (extension *storageHealthExtension) sample(ctx context.Context) {
	names := make([]string, 0, len(extension.config.Paths))
	for name := range extension.config.Paths {
		names = append(names, name)
	}
	sort.Strings(names)
	now := extension.now()
	for _, name := range names {
		attrs := metric.WithAttributes(attribute.String("storage", name))
		extension.metrics.configured.Record(ctx, 1, attrs)
		sample, err := samplePath(extension.config.Paths[name], extension.config.MaxEntries, now)
		if err != nil {
			extension.metrics.available.Record(ctx, 0, attrs)
			extension.metrics.freeBytes.Record(ctx, 0, attrs)
			extension.metrics.freeInodes.Record(ctx, 0, attrs)
			extension.metrics.statePresent.Record(ctx, 0, attrs)
			extension.metrics.stateAge.Record(ctx, 0, attrs)
			extension.metrics.truncated.Record(ctx, 0, attrs)
			continue
		}
		extension.metrics.available.Record(ctx, boolValue(sample.available), attrs)
		extension.metrics.freeBytes.Record(ctx, sample.freeBytes, attrs)
		extension.metrics.freeInodes.Record(ctx, sample.freeInodes, attrs)
		extension.metrics.statePresent.Record(ctx, boolValue(sample.statePresent), attrs)
		extension.metrics.stateAge.Record(ctx, sample.stateAge, attrs)
		extension.metrics.truncated.Record(ctx, boolValue(sample.truncated), attrs)
	}
}

func samplePath(path string, maxEntries int, now time.Time) (pathSample, error) {
	return samplePathWithStatfs(path, maxEntries, now, syscall.Statfs)
}

type statfsFunc func(string, *syscall.Statfs_t) error

func samplePathWithStatfs(path string, maxEntries int, now time.Time, statfs statfsFunc) (pathSample, error) {
	statTarget, err := nearestExistingPath(path)
	if err != nil {
		return pathSample{}, err
	}
	var filesystem syscall.Statfs_t
	if err := statfs(statTarget, &filesystem); err != nil {
		return pathSample{}, err
	}
	newest, present, truncated, err := newestState(path, maxEntries)
	if err != nil {
		return pathSample{}, err
	}
	age := 0.0
	if present {
		age = now.Sub(newest).Seconds()
		if age < 0 {
			age = 0
		}
	}
	return pathSample{
		available:    true,
		freeBytes:    saturatingProduct(uint64(filesystem.Bavail), uint64(filesystem.Bsize)),
		freeInodes:   saturatingInt64(uint64(filesystem.Ffree)),
		statePresent: present,
		stateAge:     age,
		truncated:    truncated,
	}, nil
}

func nearestExistingPath(path string) (string, error) {
	candidate := filepath.Clean(path)
	for {
		if _, err := os.Stat(candidate); err == nil {
			return candidate, nil
		} else if !errors.Is(err, os.ErrNotExist) {
			return "", err
		}
		parent := filepath.Dir(candidate)
		if parent == candidate {
			return "", os.ErrNotExist
		}
		candidate = parent
	}
}

func newestState(path string, maxEntries int) (time.Time, bool, bool, error) {
	info, err := os.Stat(path)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return time.Time{}, false, false, nil
		}
		return time.Time{}, false, false, err
	}
	if !info.IsDir() {
		return info.ModTime(), true, false, nil
	}
	directory, err := os.Open(path)
	if err != nil {
		return time.Time{}, false, false, err
	}
	defer func() {
		_ = directory.Close()
	}()
	entries, err := directory.ReadDir(maxEntries + 1)
	if err != nil && !errors.Is(err, io.EOF) {
		return time.Time{}, false, false, err
	}
	truncated := len(entries) > maxEntries
	if truncated {
		entries = entries[:maxEntries]
	}
	newest := time.Time{}
	for _, entry := range entries {
		entryInfo, err := entry.Info()
		if err != nil {
			return time.Time{}, false, truncated, err
		}
		if entryInfo.ModTime().After(newest) {
			newest = entryInfo.ModTime()
		}
	}
	return newest, !newest.IsZero(), truncated, nil
}

func boolValue(value bool) int64 {
	if value {
		return 1
	}
	return 0
}

func saturatingInt64(value uint64) int64 {
	if value > math.MaxInt64 {
		return math.MaxInt64
	}
	return int64(value)
}

func saturatingProduct(left, right uint64) int64 {
	if right != 0 && left > math.MaxInt64/right {
		return math.MaxInt64
	}
	return int64(left * right)
}
