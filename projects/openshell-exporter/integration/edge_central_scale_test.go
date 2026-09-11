// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package integration

import (
	"bytes"
	"context"
	"crypto/sha256"
	"fmt"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"sync"
	"testing"
	"time"

	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/status"
)

const scaleTenant = "customer-a"

type scaleDestination struct {
	ptraceotlp.UnimplementedGRPCServer

	mu                sync.Mutex
	available         bool
	attemptedReplicas map[string]int
	delivered         map[string]scaleDelivery
}

type scaleDelivery struct {
	Replica   string
	SandboxID string
	TenantID  string
	ShardKey  string
}

func newScaleDestination() *scaleDestination {
	return &scaleDestination{
		attemptedReplicas: map[string]int{},
		delivered:         map[string]scaleDelivery{},
	}
}

func (destination *scaleDestination) Export(
	_ context.Context,
	request ptraceotlp.ExportRequest,
) (ptraceotlp.ExportResponse, error) {
	destination.mu.Lock()
	defer destination.mu.Unlock()
	for resourceIndex := 0; resourceIndex < request.Traces().ResourceSpans().Len(); resourceIndex++ {
		resourceSpans := request.Traces().ResourceSpans().At(resourceIndex)
		attributes := resourceSpans.Resource().Attributes()
		replica := scaleAttribute(attributes, "openshell.exporter.central.replica")
		destination.attemptedReplicas[replica]++
		if !destination.available {
			continue
		}
		delivery := scaleDelivery{
			Replica:   replica,
			SandboxID: scaleAttribute(attributes, "openshell.sandbox.id"),
			TenantID:  scaleAttribute(attributes, "openshell.exporter.tenant.id"),
			ShardKey:  scaleAttribute(attributes, "openshell.exporter.shard.key"),
		}
		for scopeIndex := 0; scopeIndex < resourceSpans.ScopeSpans().Len(); scopeIndex++ {
			spans := resourceSpans.ScopeSpans().At(scopeIndex).Spans()
			for spanIndex := 0; spanIndex < spans.Len(); spanIndex++ {
				destination.delivered[spans.At(spanIndex).Name()] = delivery
			}
		}
	}
	if !destination.available {
		return ptraceotlp.NewExportResponse(), status.Error(codes.Unavailable, "qualified destination outage")
	}
	return ptraceotlp.NewExportResponse(), nil
}

func (destination *scaleDestination) setAvailable(available bool) {
	destination.mu.Lock()
	destination.available = available
	destination.mu.Unlock()
}

func (destination *scaleDestination) replicaAttempts(replica string) int {
	destination.mu.Lock()
	defer destination.mu.Unlock()
	return destination.attemptedReplicas[replica]
}

func (destination *scaleDestination) generation(generation string, count int) (map[string]scaleDelivery, bool) {
	destination.mu.Lock()
	defer destination.mu.Unlock()
	result := make(map[string]scaleDelivery, count)
	for index := 0; index < count; index++ {
		name := fmt.Sprintf("%s-%03d", generation, index)
		delivery, ok := destination.delivered[name]
		if !ok {
			return nil, false
		}
		result[delivery.SandboxID] = delivery
	}
	return result, len(result) == count
}

func (destination *scaleDestination) hasSpan(name string) bool {
	destination.mu.Lock()
	defer destination.mu.Unlock()
	_, ok := destination.delivered[name]
	return ok
}

func scaleAttribute(attributes pcommon.Map, key string) string {
	value, ok := attributes.Get(key)
	if !ok || value.Type() != pcommon.ValueTypeStr {
		return ""
	}
	return value.Str()
}

type scaleCollector struct {
	command *exec.Cmd
	output  bytes.Buffer
}

func startScaleCollector(t *testing.T, binary, configPath string, listenPort int) *scaleCollector {
	t.Helper()
	collector := &scaleCollector{}
	collector.command = exec.Command(binary, "--config", configPath)
	collector.command.Stdout = &collector.output
	collector.command.Stderr = &collector.output
	if err := collector.command.Start(); err != nil {
		t.Fatalf("start scale Collector: %v", err)
	}
	deadline := time.Now().Add(10 * time.Second)
	for time.Now().Before(deadline) {
		connection, err := net.DialTimeout("tcp", fmt.Sprintf("127.0.0.1:%d", listenPort), 100*time.Millisecond)
		if err == nil {
			_ = connection.Close()
			return collector
		}
		if collector.command.ProcessState != nil && collector.command.ProcessState.Exited() {
			break
		}
		time.Sleep(50 * time.Millisecond)
	}
	collector.stop(false)
	t.Fatalf("Collector did not listen on port %d:\n%s", listenPort, collector.output.String())
	return nil
}

func (collector *scaleCollector) stop(crash bool) {
	if collector == nil || collector.command == nil || collector.command.Process == nil {
		return
	}
	if crash {
		_ = collector.command.Process.Kill()
	} else {
		_ = collector.command.Process.Signal(os.Interrupt)
	}
	done := make(chan struct{})
	go func() {
		_ = collector.command.Wait()
		close(done)
	}()
	select {
	case <-done:
	case <-time.After(5 * time.Second):
		_ = collector.command.Process.Kill()
		<-done
	}
	collector.command = nil
}

func TestEdgeCentralShardingOutageRestartAndReshard(t *testing.T) {
	binary := os.Getenv("TEST_EXPORTER_BINARY")
	if binary == "" {
		t.Skip("TEST_EXPORTER_BINARY is required for edge-to-central scale qualification")
	}
	absoluteBinary, err := filepath.Abs(binary)
	if err != nil {
		t.Fatal(err)
	}

	destination := newScaleDestination()
	destinationListener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	destinationServer := grpc.NewServer()
	ptraceotlp.RegisterGRPCServer(destinationServer, destination)
	go func() { _ = destinationServer.Serve(destinationListener) }()
	t.Cleanup(func() {
		destinationServer.Stop()
		_ = destinationListener.Close()
	})

	root := t.TempDir()
	centralAPort := freeTCPPort(t)
	centralBPort := freeTCPPort(t)
	edgePort := freeTCPPort(t)
	centralAConfig := filepath.Join(root, "central-a.yaml")
	centralBConfig := filepath.Join(root, "central-b.yaml")
	edgeConfig := filepath.Join(root, "edge.yaml")
	writeScaleCentralConfig(t, centralAConfig, filepath.Join(root, "central-a-queue"), centralAPort, destinationListener.Addr().String(), "central-a")
	writeScaleCentralConfig(t, centralBConfig, filepath.Join(root, "central-b-queue"), centralBPort, destinationListener.Addr().String(), "central-b")
	writeScaleEdgeConfig(t, edgeConfig, filepath.Join(root, "edge-queue"), edgePort, []int{centralAPort, centralBPort})

	centralA := startScaleCollector(t, absoluteBinary, centralAConfig, centralAPort)
	centralB := startScaleCollector(t, absoluteBinary, centralBConfig, centralBPort)
	edge := startScaleCollector(t, absoluteBinary, edgeConfig, edgePort)
	t.Cleanup(func() {
		edge.stop(false)
		centralA.stop(false)
		centralB.stop(false)
		if t.Failed() {
			t.Logf("edge output:\n%s", edge.output.String())
			t.Logf("central A output:\n%s", centralA.output.String())
			t.Logf("central B output:\n%s", centralB.output.String())
		}
	})

	const shardCount = 64
	exportScaleGeneration(t, edgePort, "before-restart", shardCount)
	waitFor(t, "both central replicas to own isolated outage work", func() bool {
		return destination.replicaAttempts("central-a") > 0 && destination.replicaAttempts("central-b") > 0
	})
	assertScaleQueueState(t, filepath.Join(root, "central-a-queue"))
	assertScaleQueueState(t, filepath.Join(root, "central-b-queue"))

	centralA.stop(true)
	centralB.stop(true)
	destination.setAvailable(true)
	centralA = startScaleCollector(t, absoluteBinary, centralAConfig, centralAPort)
	centralB = startScaleCollector(t, absoluteBinary, centralBConfig, centralBPort)

	var initial map[string]scaleDelivery
	waitFor(t, "all accepted evidence to replay after central crashes", func() bool {
		var complete bool
		initial, complete = destination.generation("before-restart", shardCount)
		return complete
	})
	assertScaleDeliveries(t, initial, shardCount)

	// Planned scale-down: the removed replica is drained before membership changes.
	edge.stop(false)
	centralB.stop(false)
	writeScaleEdgeConfig(t, edgeConfig, filepath.Join(root, "edge-queue"), edgePort, []int{centralAPort})
	edge = startScaleCollector(t, absoluteBinary, edgeConfig, edgePort)
	exportScaleGeneration(t, edgePort, "scaled-down", shardCount)
	var scaledDown map[string]scaleDelivery
	waitFor(t, "all shards to route to the remaining replica", func() bool {
		var complete bool
		scaledDown, complete = destination.generation("scaled-down", shardCount)
		return complete
	})
	for sandboxID, delivery := range scaledDown {
		if delivery.Replica != "central-a" {
			t.Fatalf("scaled-down shard %s reached %s, want central-a", sandboxID, delivery.Replica)
		}
	}

	// Restoring the same membership must restore the original deterministic map.
	centralB = startScaleCollector(t, absoluteBinary, centralBConfig, centralBPort)
	edge.stop(false)
	writeScaleEdgeConfig(t, edgeConfig, filepath.Join(root, "edge-queue"), edgePort, []int{centralAPort, centralBPort})
	edge = startScaleCollector(t, absoluteBinary, edgeConfig, edgePort)
	exportScaleGeneration(t, edgePort, "restored", shardCount)
	var restored map[string]scaleDelivery
	waitFor(t, "restored membership delivery", func() bool {
		var complete bool
		restored, complete = destination.generation("restored", shardCount)
		return complete
	})
	for sandboxID, before := range initial {
		after := restored[sandboxID]
		if before.Replica != after.Replica || before.ShardKey != after.ShardKey {
			t.Fatalf("restored route for %s = %s/%s, want %s/%s", sandboxID, after.Replica, after.ShardKey, before.Replica, before.ShardKey)
		}
	}

	// A central replica is not a shared multi-tenant endpoint. It fails closed
	// when a caller bypasses the edge and supplies another tenant marker.
	exportForgedTenant(t, centralAPort)
	time.Sleep(300 * time.Millisecond)
	if destination.hasSpan("forged-tenant") {
		t.Fatal("central replica delivered evidence for an unconfigured tenant")
	}
}

func writeScaleCentralConfig(t *testing.T, path, queuePath string, receiverPort int, destination, replica string) {
	t.Helper()
	metricsPort := freeTCPPort(t)
	config := fmt.Sprintf(`extensions:
  file_storage/queue:
    directory: %s
    create_directory: true
receivers:
  otlp/internal:
    protocols:
      grpc:
        endpoint: 127.0.0.1:%d
processors:
  evidencecontract/verify:
    mode: verify
    contract_version: "1.0"
    tenant_id: %s
  resource/replica:
    attributes:
      - key: openshell.exporter.central.replica
        value: %s
        action: upsert
exporters:
  otlp/destination:
    endpoint: %s
    tls:
      insecure: true
    timeout: 100ms
    retry_on_failure:
      enabled: true
      initial_interval: 50ms
      max_interval: 100ms
      max_elapsed_time: 0s
    sending_queue:
      enabled: true
      storage: file_storage/queue
      queue_size: 512
      num_consumers: 1
      block_on_overflow: true
      batch:
        flush_timeout: 20ms
        min_size: 1
        max_size: 8
        sizer: items
service:
  telemetry:
    metrics:
      level: normal
      readers:
        - pull:
            exporter:
              prometheus:
                host: 127.0.0.1
                port: %d
  extensions: [file_storage/queue]
  pipelines:
    traces:
      receivers: [otlp/internal]
      processors: [evidencecontract/verify, resource/replica]
      exporters: [otlp/destination]
`, queuePath, receiverPort, scaleTenant, replica, destination, metricsPort)
	if err := os.WriteFile(path, []byte(config), 0o600); err != nil {
		t.Fatal(err)
	}
}

func writeScaleEdgeConfig(t *testing.T, path, queuePath string, receiverPort int, centralPorts []int) {
	t.Helper()
	metricsPort := freeTCPPort(t)
	endpoints := make([]string, 0, len(centralPorts))
	for _, port := range centralPorts {
		endpoints = append(endpoints, fmt.Sprintf("        - 127.0.0.1:%d", port))
	}
	sort.Strings(endpoints)
	config := fmt.Sprintf(`extensions:
  file_storage/queue:
    directory: %s
    create_directory: true
receivers:
  otlp/edge:
    protocols:
      grpc:
        endpoint: 127.0.0.1:%d
processors:
  evidencecontract/stamp:
    mode: stamp
    contract_version: "1.0"
    tenant_id: %s
exporters:
  load_balancing/internal:
    routing_key: attributes
    routing_attributes:
      - openshell.exporter.tenant.id
      - openshell.exporter.shard.key
    timeout: 100ms
    retry_on_failure:
      enabled: true
      initial_interval: 50ms
      max_interval: 100ms
      max_elapsed_time: 0s
    sending_queue:
      enabled: true
      storage: file_storage/queue
      queue_size: 512
      num_consumers: 1
      block_on_overflow: true
      batch:
        flush_timeout: 20ms
        min_size: 1
        max_size: 1
        sizer: items
    protocol:
      otlp:
        tls:
          insecure: true
        timeout: 100ms
        retry_on_failure:
          enabled: false
        sending_queue:
          enabled: false
    resolver:
      static:
        hostnames:
%s
service:
  telemetry:
    metrics:
      level: normal
      readers:
        - pull:
            exporter:
              prometheus:
                host: 127.0.0.1
                port: %d
  extensions: [file_storage/queue]
  pipelines:
    traces:
      receivers: [otlp/edge]
      processors: [evidencecontract/stamp]
      exporters: [load_balancing/internal]
`, queuePath, receiverPort, scaleTenant, joinScaleLines(endpoints), metricsPort)
	if err := os.WriteFile(path, []byte(config), 0o600); err != nil {
		t.Fatal(err)
	}
}

func joinScaleLines(lines []string) string {
	result := ""
	for index, line := range lines {
		if index > 0 {
			result += "\n"
		}
		result += line
	}
	return result
}

func exportScaleGeneration(t *testing.T, receiverPort int, generation string, count int) {
	t.Helper()
	connection, err := grpc.NewClient(
		fmt.Sprintf("127.0.0.1:%d", receiverPort),
		grpc.WithTransportCredentials(insecure.NewCredentials()),
	)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = connection.Close() }()
	client := ptraceotlp.NewGRPCClient(connection)
	for index := 0; index < count; index++ {
		name := fmt.Sprintf("%s-%03d", generation, index)
		request := scaleTraceRequest(name, fmt.Sprintf("sandbox-%03d", index))
		requestContext, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		_, exportErr := client.Export(requestContext, request)
		cancel()
		if exportErr != nil {
			t.Fatalf("export %s: %v", name, exportErr)
		}
	}
}

func scaleTraceRequest(name, sandboxID string) ptraceotlp.ExportRequest {
	request := ptraceotlp.NewExportRequest()
	resourceSpans := request.Traces().ResourceSpans().AppendEmpty()
	attributes := resourceSpans.Resource().Attributes()
	attributes.PutStr("service.name", "scale-qualification")
	attributes.PutStr("telemetry.source", "nemo_relay")
	attributes.PutStr("openshell.gateway.id", "scale-gateway")
	attributes.PutStr("openshell.workspace", "default")
	attributes.PutStr("openshell.sandbox.id", sandboxID)
	span := resourceSpans.ScopeSpans().AppendEmpty().Spans().AppendEmpty()
	digest := sha256.Sum256([]byte(name))
	var traceID pcommon.TraceID
	copy(traceID[:], digest[:16])
	var spanID pcommon.SpanID
	copy(spanID[:], digest[16:24])
	span.SetTraceID(traceID)
	span.SetSpanID(spanID)
	span.SetName(name)
	span.SetStartTimestamp(pcommon.NewTimestampFromTime(time.Now().Add(-time.Millisecond)))
	span.SetEndTimestamp(pcommon.NewTimestampFromTime(time.Now()))
	return request
}

func exportForgedTenant(t *testing.T, centralPort int) {
	t.Helper()
	request := scaleTraceRequest("forged-tenant", "sandbox-forged")
	attributes := request.Traces().ResourceSpans().At(0).Resource().Attributes()
	attributes.PutStr("openshell.exporter.contract.version", "1.0")
	attributes.PutStr("openshell.exporter.contract.stage", "privacy_filtered")
	attributes.PutStr("openshell.exporter.contract.signal", "traces")
	attributes.PutStr("openshell.exporter.contract.producer", "openshell-event-exporter")
	attributes.PutStr("openshell.exporter.tenant.id", "customer-b")
	attributes.PutStr("openshell.exporter.shard.version", "1")
	attributes.PutStr("openshell.exporter.shard.key", "sha256:forged")
	connection, err := grpc.NewClient(
		fmt.Sprintf("127.0.0.1:%d", centralPort),
		grpc.WithTransportCredentials(insecure.NewCredentials()),
	)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = connection.Close() }()
	requestContext, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	_, _ = ptraceotlp.NewGRPCClient(connection).Export(requestContext, request)
}

func assertScaleQueueState(t *testing.T, path string) {
	t.Helper()
	entries, err := os.ReadDir(path)
	if err != nil {
		t.Fatalf("read isolated queue %s: %v", path, err)
	}
	if len(entries) == 0 {
		t.Fatalf("isolated queue %s has no durable state", path)
	}
}

func assertScaleDeliveries(t *testing.T, deliveries map[string]scaleDelivery, count int) {
	t.Helper()
	if len(deliveries) != count {
		t.Fatalf("delivered sandboxes = %d, want %d", len(deliveries), count)
	}
	replicas := map[string]bool{}
	for sandboxID, delivery := range deliveries {
		if delivery.TenantID != scaleTenant {
			t.Fatalf("sandbox %s tenant = %q, want %q", sandboxID, delivery.TenantID, scaleTenant)
		}
		if delivery.ShardKey == "" {
			t.Fatalf("sandbox %s lost its shard key", sandboxID)
		}
		replicas[delivery.Replica] = true
	}
	if !replicas["central-a"] || !replicas["central-b"] {
		t.Fatalf("deterministic distribution did not use both replicas: %v", replicas)
	}
}
