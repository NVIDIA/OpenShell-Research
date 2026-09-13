// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package watchsandboxreceiver

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/NVIDIA/OpenShell/sdk/go/proto/datamodelv1"
	pb "github.com/NVIDIA/OpenShell/sdk/go/proto/openshellv1"
	"go.opentelemetry.io/collector/consumer"
	"go.opentelemetry.io/collector/consumer/consumererror"
	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/plog"
	"go.opentelemetry.io/collector/receiver"
)

func TestEveryPublicWatchSandboxVariantConverts(t *testing.T) {
	config := createDefaultConfig().(*Config)
	ref := sandboxRef{ID: "sandbox-id", Name: "sandbox-name"}
	tests := map[string]struct {
		event *pb.SandboxStreamEvent
		kind  string
	}{
		"lifecycle": {
			event: &pb.SandboxStreamEvent{
				Payload: &pb.SandboxStreamEvent_Sandbox{
					Sandbox: &pb.Sandbox{
						Metadata: &datamodelv1.ObjectMeta{
							Id:              "sandbox-id",
							Name:            "sandbox-name",
							Workspace:       "default",
							ResourceVersion: 7,
						},
						Status: &pb.SandboxStatus{CurrentPolicyVersion: 3},
					},
				},
			},
			kind: "sandbox.lifecycle",
		},
		"gateway log": {
			event: &pb.SandboxStreamEvent{
				Payload: &pb.SandboxStreamEvent_Log{
					Log: &pb.SandboxLogLine{
						SandboxId:   "sandbox-id",
						TimestampMs: 1_700_000_000_000,
						Source:      "gateway",
						Message:     "allowed",
					},
				},
			},
			kind: "gateway.log",
		},
		"sandbox log": {
			event: &pb.SandboxStreamEvent{
				Payload: &pb.SandboxStreamEvent_Log{
					Log: &pb.SandboxLogLine{
						SandboxId: "sandbox-id",
						Source:    "sandbox",
						Message:   "supervisor ready",
					},
				},
			},
			kind: "sandbox.log",
		},
		"platform event": {
			event: &pb.SandboxStreamEvent{
				Payload: &pb.SandboxStreamEvent_Event{
					Event: &pb.PlatformEvent{
						TimestampMs: 1_700_000_000_001,
						Source:      "kubernetes",
						Type:        "Warning",
						Reason:      "BackOff",
					},
				},
			},
			kind: "platform.event",
		},
		"draft update": {
			event: &pb.SandboxStreamEvent{
				Payload: &pb.SandboxStreamEvent_DraftPolicyUpdate{
					DraftPolicyUpdate: &pb.DraftPolicyUpdate{
						DraftVersion: 8,
						NewChunks:    1,
						TotalPending: 2,
						Summary:      "network evidence",
					},
				},
			},
			kind: "policy.draft_updated",
		},
		"warning": {
			event: &pb.SandboxStreamEvent{
				Payload: &pb.SandboxStreamEvent_Warning{
					Warning: &pb.SandboxStreamWarning{Message: "lagged"},
				},
			},
			kind: "stream.warning",
		},
	}
	for name, test := range tests {
		t.Run(name, func(t *testing.T) {
			logs, err := convertStreamEvent(config, ref, "v1.2.3", test.event)
			if err != nil {
				t.Fatal(err)
			}
			if logs.LogRecordCount() != 1 {
				t.Fatalf("record count=%d", logs.LogRecordCount())
			}
			record := logs.ResourceLogs().
				At(0).
				ScopeLogs().
				At(0).
				LogRecords().
				At(0)
			kind, _ := record.Attributes().Get("openshell.acquisition.kind")
			if kind.Str() != test.kind {
				t.Fatalf("kind=%q want=%q", kind.Str(), test.kind)
			}
			if record.ObservedTimestamp() == 0 {
				t.Fatal("observed time missing")
			}
			for key, expected := range map[string]any{
				"openshell.acquisition.transport":        "grpc",
				"openshell.acquisition.api_operation":    "openshell.v1.OpenShell/WatchSandbox",
				"openshell.acquisition.stream_resumable": false,
				"openshell.acquisition.follow_status":    true,
				"openshell.acquisition.follow_logs":      true,
				"openshell.acquisition.follow_events":    true,
				"openshell.acquisition.stop_on_terminal": false,
				"openshell.acquisition.log_since_ms":     int64(0),
				"openshell.acquisition.log_min_level":    "",
				"openshell.acquisition.log_tail_lines":   int64(config.LogTailLines),
				"openshell.acquisition.event_tail":       int64(config.EventTail),
			} {
				value, ok := record.Attributes().Get(key)
				if !ok || value.AsRaw() != expected {
					t.Fatalf("%s=%#v, want %#v", key, value.AsRaw(), expected)
				}
			}
			logSources, ok := record.Attributes().Get("openshell.acquisition.log_sources")
			if !ok || logSources.Slice().Len() != 2 ||
				logSources.Slice().At(0).Str() != "gateway" ||
				logSources.Slice().At(1).Str() != "sandbox" {
				t.Fatalf("log sources=%#v", logSources.AsRaw())
			}
			payloadType, ok := record.Attributes().Get("openshell.acquisition.payload_type")
			if !ok || !strings.HasPrefix(payloadType.Str(), "openshell.v1.") {
				t.Fatalf("payload type=%q", payloadType.Str())
			}
			sourcePayload, ok := record.Body().Map().Get("source_payload")
			if !ok || sourcePayload.Type() != pcommon.ValueTypeMap {
				t.Fatal("full public source payload is missing")
			}
		})
	}
}

func TestLifecyclePreservesCompletePublicPayload(t *testing.T) {
	config := createDefaultConfig().(*Config)
	logs, err := lifecycleLogs(config, "v0.0.113", &pb.Sandbox{
		Metadata: &datamodelv1.ObjectMeta{
			Id:                  "sandbox-id",
			Name:                "sandbox-name",
			CreatedAtMs:         1_700_000_000_000,
			Labels:              map[string]string{"team": "security"},
			ResourceVersion:     19,
			Annotations:         map[string]string{"trace.id": "trace-from-annotation"},
			Workspace:           "default",
			DeletionTimestampMs: 1_700_000_100_000,
		},
		Spec: &pb.SandboxSpec{
			LogLevel:    "debug",
			Environment: map[string]string{"authorization": "Bearer source-secret"},
			Template: &pb.SandboxTemplate{
				Image:       "example.invalid/agent@sha256:abc",
				AgentSocket: "/run/openshell/agent.sock",
				Annotations: map[string]string{"future.context": "preserved"},
			},
			Providers: []string{"nvidia"},
		},
		Status: &pb.SandboxStatus{
			SandboxName: "driver-sandbox-id",
			AgentPod:    "agent-pod-1",
			AgentFd:     "agent.sock",
			SandboxFd:   "sandbox.sock",
			Conditions: []*pb.SandboxCondition{{
				Type:               "Ready",
				Status:             "True",
				Reason:             "Provisioned",
				Message:            "sandbox is ready",
				LastTransitionTime: "2026-08-21T00:00:00Z",
			}},
			Phase:                pb.SandboxPhase_SANDBOX_PHASE_READY,
			CurrentPolicyVersion: 7,
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	record := logs.ResourceLogs().At(0).ScopeLogs().At(0).LogRecords().At(0)
	sourcePayload, ok := record.Body().Map().Get("source_payload")
	if !ok || sourcePayload.Type() != pcommon.ValueTypeMap {
		t.Fatal("source_payload is missing")
	}
	metadata, ok := sourcePayload.Map().Get("metadata")
	if !ok || metadata.Map().AsRaw()["annotations"].(map[string]any)["trace.id"] != "trace-from-annotation" {
		t.Fatalf("metadata=%#v", metadata.AsRaw())
	}
	spec, ok := sourcePayload.Map().Get("spec")
	if !ok {
		t.Fatal("spec is missing")
	}
	if spec.Map().AsRaw()["environment"].(map[string]any)["authorization"] != "Bearer source-secret" {
		t.Fatalf("spec=%#v", spec.AsRaw())
	}
	template := spec.Map().AsRaw()["template"].(map[string]any)
	if template["future.context"] != nil {
		t.Fatalf("unexpected flattened template=%#v", template)
	}
	if template["annotations"].(map[string]any)["future.context"] != "preserved" {
		t.Fatalf("template annotations=%#v", template["annotations"])
	}
	status, ok := sourcePayload.Map().Get("status")
	if !ok {
		t.Fatal("status is missing")
	}
	conditions := status.Map().AsRaw()["conditions"].([]any)
	if len(conditions) != 1 || conditions[0].(map[string]any)["reason"] != "Provisioned" {
		t.Fatalf("conditions=%#v", conditions)
	}
}

func TestLifecyclePolicyVersionIsOnlyEmittedWhenReported(t *testing.T) {
	config := createDefaultConfig().(*Config)
	for _, test := range []struct {
		name              string
		policyVersion     uint32
		expectPolicyField bool
	}{
		{name: "unavailable", policyVersion: 0, expectPolicyField: false},
		{name: "reported", policyVersion: 3, expectPolicyField: true},
	} {
		t.Run(test.name, func(t *testing.T) {
			logs, err := lifecycleLogs(config, "", &pb.Sandbox{
				Metadata: &datamodelv1.ObjectMeta{
					Id:              "sandbox-id",
					Name:            "sandbox-name",
					Workspace:       "default",
					ResourceVersion: 7,
				},
				Status: &pb.SandboxStatus{CurrentPolicyVersion: test.policyVersion},
			})
			if err != nil {
				t.Fatal(err)
			}
			record := logs.ResourceLogs().At(0).ScopeLogs().At(0).LogRecords().At(0)
			attribute, attributeExists := record.Attributes().Get("openshell.policy.version")
			sandboxValue, sandboxExists := record.Body().Map().Get("sandbox")
			if !sandboxExists || sandboxValue.Type() != pcommon.ValueTypeMap {
				t.Fatal("lifecycle body has no sandbox object")
			}
			bodyVersion, bodyExists := sandboxValue.Map().Get("policy_version")
			if attributeExists != test.expectPolicyField || bodyExists != test.expectPolicyField {
				t.Fatalf(
					"policy presence attribute=%t body=%t, want %t",
					attributeExists,
					bodyExists,
					test.expectPolicyField,
				)
			}
			if test.expectPolicyField {
				if attribute.Type() != pcommon.ValueTypeInt || attribute.Int() != int64(test.policyVersion) {
					t.Fatalf("policy attribute=%v, want %d", attribute.AsRaw(), test.policyVersion)
				}
				if bodyVersion.Type() != pcommon.ValueTypeInt || bodyVersion.Int() != int64(test.policyVersion) {
					t.Fatalf("policy body=%v, want %d", bodyVersion.AsRaw(), test.policyVersion)
				}
			}
		})
	}
}

func TestNilAndUnsupportedPayloadBecomeConversionErrors(t *testing.T) {
	config := createDefaultConfig().(*Config)
	ref := sandboxRef{ID: "sandbox-id"}
	for _, event := range []*pb.SandboxStreamEvent{nil, {}} {
		if _, err := convertStreamEvent(config, ref, "", event); err == nil {
			t.Fatal("expected conversion error")
		}
	}
}

func TestShutdownHonorsContextDeadline(t *testing.T) {
	watch := newWatchReceiver(
		createDefaultConfig().(*Config),
		receiver.Settings{},
		nil,
	)
	watch.discoveryWG.Add(1)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if err := watch.Shutdown(ctx); !errors.Is(err, context.Canceled) {
		t.Fatalf("Shutdown() error = %v, want context cancellation", err)
	}
	watch.discoveryWG.Done()
	if err := watch.Shutdown(context.Background()); err != nil {
		t.Fatalf("Shutdown() after workers exit: %v", err)
	}
}

func TestConsumeWithBackpressureDoesNotRetryPermanentError(t *testing.T) {
	next, err := consumer.NewLogs(func(context.Context, plog.Logs) error {
		return consumererror.NewPermanent(errors.New("invalid downstream request"))
	})
	if err != nil {
		t.Fatal(err)
	}
	watch := newWatchReceiver(
		createDefaultConfig().(*Config),
		receiver.Settings{},
		next,
	)
	ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer cancel()
	err = watch.consumeWithBackpressure(ctx, plog.NewLogs())
	if !consumererror.IsPermanent(err) {
		t.Fatalf("permanent error was retried or lost: %v", err)
	}
}
