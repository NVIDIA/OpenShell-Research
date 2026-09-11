// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package evidencecontractprocessor

import (
	"context"

	"go.opentelemetry.io/collector/component"
	"go.opentelemetry.io/collector/consumer"
	"go.opentelemetry.io/collector/processor"
	"go.opentelemetry.io/collector/processor/processorhelper"
)

var componentType = component.MustNewType("evidencecontract")

func NewFactory() processor.Factory {
	return processor.NewFactory(
		componentType,
		createDefaultConfig,
		processor.WithLogs(createLogsProcessor, component.StabilityLevelAlpha),
		processor.WithTraces(createTracesProcessor, component.StabilityLevelAlpha),
	)
}

func createDefaultConfig() component.Config {
	return &Config{Mode: modeVerify, ContractVersion: versionV1, TenantID: "default"}
}

func createLogsProcessor(ctx context.Context, settings processor.Settings, cfg component.Config, next consumer.Logs) (processor.Logs, error) {
	implementation, err := newProcessor(cfg.(*Config), settings.Logger, settings.MeterProvider)
	if err != nil {
		return nil, err
	}
	return processorhelper.NewLogs(
		ctx, settings, cfg, next, implementation.processLogs,
		processorhelper.WithCapabilities(consumer.Capabilities{MutatesData: implementation.config.Mode == modeStamp}),
	)
}

func createTracesProcessor(ctx context.Context, settings processor.Settings, cfg component.Config, next consumer.Traces) (processor.Traces, error) {
	implementation, err := newProcessor(cfg.(*Config), settings.Logger, settings.MeterProvider)
	if err != nil {
		return nil, err
	}
	return processorhelper.NewTraces(
		ctx, settings, cfg, next, implementation.processTraces,
		processorhelper.WithCapabilities(consumer.Capabilities{MutatesData: implementation.config.Mode == modeStamp}),
	)
}
