// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package watchsandboxreceiver

import (
	"context"
	"errors"
	"fmt"
	"io"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/NVIDIA/OpenShell/sdk/go/proto/datamodelv1"
	pb "github.com/NVIDIA/OpenShell/sdk/go/proto/openshellv1"
	"go.opentelemetry.io/collector/component"
	"go.opentelemetry.io/collector/consumer"
	"go.opentelemetry.io/collector/consumer/consumererror"
	"go.opentelemetry.io/collector/pdata/plog"
	"go.opentelemetry.io/collector/receiver"
	sdkmetric "go.opentelemetry.io/otel/sdk/metric"
	"go.opentelemetry.io/otel/sdk/metric/metricdata"
	"go.uber.org/zap"
)

type lifecycleHost struct{}

func (lifecycleHost) GetExtensions() map[component.ID]component.Component {
	return map[component.ID]component.Component{}
}

func lifecycleSettings() receiver.Settings {
	return receiver.Settings{
		ID: component.NewID(componentType),
		TelemetrySettings: component.TelemetrySettings{
			Logger: zap.NewNop(),
		},
	}
}

type lifecycleGateway struct {
	sandboxes    []sandboxRef
	watchMu      sync.Mutex
	watchedIDs   []string
	watchCalls   atomic.Int32
	listCalls    atomic.Int32
	closeCalls   atomic.Int32
	versionError error
	version      string
}

func (g *lifecycleGateway) List(context.Context) ([]sandboxRef, error) {
	g.listCalls.Add(1)
	return g.sandboxes, nil
}

func (g *lifecycleGateway) Watch(ctx context.Context, sandboxID string) (eventStream, error) {
	g.watchMu.Lock()
	g.watchedIDs = append(g.watchedIDs, sandboxID)
	g.watchMu.Unlock()
	if g.watchCalls.Add(1) == 1 {
		return &scriptedEventStream{events: []*pb.SandboxStreamEvent{{
			Payload: &pb.SandboxStreamEvent_Sandbox{Sandbox: &pb.Sandbox{
				Metadata: &datamodelv1.ObjectMeta{
					Id:              sandboxID,
					Name:            "allowed",
					Workspace:       "default",
					ResourceVersion: 1,
				},
			}},
		}}}, nil
	}
	return blockingEventStream{ctx: ctx}, nil
}

func (g *lifecycleGateway) GatewayVersion(context.Context) (string, error) {
	if g.version != "" {
		return g.version, g.versionError
	}
	return "0.1.2", g.versionError
}

func (g *lifecycleGateway) GetDraft(context.Context, string) (*pb.GetDraftPolicyResponse, error) {
	return nil, nil
}

func (g *lifecycleGateway) GetDraftHistory(context.Context, string) (*pb.GetDraftHistoryResponse, error) {
	return nil, nil
}

func (g *lifecycleGateway) GetPolicyStatus(context.Context, string) (*pb.GetSandboxPolicyStatusResponse, error) {
	return nil, nil
}

func (g *lifecycleGateway) ListPolicyRevisions(context.Context, string) ([]*pb.SandboxPolicyRevision, error) {
	return nil, nil
}

func (g *lifecycleGateway) Close() error {
	g.closeCalls.Add(1)
	return nil
}

type scriptedEventStream struct {
	events []*pb.SandboxStreamEvent
	next   int
}

func (s *scriptedEventStream) Recv() (*pb.SandboxStreamEvent, error) {
	if s.next >= len(s.events) {
		return nil, io.EOF
	}
	event := s.events[s.next]
	s.next++
	return event, nil
}

type blockingEventStream struct {
	ctx context.Context
}

func (s blockingEventStream) Recv() (*pb.SandboxStreamEvent, error) {
	<-s.ctx.Done()
	return nil, s.ctx.Err()
}

func TestReceiverLifecycleFiltersDiscoveryAndSurfacesStreamGap(t *testing.T) {
	gateway := &lifecycleGateway{sandboxes: []sandboxRef{
		{ID: "sandbox-allowed", Name: "allowed"},
		{ID: "sandbox-denied", Name: "denied"},
	}}
	config := createDefaultConfig().(*Config)
	config.TokenEnv = ""
	config.SandboxNames = []string{"allowed"}
	config.DiscoveryInterval = time.Hour
	config.ReconnectInitial = time.Millisecond
	config.ReconnectMax = 2 * time.Millisecond

	kinds := make(chan string, 4)
	next, err := consumer.NewLogs(func(_ context.Context, logs plog.Logs) error {
		record := logs.ResourceLogs().At(0).ScopeLogs().At(0).LogRecords().At(0)
		kind, _ := record.Attributes().Get("openshell.acquisition.kind")
		kinds <- kind.Str()
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	receiver := newWatchReceiver(config, lifecycleSettings(), next)
	receiver.newClient = func(*Config) (gatewayClient, error) { return gateway, nil }
	if err := receiver.Start(context.Background(), lifecycleHost{}); err != nil {
		t.Fatal(err)
	}

	wantKinds := map[string]bool{"sandbox.lifecycle": false, "stream.warning": false}
	deadline := time.NewTimer(2 * time.Second)
	defer deadline.Stop()
	for !wantKinds["sandbox.lifecycle"] || !wantKinds["stream.warning"] {
		select {
		case kind := <-kinds:
			if _, ok := wantKinds[kind]; ok {
				wantKinds[kind] = true
			}
		case <-deadline.C:
			t.Fatalf("timed out waiting for lifecycle and gap evidence: %#v", wantKinds)
		}
	}

	shutdownContext, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	if err := receiver.Shutdown(shutdownContext); err != nil {
		t.Fatal(err)
	}
	if gateway.listCalls.Load() == 0 {
		t.Fatal("sandbox discovery was not attempted")
	}
	if gateway.closeCalls.Load() != 1 {
		t.Fatalf("gateway close calls=%d, want 1", gateway.closeCalls.Load())
	}
	gateway.watchMu.Lock()
	defer gateway.watchMu.Unlock()
	for _, sandboxID := range gateway.watchedIDs {
		if sandboxID != "allowed" {
			t.Fatalf("unauthorized sandbox was watched: %q", sandboxID)
		}
	}
}

func TestReceiverStartFailsClosedAndReleasesClient(t *testing.T) {
	config := createDefaultConfig().(*Config)
	config.TokenEnv = ""
	receiver := newWatchReceiver(config, lifecycleSettings(), nil)
	receiver.newClient = func(*Config) (gatewayClient, error) {
		return nil, errors.New("client construction failed")
	}
	if err := receiver.Start(context.Background(), lifecycleHost{}); err == nil {
		t.Fatal("receiver start accepted a failed client")
	}

	storageID := component.NewID(component.MustNewType("file_storage"))
	config.PolicyReconciliation.Enabled = true
	config.PolicyReconciliation.StorageID = &storageID
	gateway := &lifecycleGateway{}
	receiver = newWatchReceiver(config, lifecycleSettings(), nil)
	receiver.newClient = func(*Config) (gatewayClient, error) { return gateway, nil }
	if err := receiver.Start(context.Background(), lifecycleHost{}); err == nil {
		t.Fatal("receiver start accepted missing policy storage")
	}
	if gateway.closeCalls.Load() != 1 {
		t.Fatalf("failed start close calls=%d, want 1", gateway.closeCalls.Load())
	}
}

func TestCanceledDeliveryDoesNotCallDownstream(t *testing.T) {
	var calls atomic.Int32
	next, err := consumer.NewLogs(func(context.Context, plog.Logs) error {
		calls.Add(1)
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	r := newWatchReceiver(createDefaultConfig().(*Config), lifecycleSettings(), next)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if err := r.consumeWithBackpressure(ctx, plog.NewLogs()); !errors.Is(err, context.Canceled) {
		t.Fatalf("delivery error = %v, want cancellation", err)
	}
	if calls.Load() != 0 {
		t.Fatal("canceled delivery reached downstream")
	}
}

func TestWatchPermanentRejectionReportsGap(t *testing.T) {
	reader := sdkmetric.NewManualReader()
	provider := sdkmetric.NewMeterProvider(sdkmetric.WithReader(reader))
	t.Cleanup(func() { _ = provider.Shutdown(context.Background()) })
	gateway := &lifecycleGateway{sandboxes: []sandboxRef{{ID: "sandbox", Name: "allowed"}}}
	config := createDefaultConfig().(*Config)
	config.DiscoveryInterval = time.Hour
	config.ReconnectInitial = time.Hour
	config.ReconnectMax = time.Hour
	next, err := consumer.NewLogs(func(context.Context, plog.Logs) error {
		return consumererror.NewPermanent(errors.New("rejected"))
	})
	if err != nil {
		t.Fatal(err)
	}
	settings := lifecycleSettings()
	settings.MeterProvider = provider
	r := newWatchReceiver(config, settings, next)
	r.newClient = func(*Config) (gatewayClient, error) { return gateway, nil }
	if err := r.Start(context.Background(), lifecycleHost{}); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = r.Shutdown(context.Background()) })
	deadline := time.Now().Add(time.Second)
	for {
		var found bool
		for _, m := range collectMetrics(t, reader) {
			if m.Name != "openshell.exporter.stream.gap_warnings" {
				continue
			}
			for _, point := range m.Data.(metricdata.Sum[int64]).DataPoints {
				reason, _ := point.Attributes.Value("reason")
				if reason.AsString() == "downstream_permanent_rejection" && point.Value == 1 {
					found = true
				}
			}
		}
		if found {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("permanently rejected source event did not report a gap")
		}
		time.Sleep(time.Millisecond)
	}
}

// Each stream offers an unlimited sequence. A saturated downstream must leave
// exactly one record per watch in flight, not keep draining into local buffers.
type loadGateway struct {
	lifecycleGateway
	reads atomic.Int32
}

func (g *loadGateway) Watch(ctx context.Context, id string) (eventStream, error) {
	g.watchCalls.Add(1)
	return &loadStream{ctx: ctx, id: id, reads: &g.reads}, nil
}

type loadStream struct {
	ctx   context.Context
	id    string
	reads *atomic.Int32
}

func (s *loadStream) Recv() (*pb.SandboxStreamEvent, error) {
	if err := s.ctx.Err(); err != nil {
		return nil, err
	}
	s.reads.Add(1)
	return &pb.SandboxStreamEvent{Payload: &pb.SandboxStreamEvent_Sandbox{Sandbox: &pb.Sandbox{
		Metadata: &datamodelv1.ObjectMeta{Id: s.id, Name: s.id, ResourceVersion: 1},
	}}}, nil
}

func TestSaturatedWatchesBoundReadsAndCancelUnderLoad(t *testing.T) {
	for _, slow := range []bool{false, true} {
		t.Run(fmt.Sprintf("blocking=%t", slow), func(t *testing.T) {
			const watches = 32
			gateway := &loadGateway{}
			for i := range watches {
				gateway.sandboxes = append(gateway.sandboxes, sandboxRef{ID: fmt.Sprint(i), Name: fmt.Sprint(i)})
			}
			reader := sdkmetric.NewManualReader()
			provider := sdkmetric.NewMeterProvider(sdkmetric.WithReader(reader))
			t.Cleanup(func() { _ = provider.Shutdown(context.Background()) })
			settings := lifecycleSettings()
			settings.MeterProvider = provider
			entered := make(chan struct{}, watches*2)
			next, err := consumer.NewLogs(func(ctx context.Context, _ plog.Logs) error {
				entered <- struct{}{}
				if slow {
					<-ctx.Done()
					return ctx.Err()
				}
				return errors.New("bounded queue full")
			})
			if err != nil {
				t.Fatal(err)
			}
			config := createDefaultConfig().(*Config)
			config.DiscoveryInterval = time.Hour
			r := newWatchReceiver(config, settings, next)
			r.newClient = func(*Config) (gatewayClient, error) { return gateway, nil }
			if err := r.Start(context.Background(), lifecycleHost{}); err != nil {
				t.Fatal(err)
			}
			t.Cleanup(func() { _ = r.Shutdown(context.Background()) })
			ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
			defer cancel()
			for range watches {
				select {
				case <-entered:
				case <-ctx.Done():
					t.Fatal("watches did not saturate downstream")
				}
			}
			// Long enough to catch an accidental read-ahead loop, shorter than retry backoff.
			time.Sleep(50 * time.Millisecond)
			if got := gateway.reads.Load(); got != watches {
				t.Fatalf("reads=%d, want bounded %d", got, watches)
			}
			if !slow {
				m := collectMetrics(t, reader)
				assertIntSum(t, m, "openshell.exporter.stream.gap_warnings", watches, "reason", "downstream_backpressure")
				active := findMetric(t, m, "openshell.exporter.stream.backpressure_active").Data.(metricdata.Sum[int64])
				if len(active.DataPoints) != 1 || active.DataPoints[0].Value != watches {
					t.Fatalf("active retries: %#v", active)
				}
			}
			shutdown, stop := context.WithTimeout(context.Background(), time.Second)
			defer stop()
			if err := r.Shutdown(shutdown); err != nil {
				t.Fatal(err)
			}
			if gateway.closeCalls.Load() != 1 {
				t.Fatal("gateway was not closed exactly once")
			}
			m := collectMetrics(t, reader)
			assertIntSum(t, m, "openshell.exporter.stream.unacknowledged_records", watches, "reason", "canceled")
			if !slow {
				active := findMetric(t, m, "openshell.exporter.stream.backpressure_active").Data.(metricdata.Sum[int64])
				if active.DataPoints[0].Value != 0 {
					t.Fatalf("active retries after shutdown: %#v", active)
				}
			}
		})
	}
}

func TestReconnectJitterRemainsBoundedAtCap(t *testing.T) {
	for _, delay := range []time.Duration{10 * time.Millisecond, time.Second, 10 * time.Second} {
		const maximum = time.Second
		unique := map[time.Duration]bool{}
		for range 100 {
			got := reconnectDelay(delay, maximum)
			if got < min(delay, maximum)/2 || got > min(delay, maximum) {
				t.Fatalf("delay %v produced %v", delay, got)
			}
			unique[got] = true
		}
		if len(unique) < 2 {
			t.Fatalf("jitter disappeared at delay=%v", delay)
		}
	}
	r := newWatchReceiver(createDefaultConfig().(*Config), lifecycleSettings(), nil)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if r.waitReconnect(ctx, time.Hour) {
		t.Fatal("canceled reconnect waited")
	}
}

type startupGateway struct {
	lifecycleGateway
	entered chan struct{}
}

func (g *startupGateway) GatewayVersion(ctx context.Context) (string, error) {
	close(g.entered)
	<-ctx.Done()
	return "", ctx.Err()
}
func TestCanceledStartupStopsVersionEnrichment(t *testing.T) {
	gateway := &startupGateway{entered: make(chan struct{})}
	r := newWatchReceiver(createDefaultConfig().(*Config), lifecycleSettings(), nil)
	r.newClient = func(*Config) (gatewayClient, error) { return gateway, nil }
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan error, 1)
	go func() { done <- r.Start(ctx, lifecycleHost{}) }()
	select {
	case <-gateway.entered:
	case <-time.After(time.Second):
		t.Fatal("version lookup did not start")
	}
	cancel()
	select {
	case err := <-done:
		if !errors.Is(err, context.Canceled) {
			t.Fatalf("startup error=%v", err)
		}
	case <-time.After(time.Second):
		t.Fatal("startup ignored cancellation")
	}
	if gateway.closeCalls.Load() != 1 || gateway.listCalls.Load() != 0 {
		t.Fatal("canceled startup did not release the client before discovery")
	}
}

type slowClosingStorage struct {
	*memoryPolicyStorage
	entered chan struct{}
}

func (s *slowClosingStorage) Close(ctx context.Context) error {
	close(s.entered)
	<-ctx.Done()
	return ctx.Err()
}
func TestShutdownCancelsStorageCloseAndReleasesGateway(t *testing.T) {
	r := newWatchReceiver(createDefaultConfig().(*Config), lifecycleSettings(), nil)
	gateway := &lifecycleGateway{}
	store := &slowClosingStorage{memoryPolicyStorage: newMemoryPolicyStorage(), entered: make(chan struct{})}
	r.client = gateway
	r.policyStorage = store
	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancel()
	if err := r.Shutdown(ctx); !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("shutdown error=%v", err)
	}
	select {
	case <-store.entered:
	default:
		t.Fatal("storage was not closed")
	}
	select {
	case <-r.shutdownDone:
	case <-time.After(time.Second):
		t.Fatal("storage close did not receive cancellation")
	}
	if gateway.closeCalls.Load() != 1 {
		t.Fatal("storage close prevented gateway cleanup")
	}
	if err := r.Shutdown(context.Background()); !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("storage close error was lost: %v", err)
	}
}

func TestGatewayMinimumVersion(t *testing.T) {
	for _, version := range []string{"0.1.0", "v0.1.0", "0.1.2", "1.0.0", "0.1.0+build.1"} {
		if err := validateGatewayVersion(version); err != nil {
			t.Errorf("%s: %v", version, err)
		}
	}
	for _, version := range []string{"", "dev", "0.0.113", "0.1.0-rc.1", "not-a-version"} {
		if err := validateGatewayVersion(version); err == nil {
			t.Errorf("accepted unsupported version %q", version)
		}
	}
}

func TestUnsupportedGatewayFailsBeforeDiscovery(t *testing.T) {
	for _, tt := range []struct {
		name, version string
		err           error
	}{
		{name: "old", version: "0.0.113"},
		{name: "malformed", version: "unknown"},
		{name: "unavailable", err: errors.New("version unavailable")},
	} {
		t.Run(tt.name, func(t *testing.T) {
			gateway := &lifecycleGateway{version: tt.version, versionError: tt.err}
			r := newWatchReceiver(createDefaultConfig().(*Config), lifecycleSettings(), nil)
			r.newClient = func(*Config) (gatewayClient, error) { return gateway, nil }
			if err := r.Start(context.Background(), lifecycleHost{}); err == nil {
				t.Fatal("unsupported gateway started")
			}
			if gateway.listCalls.Load() != 0 || gateway.watchCalls.Load() != 0 {
				t.Fatal("unsupported gateway was queried")
			}
			if gateway.closeCalls.Load() != 1 {
				t.Fatal("client was not closed exactly once")
			}
		})
	}
}
