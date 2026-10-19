// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package openshellprocessor

import (
	"context"
	"errors"

	"go.opentelemetry.io/collector/component"
	"go.opentelemetry.io/collector/consumer"
	"go.opentelemetry.io/collector/consumer/consumererror"
	"go.opentelemetry.io/collector/pdata/plog"
	"go.opentelemetry.io/collector/processor"
	"go.opentelemetry.io/collector/processor/processorhelper"
)

var componentType = component.MustNewType("openshell")

func NewFactory() processor.Factory {
	return processor.NewFactory(
		componentType,
		createDefaultConfig,
		processor.WithLogs(createLogsProcessor, component.StabilityLevelAlpha),
	)
}

func createDefaultConfig() component.Config {
	return &Config{
		SourceProfiles:   []string{"ocsf.file"},
		GatewayID:        "local-gateway",
		Workspace:        "default",
		SourceInstance:   "ocsf-jsonl",
		DefaultSandboxID: "unknown",
		Validation: ValidationConfig{
			Mode: "mark",
		},
		Redaction: RedactionConfig{
			ProfileID: "openshell-default",
			Version:   "1",
			Keys: []string{
				"access_token",
				"api_key",
				"authorization",
				"client_secret",
				"password",
				"refresh_token",
				"secret",
			},
			Patterns: []string{
				`(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*`,
				`(?i)\b(api[_-]?key|password|secret)\s*[:=]\s*[^\s,;]+`,
			},
		},
		CorrelationFields: []string{
			"metadata.original_event_uid",
			"unmapped.correlation_id",
			"unmapped.policy_chunk_id",
		},
	}
}

func createLogsProcessor(
	ctx context.Context,
	settings processor.Settings,
	cfg component.Config,
	next consumer.Logs,
) (processor.Logs, error) {
	processorConfig := cfg.(*Config)
	implementation, err := newProcessorWithMeter(
		processorConfig,
		settings.Logger,
		settings.MeterProvider,
	)
	if err != nil {
		return nil, err
	}
	inner, err := processorhelper.NewLogs(
		ctx,
		settings,
		cfg,
		next,
		implementation.processLogs,
		processorhelper.WithStart(implementation.start),
		processorhelper.WithCapabilities(consumer.Capabilities{MutatesData: true}),
	)
	if err != nil {
		return nil, err
	}
	return &retrySafeProcessor{Logs: inner}, nil
}

// File receivers can retry the same input after a downstream enqueue failure.
// Keep their source intact: re-normalizing an already redacted envelope changes
// both validation and identity. The inner pipeline owns each attempt's copy.
type retrySafeProcessor struct {
	processor.Logs
}

func (*retrySafeProcessor) Capabilities() consumer.Capabilities {
	return consumer.Capabilities{MutatesData: false}
}

func (p *retrySafeProcessor) ConsumeLogs(ctx context.Context, logs plog.Logs) error {
	attempt := plog.NewLogs()
	logs.CopyTo(attempt)
	err := p.Logs.ConsumeLogs(ctx, attempt)
	var partial consumererror.Logs
	if errors.As(err, &partial) {
		// Partial failures carry normalized data. Retry the original source batch
		// instead; accepted records may repeat with the same source/ID.
		return consumererror.NewLogs(err, logs)
	}
	return err
}
