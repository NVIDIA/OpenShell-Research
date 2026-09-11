// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package integration

import (
	"bufio"
	"fmt"
	"io/fs"
	"math/bits"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"
)

type capacityLatencyReport struct {
	Count                     int     `json:"count"`
	MeanMilliseconds          float64 `json:"mean_milliseconds"`
	P50UpperBoundMilliseconds int64   `json:"p50_upper_bound_milliseconds"`
	P95UpperBoundMilliseconds int64   `json:"p95_upper_bound_milliseconds"`
	P99UpperBoundMilliseconds int64   `json:"p99_upper_bound_milliseconds"`
	MaximumMilliseconds       int64   `json:"maximum_milliseconds"`
}

type capacityLatencyHistogram struct {
	count             uint64
	totalMilliseconds uint64
	maximumMillis     uint64
	buckets           [64]uint64
}

func (h *capacityLatencyHistogram) record(elapsed time.Duration) {
	if elapsed < 0 {
		elapsed = 0
	}
	milliseconds := uint64(elapsed / time.Millisecond)
	bucket := bits.Len64(milliseconds)
	if bucket >= len(h.buckets) {
		bucket = len(h.buckets) - 1
	}
	h.count++
	h.totalMilliseconds += milliseconds
	h.buckets[bucket]++
	if milliseconds > h.maximumMillis {
		h.maximumMillis = milliseconds
	}
}

func (h *capacityLatencyHistogram) report() capacityLatencyReport {
	report := capacityLatencyReport{Count: int(h.count), MaximumMilliseconds: int64(h.maximumMillis)}
	if h.count == 0 {
		return report
	}
	report.MeanMilliseconds = float64(h.totalMilliseconds) / float64(h.count)
	report.P50UpperBoundMilliseconds = h.percentileUpperBound(50)
	report.P95UpperBoundMilliseconds = h.percentileUpperBound(95)
	report.P99UpperBoundMilliseconds = h.percentileUpperBound(99)
	return report
}

func (h *capacityLatencyHistogram) percentileUpperBound(percentile uint64) int64 {
	target := (h.count*percentile + 99) / 100
	var cumulative uint64
	for bucket, count := range h.buckets {
		cumulative += count
		if cumulative < target {
			continue
		}
		if bucket == 0 {
			return 0
		}
		if bucket == len(h.buckets)-1 {
			return int64(^uint64(0) >> 1)
		}
		return int64((uint64(1) << bucket) - 1)
	}
	return int64(h.maximumMillis)
}

type capacityResourceReport struct {
	Supported              bool    `json:"supported"`
	SampleInterval         string  `json:"sample_interval"`
	Samples                int     `json:"samples"`
	SampleErrors           int     `json:"sample_errors"`
	LastError              string  `json:"last_error,omitempty"`
	MeanCPUPercent         float64 `json:"mean_cpu_percent"`
	MaximumCPUPercent      float64 `json:"maximum_cpu_percent"`
	MeanRSSBytes           int64   `json:"mean_rss_bytes"`
	MaximumRSSBytes        int64   `json:"maximum_rss_bytes"`
	ProcessReadBytes       uint64  `json:"process_read_bytes"`
	ProcessWrittenBytes    uint64  `json:"process_written_bytes"`
	MaximumCheckpointBytes int64   `json:"maximum_checkpoint_bytes"`
	MaximumQueueBytes      int64   `json:"maximum_queue_bytes"`
	MaximumRecoveryBytes   int64   `json:"maximum_recovery_bytes"`
}

type capacityProcessSnapshot struct {
	processTicks uint64
	hostTicks    uint64
	rssBytes     int64
	readBytes    uint64
	writtenBytes uint64
}

type capacitySampler struct {
	pid            int
	interval       time.Duration
	checkpointPath string
	queuePath      string
	recoveryPath   string
	stopChannel    chan struct{}
	done           chan struct{}
	stopOnce       sync.Once
	report         capacityResourceReport
	rssTotal       uint64
	cpuTotal       float64
	cpuSamples     int
	initialRead    uint64
	initialWritten uint64
}

func startCapacitySampler(
	pid int,
	interval time.Duration,
	checkpointPath string,
	queuePath string,
	recoveryPath string,
) (*capacitySampler, error) {
	if runtime.GOOS != "linux" {
		return nil, fmt.Errorf("capacity resource sampling requires Linux /proc")
	}
	if pid <= 0 || interval <= 0 {
		return nil, fmt.Errorf("capacity sampler pid and interval must be positive")
	}
	initial, err := readCapacityProcessSnapshot(pid)
	if err != nil {
		return nil, err
	}
	sampler := &capacitySampler{
		pid:            pid,
		interval:       interval,
		checkpointPath: checkpointPath,
		queuePath:      queuePath,
		recoveryPath:   recoveryPath,
		stopChannel:    make(chan struct{}),
		done:           make(chan struct{}),
		initialRead:    initial.readBytes,
		initialWritten: initial.writtenBytes,
		report: capacityResourceReport{
			Supported:      true,
			SampleInterval: interval.String(),
		},
	}
	sampler.record(initial, capacityProcessSnapshot{})
	go sampler.run(initial)
	return sampler, nil
}

func (s *capacitySampler) run(previous capacityProcessSnapshot) {
	defer close(s.done)
	ticker := time.NewTicker(s.interval)
	defer ticker.Stop()
	for {
		select {
		case <-ticker.C:
			previous = s.sample(previous)
		case <-s.stopChannel:
			s.sample(previous)
			s.finish()
			return
		}
	}
}

func (s *capacitySampler) sample(previous capacityProcessSnapshot) capacityProcessSnapshot {
	current, err := readCapacityProcessSnapshot(s.pid)
	if err != nil {
		s.report.SampleErrors++
		s.report.LastError = err.Error()
		return previous
	}
	s.record(current, previous)
	return current
}

func (s *capacitySampler) record(current, previous capacityProcessSnapshot) {
	s.report.Samples++
	s.rssTotal += uint64(current.rssBytes)
	if current.rssBytes > s.report.MaximumRSSBytes {
		s.report.MaximumRSSBytes = current.rssBytes
	}
	if previous.hostTicks > 0 && current.hostTicks > previous.hostTicks && current.processTicks >= previous.processTicks {
		cpuPercent := float64(current.processTicks-previous.processTicks) /
			float64(current.hostTicks-previous.hostTicks) * float64(runtime.NumCPU()) * 100
		s.cpuTotal += cpuPercent
		s.cpuSamples++
		if cpuPercent > s.report.MaximumCPUPercent {
			s.report.MaximumCPUPercent = cpuPercent
		}
	}
	if current.readBytes >= s.initialRead {
		s.report.ProcessReadBytes = current.readBytes - s.initialRead
	}
	if current.writtenBytes >= s.initialWritten {
		s.report.ProcessWrittenBytes = current.writtenBytes - s.initialWritten
	}
	for path, maximum := range map[string]*int64{
		s.checkpointPath: &s.report.MaximumCheckpointBytes,
		s.queuePath:      &s.report.MaximumQueueBytes,
		s.recoveryPath:   &s.report.MaximumRecoveryBytes,
	} {
		size, err := capacityPathSize(path)
		if err != nil {
			s.report.SampleErrors++
			s.report.LastError = err.Error()
			continue
		}
		if size > *maximum {
			*maximum = size
		}
	}
}

func (s *capacitySampler) finish() {
	if s.report.Samples > 0 {
		s.report.MeanRSSBytes = int64(s.rssTotal / uint64(s.report.Samples))
	}
	if s.cpuSamples > 0 {
		s.report.MeanCPUPercent = s.cpuTotal / float64(s.cpuSamples)
	}
}

func (s *capacitySampler) stop() capacityResourceReport {
	s.stopOnce.Do(func() { close(s.stopChannel) })
	<-s.done
	return s.report
}

func readCapacityProcessSnapshot(pid int) (capacityProcessSnapshot, error) {
	status, err := os.ReadFile(fmt.Sprintf("/proc/%d/status", pid))
	if err != nil {
		return capacityProcessSnapshot{}, fmt.Errorf("read process status: %w", err)
	}
	rssBytes, err := parseCapacityRSS(status)
	if err != nil {
		return capacityProcessSnapshot{}, err
	}
	processStat, err := os.ReadFile(fmt.Sprintf("/proc/%d/stat", pid))
	if err != nil {
		return capacityProcessSnapshot{}, fmt.Errorf("read process stat: %w", err)
	}
	processTicks, err := parseCapacityProcessTicks(processStat)
	if err != nil {
		return capacityProcessSnapshot{}, err
	}
	hostStat, err := os.ReadFile("/proc/stat")
	if err != nil {
		return capacityProcessSnapshot{}, fmt.Errorf("read host stat: %w", err)
	}
	hostTicks, err := parseCapacityHostTicks(hostStat)
	if err != nil {
		return capacityProcessSnapshot{}, err
	}
	processIO, err := os.ReadFile(fmt.Sprintf("/proc/%d/io", pid))
	if err != nil {
		return capacityProcessSnapshot{}, fmt.Errorf("read process io: %w", err)
	}
	readBytes, writtenBytes, err := parseCapacityIO(processIO)
	if err != nil {
		return capacityProcessSnapshot{}, err
	}
	return capacityProcessSnapshot{
		processTicks: processTicks,
		hostTicks:    hostTicks,
		rssBytes:     rssBytes,
		readBytes:    readBytes,
		writtenBytes: writtenBytes,
	}, nil
}

func parseCapacityRSS(encoded []byte) (int64, error) {
	scanner := bufio.NewScanner(strings.NewReader(string(encoded)))
	for scanner.Scan() {
		fields := strings.Fields(scanner.Text())
		if len(fields) == 3 && fields[0] == "VmRSS:" && fields[2] == "kB" {
			kilobytes, err := strconv.ParseInt(fields[1], 10, 64)
			if err != nil || kilobytes < 0 {
				return 0, fmt.Errorf("invalid VmRSS value %q", fields[1])
			}
			return kilobytes * 1024, nil
		}
	}
	return 0, fmt.Errorf("process status does not contain VmRSS")
}

func parseCapacityProcessTicks(encoded []byte) (uint64, error) {
	line := strings.TrimSpace(string(encoded))
	endName := strings.LastIndex(line, ") ")
	if endName < 0 {
		return 0, fmt.Errorf("process stat has no command terminator")
	}
	fields := strings.Fields(line[endName+2:])
	if len(fields) < 13 {
		return 0, fmt.Errorf("process stat has %d fields after command, want at least 13", len(fields))
	}
	userTicks, err := strconv.ParseUint(fields[11], 10, 64)
	if err != nil {
		return 0, fmt.Errorf("parse process user ticks: %w", err)
	}
	systemTicks, err := strconv.ParseUint(fields[12], 10, 64)
	if err != nil {
		return 0, fmt.Errorf("parse process system ticks: %w", err)
	}
	return userTicks + systemTicks, nil
}

func parseCapacityHostTicks(encoded []byte) (uint64, error) {
	line, _, _ := strings.Cut(string(encoded), "\n")
	fields := strings.Fields(line)
	if len(fields) < 2 || fields[0] != "cpu" {
		return 0, fmt.Errorf("host stat does not begin with aggregate cpu counters")
	}
	var total uint64
	for _, field := range fields[1:] {
		value, err := strconv.ParseUint(field, 10, 64)
		if err != nil {
			return 0, fmt.Errorf("parse host cpu ticks: %w", err)
		}
		total += value
	}
	return total, nil
}

func parseCapacityIO(encoded []byte) (uint64, uint64, error) {
	var readBytes, writtenBytes uint64
	foundRead := false
	foundWritten := false
	scanner := bufio.NewScanner(strings.NewReader(string(encoded)))
	for scanner.Scan() {
		fields := strings.Fields(scanner.Text())
		if len(fields) != 2 {
			continue
		}
		value, err := strconv.ParseUint(fields[1], 10, 64)
		if err != nil {
			return 0, 0, fmt.Errorf("parse process io %s: %w", fields[0], err)
		}
		switch fields[0] {
		case "read_bytes:":
			readBytes = value
			foundRead = true
		case "write_bytes:":
			writtenBytes = value
			foundWritten = true
		}
	}
	if !foundRead || !foundWritten {
		return 0, 0, fmt.Errorf("process io is missing read_bytes or write_bytes")
	}
	return readBytes, writtenBytes, nil
}

func capacityPathSize(path string) (int64, error) {
	var size int64
	err := filepath.WalkDir(path, func(current string, entry fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			if os.IsNotExist(walkErr) {
				return nil
			}
			return walkErr
		}
		if entry.IsDir() {
			return nil
		}
		info, err := entry.Info()
		if err != nil {
			return err
		}
		size += info.Size()
		return nil
	})
	if os.IsNotExist(err) {
		return 0, nil
	}
	return size, err
}

func TestCapacityLatencyHistogramIsBoundedAndMonotonic(t *testing.T) {
	var histogram capacityLatencyHistogram
	for _, elapsed := range []time.Duration{0, time.Millisecond, 2 * time.Millisecond, 100 * time.Millisecond} {
		histogram.record(elapsed)
	}
	report := histogram.report()
	if report.Count != 4 || report.MeanMilliseconds != 25.75 || report.MaximumMilliseconds != 100 {
		t.Fatalf("unexpected latency report: %+v", report)
	}
	if report.P50UpperBoundMilliseconds != 1 || report.P95UpperBoundMilliseconds != 127 ||
		report.P99UpperBoundMilliseconds != 127 {
		t.Fatalf("unexpected bounded percentiles: %+v", report)
	}
}

func TestCapacityProcParsers(t *testing.T) {
	rss, err := parseCapacityRSS([]byte("Name:\texporter\nVmRSS:\t1234 kB\n"))
	if err != nil || rss != 1234*1024 {
		t.Fatalf("rss=%d err=%v", rss, err)
	}
	processTicks, err := parseCapacityProcessTicks([]byte("123 (collector worker) R 1 2 3 4 5 6 7 8 9 10 11 12 13"))
	if err != nil || processTicks != 23 {
		t.Fatalf("process ticks=%d err=%v", processTicks, err)
	}
	hostTicks, err := parseCapacityHostTicks([]byte("cpu 1 2 3 4\ncpu0 1 2 3 4\n"))
	if err != nil || hostTicks != 10 {
		t.Fatalf("host ticks=%d err=%v", hostTicks, err)
	}
	readBytes, writtenBytes, err := parseCapacityIO([]byte("rchar: 100\nread_bytes: 200\nwrite_bytes: 300\n"))
	if err != nil || readBytes != 200 || writtenBytes != 300 {
		t.Fatalf("io=%d/%d err=%v", readBytes, writtenBytes, err)
	}

	for name, parse := range map[string]func() error{
		"rss":          func() error { _, err := parseCapacityRSS([]byte("VmSize: 1 kB")); return err },
		"process stat": func() error { _, err := parseCapacityProcessTicks([]byte("malformed")); return err },
		"host stat":    func() error { _, err := parseCapacityHostTicks([]byte("intr 1 2")); return err },
		"process io":   func() error { _, _, err := parseCapacityIO([]byte("read_bytes: 1")); return err },
	} {
		t.Run(name, func(t *testing.T) {
			if err := parse(); err == nil {
				t.Fatal("malformed proc input was accepted")
			}
		})
	}
}

func TestCapacitySamplerRecordsBoundedProcessAndStorageEvidence(t *testing.T) {
	if runtime.GOOS != "linux" {
		t.Skip("Linux /proc is required")
	}
	root := t.TempDir()
	checkpointPath := filepath.Join(root, "checkpoints")
	queuePath := filepath.Join(root, "queue")
	recoveryPath := filepath.Join(root, "recovery.json")
	for path, body := range map[string][]byte{
		filepath.Join(checkpointPath, "state.db"): []byte("checkpoint"),
		filepath.Join(queuePath, "queue.db"):      []byte("queue-state"),
		recoveryPath:                              []byte("recovery-evidence"),
	} {
		if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, body, 0o600); err != nil {
			t.Fatal(err)
		}
	}

	sampler, err := startCapacitySampler(os.Getpid(), 10*time.Millisecond, checkpointPath, queuePath, recoveryPath)
	if err != nil {
		t.Fatal(err)
	}
	time.Sleep(35 * time.Millisecond)
	report := sampler.stop()
	if !report.Supported || report.Samples < 3 || report.SampleErrors != 0 || report.MaximumRSSBytes <= 0 {
		t.Fatalf("incomplete process evidence: %+v", report)
	}
	if report.MaximumCheckpointBytes != int64(len("checkpoint")) ||
		report.MaximumQueueBytes != int64(len("queue-state")) ||
		report.MaximumRecoveryBytes != int64(len("recovery-evidence")) {
		t.Fatalf("unexpected storage evidence: %+v", report)
	}
}
