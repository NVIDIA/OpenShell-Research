// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package watchsandboxreceiver

import (
	"context"
	"errors"
	"fmt"
	"io"
	"math/rand/v2"
	"slices"
	"sync"
	"sync/atomic"
	"time"

	pb "github.com/NVIDIA/OpenShell/sdk/go/proto/openshellv1"
	"go.opentelemetry.io/collector/component"
	"go.opentelemetry.io/collector/consumer"
	"go.opentelemetry.io/collector/consumer/consumererror"
	"go.opentelemetry.io/collector/extension/xextension/storage"
	"go.opentelemetry.io/collector/pdata/plog"
	"go.opentelemetry.io/collector/receiver"
	"go.uber.org/zap"
)

type watchReceiver struct {
	config    *Config
	settings  receiver.Settings
	next      consumer.Logs
	newClient clientFactory

	client         gatewayClient
	gatewayVersion string
	cancel         context.CancelFunc

	discoveryWG sync.WaitGroup
	watchWG     sync.WaitGroup

	mu        sync.Mutex
	watching  map[string]context.CancelFunc
	sandboxes map[string]sandboxRef

	policyStorage    storage.Client
	reconcileCh      chan reconciliationRequest
	reconcileWG      sync.WaitGroup
	reconcileMu      sync.Mutex
	reconcilePending map[string]struct{}
	policyUnhealthy  atomic.Bool

	shutdownOnce sync.Once
	shutdownDone chan struct{}
	shutdownErr  error
	metrics      *watchMetrics
}

func newWatchReceiver(
	config *Config,
	settings receiver.Settings,
	next consumer.Logs,
) *watchReceiver {
	return &watchReceiver{
		config:       config,
		settings:     settings,
		next:         next,
		newClient:    newGRPCGatewayClient,
		watching:     make(map[string]context.CancelFunc),
		sandboxes:    make(map[string]sandboxRef),
		shutdownDone: make(chan struct{}),
		metrics:      newWatchMetrics(settings.MeterProvider),
	}
}

func (r *watchReceiver) Start(ctx context.Context, host component.Host) error {
	client, err := r.newClient(r.config)
	if err != nil {
		return err
	}
	r.client = client
	runContext, cancel := context.WithCancel(context.Background())
	r.cancel = cancel
	if err := r.startPolicyReconciliation(runContext, host); err != nil {
		cancel()
		_ = client.Close()
		return err
	}
	r.metrics.enabled(ctx)
	r.settings.Logger.Info(
		"OpenShell source capability enabled",
		zap.String("source", "watchsandbox"),
		zap.String("durability", "non_resumable"),
		zap.Bool("status", true),
		zap.Bool("logs", true),
		zap.Bool("events", true),
		zap.Bool("watch_events_rpc", false),
	)

	infoContext, cancelInfo := context.WithTimeout(context.Background(), 5*time.Second)
	r.gatewayVersion, err = client.GatewayVersion(infoContext)
	cancelInfo()
	if err != nil {
		r.settings.Logger.Warn(
			"gateway version enrichment unavailable",
			zap.Error(err),
		)
	}

	r.discoveryWG.Add(1)
	go r.discoveryLoop(runContext)
	return nil
}

func (r *watchReceiver) Shutdown(ctx context.Context) error {
	r.shutdownOnce.Do(func() {
		if r.cancel != nil {
			r.cancel()
		}
		go r.finishShutdown()
	})
	select {
	case <-r.shutdownDone:
		return r.shutdownErr
	case <-ctx.Done():
		return ctx.Err()
	}
}

func (r *watchReceiver) finishShutdown() {
	r.discoveryWG.Wait()
	r.watchWG.Wait()
	r.reconcileWG.Wait()
	var shutdownErrors []error
	if r.policyStorage != nil {
		shutdownErrors = append(shutdownErrors, r.policyStorage.Close(context.Background()))
	}
	if r.client != nil {
		shutdownErrors = append(shutdownErrors, r.client.Close())
	}
	r.shutdownErr = errors.Join(shutdownErrors...)
	close(r.shutdownDone)
}

func (r *watchReceiver) discoveryLoop(ctx context.Context) {
	defer r.discoveryWG.Done()
	ticker := time.NewTicker(r.config.DiscoveryInterval)
	defer ticker.Stop()
	for {
		r.discover(ctx)
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
	}
}

func (r *watchReceiver) discover(ctx context.Context) {
	sandboxes, err := r.client.List(ctx)
	if err != nil {
		r.metrics.discoveryFailed(ctx)
		if !errors.Is(err, context.Canceled) {
			r.settings.Logger.Warn("sandbox discovery failed", zap.Error(err))
		}
		return
	}
	present := make(map[string]struct{}, len(sandboxes))
	for _, sandbox := range sandboxes {
		if len(r.config.SandboxNames) > 0 &&
			!slices.Contains(r.config.SandboxNames, sandbox.Name) {
			continue
		}
		present[sandbox.ID] = struct{}{}
		watchContext, cancel := context.WithCancel(ctx)
		r.mu.Lock()
		_, exists := r.watching[sandbox.ID]
		r.sandboxes[sandbox.ID] = sandbox
		if !exists {
			r.watching[sandbox.ID] = cancel
		}
		r.mu.Unlock()
		if exists {
			cancel()
			continue
		}
		r.enqueuePolicyReconciliation(ctx, sandbox, "discovery", 0)
		r.watchWG.Add(1)
		go func() {
			defer cancel()
			r.watch(watchContext, sandbox)
		}()
	}
	// Only a successful complete discovery can retire a sandbox. Transient list
	// failures leave existing watches and checkpoints intact.
	r.mu.Lock()
	for id := range r.sandboxes {
		if _, exists := present[id]; !exists {
			delete(r.sandboxes, id)
			if cancel, watching := r.watching[id]; watching {
				cancel()
			}
		}
	}
	r.mu.Unlock()
}

func (r *watchReceiver) watch(ctx context.Context, sandbox sandboxRef) {
	defer r.watchWG.Done()
	defer func() {
		r.mu.Lock()
		if cancel, exists := r.watching[sandbox.ID]; exists {
			cancel()
		}
		delete(r.watching, sandbox.ID)
		r.mu.Unlock()
	}()

	backoff := r.config.ReconnectInitial
	for {
		if ctx.Err() != nil {
			return
		}
		stream, err := r.client.Watch(ctx, sandbox.ID)
		if err != nil {
			r.metrics.reconnect(ctx, "stream_open_failed")
			r.emitWarning(
				ctx,
				sandbox,
				"stream_open_failed",
				fmt.Sprintf("WatchSandbox open failed: %v", err),
			)
			if !r.waitReconnect(ctx, backoff) {
				return
			}
			backoff = min(backoff*2, r.config.ReconnectMax)
			continue
		}

		r.enqueuePolicyReconciliation(ctx, sandbox, "stream_connected", 0)
		received := false
		for {
			event, recvErr := stream.Recv()
			if recvErr != nil {
				if errors.Is(recvErr, context.Canceled) || ctx.Err() != nil {
					return
				}
				code := "stream_receive_failed"
				if errors.Is(recvErr, io.EOF) {
					code = "stream_closed"
				}
				r.metrics.reconnect(ctx, code)
				r.emitWarning(
					ctx,
					sandbox,
					code,
					fmt.Sprintf("WatchSandbox interrupted: %v", recvErr),
				)
				break
			}
			logs, convertErr := convertStreamEvent(
				r.config,
				sandbox,
				r.gatewayVersion,
				event,
			)
			if convertErr != nil {
				r.emitWarning(
					ctx,
					sandbox,
					"conversion_failed",
					convertErr.Error(),
				)
				continue
			}
			received = true
			r.metrics.succeeded(ctx)
			deliveryErr := r.consumeWithBackpressure(ctx, logs)
			if deliveryErr != nil {
				if errors.Is(deliveryErr, context.Canceled) {
					return
				}
				r.settings.Logger.Warn(
					"sandbox event delivery failed",
					zap.Error(deliveryErr),
				)
			}
			if deliveryErr == nil {
				if payload, ok := event.GetPayload().(*pb.SandboxStreamEvent_DraftPolicyUpdate); ok && payload.DraftPolicyUpdate != nil {
					r.enqueuePolicyReconciliation(ctx, sandbox, "draft_notification", payload.DraftPolicyUpdate.GetDraftVersion())
				}
			}
		}
		if received {
			backoff = r.config.ReconnectInitial
		}
		if !r.waitReconnect(ctx, backoff) {
			return
		}
		backoff = min(backoff*2, r.config.ReconnectMax)
	}
}

func (r *watchReceiver) emitWarning(
	ctx context.Context,
	sandbox sandboxRef,
	code string,
	message string,
) {
	r.metrics.gap(ctx, code)
	logs, err := warningLogs(
		r.config,
		sandbox,
		r.gatewayVersion,
		code,
		message,
	)
	if err != nil {
		r.settings.Logger.Error(
			"construct stream warning evidence",
			zap.Error(err),
		)
		return
	}
	if err := r.consumeWithBackpressure(ctx, logs); err != nil &&
		!errors.Is(err, context.Canceled) {
		r.settings.Logger.Warn(
			"stream warning delivery failed",
			zap.Error(err),
		)
	}
}

func (r *watchReceiver) waitReconnect(ctx context.Context, delay time.Duration) bool {
	jitter := time.Duration(rand.Int64N(int64(delay/2) + 1))
	timer := time.NewTimer(min(delay+jitter, r.config.ReconnectMax))
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return false
	case <-timer.C:
		return true
	}
}

func (r *watchReceiver) consumeWithBackpressure(
	ctx context.Context,
	logs plog.Logs,
) error {
	delay := time.Second
	for {
		if err := r.next.ConsumeLogs(ctx, logs); err == nil {
			return nil
		} else if consumererror.IsPermanent(err) {
			return err
		}
		r.metrics.backpressured(ctx)
		timer := time.NewTimer(delay)
		select {
		case <-ctx.Done():
			if !timer.Stop() {
				<-timer.C
			}
			return ctx.Err()
		case <-timer.C:
		}
		delay = min(delay*2, 30*time.Second)
	}
}
