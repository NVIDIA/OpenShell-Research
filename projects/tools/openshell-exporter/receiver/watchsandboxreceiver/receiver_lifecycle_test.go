// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package watchsandboxreceiver

import (
	"context"
	"errors"
	"io"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/NVIDIA/OpenShell/sdk/go/proto/datamodelv1"
	pb "github.com/NVIDIA/OpenShell/sdk/go/proto/openshellv1"
	"go.opentelemetry.io/collector/component"
	"go.opentelemetry.io/collector/consumer"
	"go.opentelemetry.io/collector/pdata/plog"
	"go.opentelemetry.io/collector/receiver"
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
	return "0.0.test", g.versionError
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
		if sandboxID != "sandbox-allowed" {
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
