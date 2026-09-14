// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package watchsandboxreceiver

import (
	"context"
	"errors"
	"reflect"
	"testing"

	"github.com/NVIDIA/OpenShell-Research/projects/tools/openshell-exporter/processor/openshellprocessor"
	"go.opentelemetry.io/collector/consumer"
	"go.opentelemetry.io/collector/consumer/consumererror"
	"go.opentelemetry.io/collector/pdata/plog"
	"go.opentelemetry.io/collector/processor/processortest"
)

func withNormalizer(t *testing.T, consume func(context.Context, plog.Logs) error) consumer.Logs {
	t.Helper()
	sink, err := consumer.NewLogs(consume)
	if err != nil {
		t.Fatal(err)
	}
	factory := openshellprocessor.NewFactory()
	cfg := factory.CreateDefaultConfig().(*openshellprocessor.Config)
	cfg.SourceProfiles = []string{"watchsandbox", "policy.reconciliation"}
	normalizer, err := factory.CreateLogs(context.Background(), processortest.NewNopSettings(factory.Type()), cfg, sink)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if err := normalizer.Shutdown(context.Background()); err != nil {
			t.Error(err)
		}
	})
	return normalizer
}

func TestReceiverRetryPreservesNormalizedBodyAndID(t *testing.T) {
	cfg := createDefaultConfig().(*Config)
	var bodies []any
	var ids []string
	next := withNormalizer(t, func(_ context.Context, logs plog.Logs) error {
		record := logs.ResourceLogs().At(0).ScopeLogs().At(0).LogRecords().At(0)
		bodies = append(bodies, record.Body().AsRaw())
		id, _ := record.Attributes().Get("cloudevents.id")
		ids = append(ids, id.Str())
		if len(bodies) == 1 {
			return errors.New("temporary downstream failure")
		}
		return nil
	})
	r := newWatchReceiver(cfg, lifecycleSettings(), next)
	logs, err := draftSnapshotLogs(cfg, testReconciliationRequest().Sandbox, "test", testReconciliationGateway().draft, reconciliationMetadata{})
	if err != nil {
		t.Fatal(err)
	}
	before, err := (&plog.JSONMarshaler{}).MarshalLogs(logs)
	if err != nil {
		t.Fatal(err)
	}
	if err := r.consumeWithBackpressure(context.Background(), logs); err != nil {
		t.Fatal(err)
	}
	after, err := (&plog.JSONMarshaler{}).MarshalLogs(logs)
	if err != nil {
		t.Fatal(err)
	}
	if string(before) != string(after) {
		t.Error("receiver-owned evidence mutated")
	}
	if len(bodies) != 2 || !reflect.DeepEqual(bodies[0], bodies[1]) || ids[0] == "" || ids[0] != ids[1] {
		t.Error("retry changed normalized evidence or identity")
	}
}

func TestReconciliationWithNormalizerPersistsOnlyAcceptedSnapshots(t *testing.T) {
	for _, rejectFirst := range []bool{false, true} {
		t.Run(map[bool]string{false: "accepted", true: "rejected"}[rejectFirst], func(t *testing.T) {
			cfg := createDefaultConfig().(*Config)
			cfg.PolicyReconciliation.Enabled = true
			cfg.PolicyReconciliation.IncludeHistory = true
			store := newMemoryPolicyStorage()
			gateway := testReconciliationGateway()
			calls := 0
			reject := rejectFirst
			next := withNormalizer(t, func(context.Context, plog.Logs) error {
				calls++
				if reject {
					return consumererror.NewPermanent(errors.New("rejected"))
				}
				return nil
			})
			makeReceiver := func() *watchReceiver {
				r := newWatchReceiver(cfg, lifecycleSettings(), next)
				r.client = gateway
				r.gatewayVersion = "test"
				r.policyStorage = store
				return r
			}
			r := makeReceiver()
			request := testReconciliationRequest()
			r.reconcileSandbox(context.Background(), request)
			if calls == 0 {
				t.Fatal("no evidence delivered")
			}
			if reject {
				state, err := r.loadPolicyState(context.Background(), request.Sandbox.ID)
				if err != nil {
					t.Fatal(err)
				}
				if state.DraftHash != "" || state.StatusHash != "" {
					t.Fatal("rejected snapshots checkpointed")
				}
				reject = false
				before := calls
				r.reconcileSandbox(context.Background(), request)
				if calls <= before {
					t.Fatal("rejected snapshots not retried")
				}
			}
			before := calls
			r.reconcileSandbox(context.Background(), request)
			makeReceiver().reconcileSandbox(context.Background(), request)
			if calls != before {
				t.Fatalf("unchanged reconciliation emitted %d extra records", calls-before)
			}
		})
	}
}
