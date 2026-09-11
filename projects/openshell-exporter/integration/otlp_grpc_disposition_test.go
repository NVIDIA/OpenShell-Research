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
	"sync"
	"testing"
	"time"

	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
	"google.golang.org/genproto/googleapis/rpc/errdetails"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
	_ "google.golang.org/grpc/encoding/gzip"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/types/known/durationpb"
)

type otlpGRPCDispositionScenario struct {
	name      string
	code      codes.Code
	retryInfo bool
	retryable bool
}

var otlpGRPCDispositionScenarios = []otlpGRPCDispositionScenario{
	{name: "canceled", code: codes.Canceled, retryable: true},
	{name: "unknown", code: codes.Unknown},
	{name: "invalid-argument", code: codes.InvalidArgument},
	{name: "deadline-exceeded", code: codes.DeadlineExceeded, retryable: true},
	{name: "not-found", code: codes.NotFound},
	{name: "already-exists", code: codes.AlreadyExists},
	{name: "permission-denied", code: codes.PermissionDenied},
	{name: "resource-exhausted-without-retry-info", code: codes.ResourceExhausted},
	{name: "resource-exhausted-with-retry-info", code: codes.ResourceExhausted, retryInfo: true, retryable: true},
	{name: "failed-precondition", code: codes.FailedPrecondition},
	{name: "aborted", code: codes.Aborted, retryable: true},
	{name: "out-of-range", code: codes.OutOfRange, retryable: true},
	{name: "unimplemented", code: codes.Unimplemented},
	{name: "internal", code: codes.Internal},
	{name: "unavailable", code: codes.Unavailable, retryable: true},
	{name: "data-loss", code: codes.DataLoss, retryable: true},
	{name: "unauthenticated", code: codes.Unauthenticated},
}

type otlpGRPCDispositionState struct {
	scenario otlpGRPCDispositionScenario
	attempts int
	accepted bool
}

type otlpGRPCDispositionServer struct {
	ptraceotlp.UnimplementedGRPCServer

	mu     sync.Mutex
	states map[string]*otlpGRPCDispositionState
}

func newOTLPGRPCDispositionServer() *otlpGRPCDispositionServer {
	states := make(map[string]*otlpGRPCDispositionState, len(otlpGRPCDispositionScenarios))
	for _, scenario := range otlpGRPCDispositionScenarios {
		states[scenario.name] = &otlpGRPCDispositionState{scenario: scenario}
	}
	return &otlpGRPCDispositionServer{states: states}
}

func (s *otlpGRPCDispositionServer) Export(
	_ context.Context,
	request ptraceotlp.ExportRequest,
) (ptraceotlp.ExportResponse, error) {
	name := dispositionSpanName(request)
	if name == "readiness" {
		return ptraceotlp.NewExportResponse(), nil
	}

	s.mu.Lock()
	defer s.mu.Unlock()
	state, ok := s.states[name]
	if !ok {
		return ptraceotlp.NewExportResponse(), status.Error(codes.InvalidArgument, "unknown disposition scenario")
	}
	state.attempts++
	if state.scenario.retryable && state.attempts > 1 {
		state.accepted = true
		return ptraceotlp.NewExportResponse(), nil
	}
	if state.scenario.code == codes.ResourceExhausted && state.scenario.retryInfo {
		withDetails, err := status.New(state.scenario.code, state.scenario.name).WithDetails(&errdetails.RetryInfo{
			RetryDelay: durationpb.New(10 * time.Millisecond),
		})
		if err != nil {
			return ptraceotlp.NewExportResponse(), status.Error(codes.Internal, err.Error())
		}
		return ptraceotlp.NewExportResponse(), withDetails.Err()
	}
	return ptraceotlp.NewExportResponse(), status.Error(state.scenario.code, state.scenario.name)
}

func dispositionSpanName(request ptraceotlp.ExportRequest) string {
	resourceSpans := request.Traces().ResourceSpans()
	if resourceSpans.Len() != 1 {
		return ""
	}
	scopeSpans := resourceSpans.At(0).ScopeSpans()
	if scopeSpans.Len() != 1 || scopeSpans.At(0).Spans().Len() != 1 {
		return ""
	}
	return scopeSpans.At(0).Spans().At(0).Name()
}

func (s *otlpGRPCDispositionServer) snapshot(name string) (int, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	state := s.states[name]
	return state.attempts, state.accepted
}

func TestOTLPGRPCDispositionThroughCollector(t *testing.T) {
	binary := os.Getenv("TEST_EXPORTER_BINARY")
	if binary == "" {
		t.Skip("TEST_EXPORTER_BINARY is required for Collector integration")
	}
	absoluteBinary, err := filepath.Abs(binary)
	if err != nil {
		t.Fatal(err)
	}

	fixture := newOTLPGRPCDispositionServer()
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	server := grpc.NewServer()
	ptraceotlp.RegisterGRPCServer(server, fixture)
	go func() {
		_ = server.Serve(listener)
	}()
	t.Cleanup(func() {
		server.GracefulStop()
		_ = listener.Close()
	})

	receiverPort := freeTCPPort(t)
	root := t.TempDir()
	configPath := filepath.Join(root, "config.yaml")
	config := fmt.Sprintf(`receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 127.0.0.1:%d
exporters:
  otlp:
    endpoint: %s
    tls:
      insecure: true
    retry_on_failure:
      enabled: true
      initial_interval: 20ms
      max_interval: 20ms
      max_elapsed_time: 1s
    sending_queue:
      enabled: false
service:
  telemetry:
    metrics:
      level: none
  pipelines:
    traces:
      receivers: [otlp]
      exporters: [otlp]
`, receiverPort, listener.Addr().String())
	if err := os.WriteFile(configPath, []byte(config), 0o600); err != nil {
		t.Fatal(err)
	}

	ctx, cancel := context.WithCancel(context.Background())
	command := exec.CommandContext(ctx, absoluteBinary, "--config", configPath)
	var collectorOutput bytes.Buffer
	command.Stdout = &collectorOutput
	command.Stderr = &collectorOutput
	if err := command.Start(); err != nil {
		cancel()
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_ = command.Process.Signal(os.Interrupt)
		done := make(chan struct{})
		go func() {
			_ = command.Wait()
			close(done)
		}()
		select {
		case <-done:
		case <-time.After(5 * time.Second):
			cancel()
			<-done
		}
		if t.Failed() {
			t.Logf("Collector output:\n%s", collectorOutput.String())
		}
	})

	connection, err := grpc.NewClient(
		fmt.Sprintf("127.0.0.1:%d", receiverPort),
		grpc.WithTransportCredentials(insecure.NewCredentials()),
	)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = connection.Close() })
	client := ptraceotlp.NewGRPCClient(connection)
	waitFor(t, "OTLP disposition Collector startup", func() bool {
		request := dispositionTraceRequest("readiness")
		requestContext, requestCancel := context.WithTimeout(context.Background(), 300*time.Millisecond)
		defer requestCancel()
		_, requestErr := client.Export(requestContext, request)
		return requestErr == nil
	})

	for _, scenario := range otlpGRPCDispositionScenarios {
		t.Run(scenario.name, func(t *testing.T) {
			requestContext, requestCancel := context.WithTimeout(context.Background(), 2*time.Second)
			_, _ = client.Export(requestContext, dispositionTraceRequest(scenario.name))
			requestCancel()

			expectedAttempts := 1
			if scenario.retryable {
				expectedAttempts = 2
			}
			waitFor(t, "OTLP gRPC disposition "+scenario.name, func() bool {
				attempts, accepted := fixture.snapshot(scenario.name)
				return attempts >= expectedAttempts && (!scenario.retryable || accepted)
			})
			time.Sleep(100 * time.Millisecond)
			attempts, accepted := fixture.snapshot(scenario.name)
			if attempts != expectedAttempts {
				t.Fatalf("gRPC code %s attempts=%d, want %d", scenario.code, attempts, expectedAttempts)
			}
			if accepted != scenario.retryable {
				t.Fatalf("gRPC code %s accepted=%v, want %v", scenario.code, accepted, scenario.retryable)
			}
		})
	}
}

func dispositionTraceRequest(name string) ptraceotlp.ExportRequest {
	request := ptraceotlp.NewExportRequest()
	resourceSpans := request.Traces().ResourceSpans().AppendEmpty()
	resourceSpans.Resource().Attributes().PutStr("service.name", "otlp-grpc-disposition-integration")
	span := resourceSpans.ScopeSpans().AppendEmpty().Spans().AppendEmpty()
	digest := sha256.Sum256([]byte("otlp-grpc-disposition:" + name))
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
