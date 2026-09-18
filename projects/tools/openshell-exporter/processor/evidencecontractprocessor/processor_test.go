// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package evidencecontractprocessor

import (
	"context"
	"reflect"
	"strings"
	"testing"

	"go.opentelemetry.io/collector/pdata/plog"
	"go.opentelemetry.io/collector/pdata/ptrace"
	"go.opentelemetry.io/otel/metric/noop"
	"go.uber.org/zap/zaptest"
)

func TestLogsRoundTripThroughInternalContract(t *testing.T) {
	logs := validLogs()
	stamp, err := newProcessor(testConfig(modeStamp), zaptest.NewLogger(t), noop.NewMeterProvider())
	if err != nil {
		t.Fatal(err)
	}
	verify, err := newProcessor(testConfig(modeVerify), zaptest.NewLogger(t), noop.NewMeterProvider())
	if err != nil {
		t.Fatal(err)
	}
	_, err = stamp.processLogs(context.Background(), logs)
	if err != nil {
		t.Fatal(err)
	}
	bodyBefore := logs.ResourceLogs().At(0).ScopeLogs().At(0).LogRecords().At(0).Body().AsRaw()
	logs, err = verify.processLogs(context.Background(), logs)
	if err != nil {
		t.Fatal(err)
	}
	bodyAfter := logs.ResourceLogs().At(0).ScopeLogs().At(0).LogRecords().At(0).Body().AsRaw()
	if !reflect.DeepEqual(bodyBefore, bodyAfter) {
		t.Fatal("verification mutated the public evidence envelope")
	}
}

func TestVerifyLogsRejectsRawOrIncompleteEvidence(t *testing.T) {
	verify, err := newProcessor(testConfig(modeVerify), zaptest.NewLogger(t), noop.NewMeterProvider())
	if err != nil {
		t.Fatal(err)
	}
	if _, err := verify.processLogs(context.Background(), validLogs()); err == nil {
		t.Fatal("raw logs without an edge marker were accepted")
	}
	logs := validLogs()
	stamp, _ := newProcessor(testConfig(modeStamp), zaptest.NewLogger(t), noop.NewMeterProvider())
	logs, _ = stamp.processLogs(context.Background(), logs)
	logs.ResourceLogs().At(0).ScopeLogs().At(0).LogRecords().At(0).Body().Map().Remove("security")
	if _, err := verify.processLogs(context.Background(), logs); err == nil {
		t.Fatal("incomplete normalized envelope was accepted")
	}
}

func TestVerifyLogsAcceptsRetainedMalformedScalar(t *testing.T) {
	logs := validLogs()
	body := logs.ResourceLogs().At(0).ScopeLogs().At(0).LogRecords().At(0).Body().Map()
	body.PutStr("original", "malformed secret=[REDACTED]")
	stamp, _ := newProcessor(testConfig(modeStamp), zaptest.NewLogger(t), noop.NewMeterProvider())
	logs, err := stamp.processLogs(context.Background(), logs)
	if err != nil {
		t.Fatal(err)
	}
	verify, _ := newProcessor(testConfig(modeVerify), zaptest.NewLogger(t), noop.NewMeterProvider())
	if _, err := verify.processLogs(context.Background(), logs); err != nil {
		t.Fatalf("retained malformed scalar was rejected: %v", err)
	}
}

func TestTracesRoundTripAndRequireNormalizedContext(t *testing.T) {
	traces := validTraces()
	stamp, _ := newProcessor(testConfig(modeStamp), zaptest.NewLogger(t), noop.NewMeterProvider())
	verify, _ := newProcessor(testConfig(modeVerify), zaptest.NewLogger(t), noop.NewMeterProvider())
	traces, err := stamp.processTraces(context.Background(), traces)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := verify.processTraces(context.Background(), traces); err != nil {
		t.Fatal(err)
	}
	traces.ResourceSpans().At(0).Resource().Attributes().Remove("telemetry.source")
	if _, err := verify.processTraces(context.Background(), traces); err == nil {
		t.Fatal("trace without bounded source context was accepted")
	}
}

func TestConfigRejectsUnknownModesVersionsAndTenants(t *testing.T) {
	for _, config := range []Config{
		{Mode: "bypass", ContractVersion: versionV1, TenantID: "tenant-a"},
		{Mode: modeVerify, ContractVersion: "2.0", TenantID: "tenant-a"},
		{Mode: modeVerify, ContractVersion: versionV1, TenantID: "Tenant A"},
	} {
		if err := config.Validate(); err == nil {
			t.Fatalf("invalid config accepted: %+v", config)
		}
	}
}

func TestShardIdentityIsStableAndTenantScoped(t *testing.T) {
	logs := validLogs()
	stamp, _ := newProcessor(testConfig(modeStamp), zaptest.NewLogger(t), noop.NewMeterProvider())
	logs, err := stamp.processLogs(context.Background(), logs)
	if err != nil {
		t.Fatal(err)
	}
	record := logs.ResourceLogs().At(0).ScopeLogs().At(0).LogRecords().At(0)
	value, ok := record.Attributes().Get(shardKeyAttribute)
	if !ok || !strings.HasPrefix(value.Str(), "sha256:") || len(value.Str()) != 71 {
		t.Fatalf("unexpected shard key %q", value.Str())
	}
	want := value.Str()
	_, err = stamp.processLogs(context.Background(), logs)
	if err != nil {
		t.Fatal(err)
	}
	got, _ := record.Attributes().Get(shardKeyAttribute)
	if got.Str() != want {
		t.Fatal("replay changed the deterministic shard key")
	}
	otherTenant := deterministicShardKey("tenant-b", "gateway-a", "default", "sandbox-a")
	if otherTenant == want {
		t.Fatal("tenant boundary did not affect shard identity")
	}
}

func TestLogAndTraceForSandboxShareShardIdentity(t *testing.T) {
	logs := validLogs()
	traces := validTraces()
	stamp, _ := newProcessor(testConfig(modeStamp), zaptest.NewLogger(t), noop.NewMeterProvider())
	logs, _ = stamp.processLogs(context.Background(), logs)
	traces, _ = stamp.processTraces(context.Background(), traces)
	logKey, _ := logs.ResourceLogs().At(0).ScopeLogs().At(0).LogRecords().At(0).Attributes().Get(shardKeyAttribute)
	traceKey, _ := traces.ResourceSpans().At(0).Resource().Attributes().Get(shardKeyAttribute)
	if logKey.Str() != traceKey.Str() {
		t.Fatalf("same sandbox routed to different shards: log=%s trace=%s", logKey.Str(), traceKey.Str())
	}
}

func TestVerifyRejectsTenantAndShardForgery(t *testing.T) {
	logs := validLogs()
	stamp, _ := newProcessor(testConfig(modeStamp), zaptest.NewLogger(t), noop.NewMeterProvider())
	verify, _ := newProcessor(testConfig(modeVerify), zaptest.NewLogger(t), noop.NewMeterProvider())
	logs, _ = stamp.processLogs(context.Background(), logs)
	logs.ResourceLogs().At(0).Resource().Attributes().PutStr(tenantIDAttribute, "tenant-b")
	if _, err := verify.processLogs(context.Background(), logs); err == nil {
		t.Fatal("forged tenant was accepted")
	}
	logs, _ = stamp.processLogs(context.Background(), logs)
	logs.ResourceLogs().At(0).ScopeLogs().At(0).LogRecords().At(0).Attributes().PutStr(shardKeyAttribute, "sha256:"+strings.Repeat("0", 64))
	if _, err := verify.processLogs(context.Background(), logs); err == nil {
		t.Fatal("forged shard key was accepted")
	}
}

func testConfig(mode string) *Config {
	return &Config{Mode: mode, ContractVersion: versionV1, TenantID: "tenant-a"}
}

func validLogs() plog.Logs {
	logs := plog.NewLogs()
	record := logs.ResourceLogs().AppendEmpty().ScopeLogs().AppendEmpty().LogRecords().AppendEmpty()
	body := record.Body().SetEmptyMap()
	body.PutStr("schema_version", versionV1)
	body.PutStr("observed_time", "2026-08-28T00:00:00Z")
	for _, key := range []string{"acquisition", "openshell", "correlation", "original"} {
		body.PutEmptyMap(key)
	}
	security := body.PutEmptyMap("security")
	security.PutEmptyMap("validation")
	security.PutEmptyMap("redaction")
	openshell, _ := body.Get("openshell")
	openshell.Map().PutStr("gateway_id", "gateway-a")
	openshell.Map().PutStr("workspace", "default")
	openshell.Map().PutStr("sandbox_id", "sandbox-a")
	return logs
}

func validTraces() ptrace.Traces {
	traces := ptrace.NewTraces()
	resourceSpans := traces.ResourceSpans().AppendEmpty()
	resource := resourceSpans.Resource().Attributes()
	resource.PutStr("telemetry.source", "nemo_relay")
	resource.PutStr("openshell.gateway.id", "gateway-a")
	resource.PutStr("openshell.workspace", "default")
	resource.PutStr("openshell.sandbox.id", "sandbox-a")
	resourceSpans.ScopeSpans().AppendEmpty().Spans().AppendEmpty().SetName("relay.model")
	return traces
}
