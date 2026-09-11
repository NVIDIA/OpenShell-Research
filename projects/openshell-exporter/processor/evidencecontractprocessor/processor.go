// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package evidencecontractprocessor

import (
	"context"
	"crypto/sha256"
	"errors"
	"fmt"
	"strings"

	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/plog"
	"go.opentelemetry.io/collector/pdata/ptrace"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/metric"
	"go.opentelemetry.io/otel/metric/noop"
	"go.uber.org/zap"
)

const (
	contractVersionAttribute  = "openshell.exporter.contract.version"
	contractStageAttribute    = "openshell.exporter.contract.stage"
	contractSignalAttribute   = "openshell.exporter.contract.signal"
	contractProducerAttribute = "openshell.exporter.contract.producer"
	tenantIDAttribute         = "openshell.exporter.tenant.id"
	shardKeyAttribute         = "openshell.exporter.shard.key"
	shardVersionAttribute     = "openshell.exporter.shard.version"

	contractProducer = "openshell-event-exporter"
	logStage         = "normalized_redacted"
	traceStage       = "privacy_filtered"
	shardVersion     = "1"
)

type contractMetrics struct {
	accepted metric.Int64Counter
	rejected metric.Int64Counter
}

type processorImpl struct {
	config  *Config
	logger  *zap.Logger
	metrics contractMetrics
}

func newProcessor(config *Config, logger *zap.Logger, provider metric.MeterProvider) (*processorImpl, error) {
	if err := config.Validate(); err != nil {
		return nil, err
	}
	if provider == nil {
		provider = noop.NewMeterProvider()
	}
	meter := provider.Meter("github.com/NVIDIA-dev/OpenShell-exporter/evidencecontract")
	accepted, err := meter.Int64Counter(
		"openshell.exporter.internal_contract.accepted",
		metric.WithDescription("Internal edge-to-central batches accepted by signal and role."),
		metric.WithUnit("{batch}"),
	)
	if err != nil {
		return nil, fmt.Errorf("create accepted contract counter: %w", err)
	}
	rejected, err := meter.Int64Counter(
		"openshell.exporter.internal_contract.rejected",
		metric.WithDescription("Internal edge-to-central batches rejected by signal and bounded reason."),
		metric.WithUnit("{batch}"),
	)
	if err != nil {
		return nil, fmt.Errorf("create rejected contract counter: %w", err)
	}
	return &processorImpl{
		config:  config,
		logger:  logger,
		metrics: contractMetrics{accepted: accepted, rejected: rejected},
	}, nil
}

func (p *processorImpl) processLogs(ctx context.Context, logs plog.Logs) (plog.Logs, error) {
	if p.config.Mode == modeStamp {
		for resourceIndex := 0; resourceIndex < logs.ResourceLogs().Len(); resourceIndex++ {
			resourceLogs := logs.ResourceLogs().At(resourceIndex)
			stampResource(resourceLogs.Resource().Attributes(), p.config.ContractVersion, p.config.TenantID, "logs", logStage)
			for scopeIndex := 0; scopeIndex < resourceLogs.ScopeLogs().Len(); scopeIndex++ {
				records := resourceLogs.ScopeLogs().At(scopeIndex).LogRecords()
				for recordIndex := 0; recordIndex < records.Len(); recordIndex++ {
					record := records.At(recordIndex)
					key, reason := logShardKey(record.Body(), p.config.TenantID)
					if reason != "" {
						return logs, p.reject(ctx, "logs", reason)
					}
					record.Attributes().PutStr(shardKeyAttribute, key)
				}
			}
		}
		p.recordAccepted(ctx, "logs")
		return logs, nil
	}
	if reason := verifyLogs(logs, p.config.ContractVersion, p.config.TenantID); reason != "" {
		return logs, p.reject(ctx, "logs", reason)
	}
	p.recordAccepted(ctx, "logs")
	return logs, nil
}

func (p *processorImpl) processTraces(ctx context.Context, traces ptrace.Traces) (ptrace.Traces, error) {
	if p.config.Mode == modeStamp {
		for i := 0; i < traces.ResourceSpans().Len(); i++ {
			resource := traces.ResourceSpans().At(i).Resource().Attributes()
			stampResource(resource, p.config.ContractVersion, p.config.TenantID, "traces", traceStage)
			key, reason := traceShardKey(resource, p.config.TenantID)
			if reason != "" {
				return traces, p.reject(ctx, "traces", reason)
			}
			resource.PutStr(shardKeyAttribute, key)
		}
		p.recordAccepted(ctx, "traces")
		return traces, nil
	}
	if reason := verifyTraces(traces, p.config.ContractVersion, p.config.TenantID); reason != "" {
		return traces, p.reject(ctx, "traces", reason)
	}
	p.recordAccepted(ctx, "traces")
	return traces, nil
}

func stampResource(attributes pcommon.Map, version, tenantID, signal, stage string) {
	attributes.PutStr(contractVersionAttribute, version)
	attributes.PutStr(contractStageAttribute, stage)
	attributes.PutStr(contractSignalAttribute, signal)
	attributes.PutStr(contractProducerAttribute, contractProducer)
	attributes.PutStr(tenantIDAttribute, tenantID)
	attributes.PutStr(shardVersionAttribute, shardVersion)
}

func verifyLogs(logs plog.Logs, version, tenantID string) string {
	if logs.ResourceLogs().Len() == 0 {
		return "empty_batch"
	}
	for resourceIndex := 0; resourceIndex < logs.ResourceLogs().Len(); resourceIndex++ {
		resourceLogs := logs.ResourceLogs().At(resourceIndex)
		if reason := verifyResource(resourceLogs.Resource().Attributes(), version, tenantID, "logs", logStage); reason != "" {
			return reason
		}
		for scopeIndex := 0; scopeIndex < resourceLogs.ScopeLogs().Len(); scopeIndex++ {
			records := resourceLogs.ScopeLogs().At(scopeIndex).LogRecords()
			for recordIndex := 0; recordIndex < records.Len(); recordIndex++ {
				record := records.At(recordIndex)
				if reason := verifyEnvelope(record.Body()); reason != "" {
					return reason
				}
				want, reason := logShardKey(record.Body(), tenantID)
				if reason != "" {
					return reason
				}
				if !exactString(record.Attributes(), shardKeyAttribute, want) {
					return "invalid_shard_key"
				}
			}
		}
	}
	return ""
}

func verifyTraces(traces ptrace.Traces, version, tenantID string) string {
	if traces.ResourceSpans().Len() == 0 {
		return "empty_batch"
	}
	for i := 0; i < traces.ResourceSpans().Len(); i++ {
		resource := traces.ResourceSpans().At(i).Resource().Attributes()
		if reason := verifyResource(resource, version, tenantID, "traces", traceStage); reason != "" {
			return reason
		}
		for _, key := range []string{"telemetry.source", "openshell.gateway.id", "openshell.workspace"} {
			if !hasNonEmptyString(resource, key) {
				return "missing_trace_context"
			}
		}
		want, reason := traceShardKey(resource, tenantID)
		if reason != "" {
			return reason
		}
		if !exactString(resource, shardKeyAttribute, want) {
			return "invalid_shard_key"
		}
	}
	return ""
}

func verifyResource(attributes pcommon.Map, version, tenantID, signal, stage string) string {
	checks := map[string]string{
		contractVersionAttribute:  version,
		contractStageAttribute:    stage,
		contractSignalAttribute:   signal,
		contractProducerAttribute: contractProducer,
		tenantIDAttribute:         tenantID,
		shardVersionAttribute:     shardVersion,
	}
	for key, want := range checks {
		if !exactString(attributes, key, want) {
			return "invalid_marker"
		}
	}
	return ""
}

func logShardKey(body pcommon.Value, tenantID string) (string, string) {
	if body.Type() != pcommon.ValueTypeMap {
		return "", "invalid_envelope"
	}
	openshellValue, ok := body.Map().Get("openshell")
	if !ok || openshellValue.Type() != pcommon.ValueTypeMap {
		return "", "invalid_envelope"
	}
	openshell := openshellValue.Map()
	gatewayID, ok := mapString(openshell, "gateway_id")
	if !ok {
		return "", "missing_shard_context"
	}
	workspace, ok := mapString(openshell, "workspace")
	if !ok {
		return "", "missing_shard_context"
	}
	sandboxID, _ := mapString(openshell, "sandbox_id")
	return deterministicShardKey(tenantID, gatewayID, workspace, sandboxID), ""
}

func traceShardKey(resource pcommon.Map, tenantID string) (string, string) {
	gatewayID, ok := mapString(resource, "openshell.gateway.id")
	if !ok {
		return "", "missing_shard_context"
	}
	workspace, ok := mapString(resource, "openshell.workspace")
	if !ok {
		return "", "missing_shard_context"
	}
	sandboxID, _ := mapString(resource, "openshell.sandbox.id")
	return deterministicShardKey(tenantID, gatewayID, workspace, sandboxID), ""
}

func deterministicShardKey(values ...string) string {
	digest := sha256.Sum256([]byte(strings.Join(values, "\x00")))
	return fmt.Sprintf("sha256:%x", digest)
}

func verifyEnvelope(body pcommon.Value) string {
	if body.Type() != pcommon.ValueTypeMap {
		return "invalid_envelope"
	}
	envelope := body.Map()
	if value, ok := envelope.Get("schema_version"); !ok || value.Type() != pcommon.ValueTypeStr || value.Str() != versionV1 {
		return "invalid_envelope"
	}
	if !hasNonEmptyString(envelope, "observed_time") {
		return "invalid_envelope"
	}
	for _, key := range []string{"acquisition", "openshell", "security", "correlation"} {
		value, ok := envelope.Get(key)
		if !ok || value.Type() != pcommon.ValueTypeMap {
			return "invalid_envelope"
		}
	}
	// Malformed non-object source records are valid evidence. The normalizer
	// retains them as a recursively redacted scalar under original.
	if _, ok := envelope.Get("original"); !ok {
		return "invalid_envelope"
	}
	security, _ := envelope.Get("security")
	for _, key := range []string{"validation", "redaction"} {
		value, ok := security.Map().Get(key)
		if !ok || value.Type() != pcommon.ValueTypeMap {
			return "invalid_envelope"
		}
	}
	return ""
}

func mapString(attributes pcommon.Map, key string) (string, bool) {
	value, ok := attributes.Get(key)
	if !ok || value.Type() != pcommon.ValueTypeStr || value.Str() == "" {
		return "", false
	}
	return value.Str(), true
}

func exactString(attributes pcommon.Map, key, want string) bool {
	value, ok := attributes.Get(key)
	return ok && value.Type() == pcommon.ValueTypeStr && value.Str() == want
}

func hasNonEmptyString(attributes pcommon.Map, key string) bool {
	_, ok := mapString(attributes, key)
	return ok
}

func (p *processorImpl) recordAccepted(ctx context.Context, signal string) {
	p.metrics.accepted.Add(ctx, 1, metric.WithAttributes(
		attribute.String("signal", signal),
		attribute.String("mode", p.config.Mode),
	))
}

func (p *processorImpl) reject(ctx context.Context, signal, reason string) error {
	p.metrics.rejected.Add(ctx, 1, metric.WithAttributes(
		attribute.String("signal", signal),
		attribute.String("reason", reason),
	))
	p.logger.Error("rejected evidence at internal role boundary",
		zap.String("signal", signal),
		zap.String("reason", reason),
		zap.String("contract_version", p.config.ContractVersion),
	)
	// Keep rejection retryable so an edge persistent queue never acknowledges
	// evidence that the central role cannot prove was safely processed.
	return errors.New("internal evidence contract rejected: " + reason)
}
