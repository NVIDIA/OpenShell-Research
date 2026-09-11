// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package relayprocessor

import (
	"context"

	"go.opentelemetry.io/collector/component"
	"go.opentelemetry.io/collector/consumer"
	"go.opentelemetry.io/collector/processor"
	"go.opentelemetry.io/collector/processor/processorhelper"
)

var componentType = component.MustNewType("relay")

func NewFactory() processor.Factory {
	return processor.NewFactory(
		componentType,
		createDefaultConfig,
		processor.WithTraces(createTracesProcessor, component.StabilityLevelBeta),
	)
}

func createDefaultConfig() component.Config {
	return &Config{
		GatewayID:                     "local-gateway",
		Workspace:                     "default",
		TelemetrySource:               "nemo_relay",
		CanonicalizeSpanNames:         true,
		RequiredCorrelationAttributes: []string{"openshell.sandbox.id", "agent.session.id"},
		Privacy: PrivacyConfig{
			Mode: privacyAllow,
			AllowedAttributes: []string{
				"deployment.environment",
				"error.category",
				"error.code",
				"error.type",
				"exception.type",
				"gen_ai.conversation.id",
				"gen_ai.operation.name",
				"gen_ai.request.model",
				"gen_ai.response.finish_reasons",
				"gen_ai.response.id",
				"gen_ai.response.model",
				"gen_ai.tool.call.id",
				"gen_ai.usage.*",
				"http.response.status_code",
				"llm.cost.*",
				"llm.model_name",
				"llm.provider",
				"llm.token_count.*",
				"nemo_relay.agent.kind",
				"nemo_relay.llm.cost.*",
				"nemo_relay.mark.parent_uuid",
				"nemo_relay.mark.uuid",
				"nemo_relay.model_name",
				"nemo_relay.scope_type",
				"nemo_relay.session.instance_id",
				"nemo_relay.tool_call_id",
				"nemo_relay.uuid",
				"openinference.span.kind",
				"openshell.sandbox.name",
				"request.id",
				"service.instance.id",
				"service.name",
				"service.namespace",
				"service.version",
				"session.id",
				"telemetry.source",
				"token_type",
				"tokens_in",
				"tokens_out",
				"tool.name",
				"tool_call.function.name",
				"tool_call.id",
			},
			DeniedAttributes: []string{
				"*authorization*",
				"*credential*",
				"*input*",
				"*output*",
				"*password*",
				"*prompt*",
				"*response*content*",
				"*secret*",
				"*token*",
				"*tool*argument*",
				"*tool*result*",
			},
		},
		Aliases: defaultAliases(),
	}
}

func createTracesProcessor(
	ctx context.Context,
	settings processor.Settings,
	cfg component.Config,
	next consumer.Traces,
) (processor.Traces, error) {
	implementation, err := newProcessor(cfg.(*Config), settings.Logger, settings.MeterProvider)
	if err != nil {
		return nil, err
	}
	return processorhelper.NewTraces(
		ctx,
		settings,
		cfg,
		next,
		implementation.processTraces,
		processorhelper.WithStart(implementation.start),
		processorhelper.WithCapabilities(consumer.Capabilities{MutatesData: true}),
	)
}
