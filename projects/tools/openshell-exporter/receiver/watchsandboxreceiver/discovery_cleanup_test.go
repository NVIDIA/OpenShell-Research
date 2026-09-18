// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package watchsandboxreceiver

import (
	"context"
	"errors"
	"testing"
	"time"

	"go.opentelemetry.io/collector/consumer"
	"go.opentelemetry.io/collector/pdata/plog"
)

type changingDiscoveryGateway struct {
	*lifecycleGateway
	current []sandboxRef
	listErr error
}

func (g *changingDiscoveryGateway) List(context.Context) ([]sandboxRef, error) {
	return g.current, g.listErr
}

func TestDiscoveryRetiresDeletedSandboxesButNotTransientFailures(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	gateway := &changingDiscoveryGateway{lifecycleGateway: &lifecycleGateway{}}
	// Use the fixture's blocking stream so cancellation is observable.
	gateway.watchCalls.Store(1)
	next, err := consumer.NewLogs(func(context.Context, plog.Logs) error { return nil })
	if err != nil {
		t.Fatal(err)
	}
	r := newWatchReceiver(createDefaultConfig().(*Config), lifecycleSettings(), next)
	r.client = gateway
	for range 10 {
		gateway.current = []sandboxRef{{ID: "sandbox", Name: "sandbox"}}
		r.discover(ctx)
		gateway.current = nil
		gateway.listErr = errors.New("temporary discovery failure")
		r.discover(ctx)
		r.mu.Lock()
		retained := len(r.sandboxes) == 1 && len(r.watching) == 1
		r.mu.Unlock()
		if !retained {
			t.Fatal("failed discovery retired a live watch")
		}
		gateway.listErr = nil
		r.discover(ctx)
		done := make(chan struct{})
		go func() { r.watchWG.Wait(); close(done) }()
		select {
		case <-done:
		case <-time.After(2 * time.Second):
			t.Fatal("deleted sandbox watch did not stop")
		}
		r.mu.Lock()
		empty := len(r.sandboxes) == 0 && len(r.watching) == 0
		r.mu.Unlock()
		if !empty {
			t.Fatal("deleted sandbox retained discovery or watch state")
		}
	}
}

func TestReconciliationSkipsQueuedDeletedSandbox(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	r := newWatchReceiver(createDefaultConfig().(*Config), lifecycleSettings(), nil)
	r.reconcileCh = make(chan reconciliationRequest, 1)
	r.reconcilePending = map[string]struct{}{"deleted": {}}
	r.reconcileCh <- reconciliationRequest{Sandbox: sandboxRef{ID: "deleted"}}
	// No gateway or storage: stale work must be discarded before either is used.
	r.reconcileWG.Add(1)
	go r.policyReconciliationWorker(ctx)
	deadline := time.After(2 * time.Second)
	for {
		r.reconcileMu.Lock()
		pending := len(r.reconcilePending)
		r.reconcileMu.Unlock()
		if pending == 0 {
			break
		}
		select {
		case <-deadline:
			t.Fatal("stale reconciliation work was not cleared")
		case <-time.After(time.Millisecond):
		}
	}
	cancel()
	r.reconcileWG.Wait()
}
