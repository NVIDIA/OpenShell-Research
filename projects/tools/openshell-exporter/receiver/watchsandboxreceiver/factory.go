// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package watchsandboxreceiver

import (
	"context"
	"time"

	"go.opentelemetry.io/collector/component"
	"go.opentelemetry.io/collector/consumer"
	"go.opentelemetry.io/collector/receiver"
)

var componentType = component.MustNewType("watchsandbox")

func NewFactory() receiver.Factory {
	return receiver.NewFactory(
		componentType,
		createDefaultConfig,
		receiver.WithLogs(createLogsReceiver, component.StabilityLevelAlpha),
	)
}

func createDefaultConfig() component.Config {
	return &Config{
		Endpoint:          "https://127.0.0.1:50051",
		Workspace:         "default",
		GatewayID:         "local-gateway",
		DiscoveryInterval: 30 * time.Second,
		TokenEnv:          "OPENSHELL_TOKEN",
		LogTailLines:      200,
		EventTail:         200,
		ReconnectInitial:  time.Second,
		ReconnectMax:      30 * time.Second,
		PolicyReconciliation: PolicyReconciliationConfig{
			Enabled:                  false,
			Interval:                 5 * time.Minute,
			Timeout:                  15 * time.Second,
			Workers:                  2,
			QueueSize:                128,
			MaxRevisions:             10000,
			IncludeHistory:           true,
			IncludeEffectivePolicies: false,
		},
	}
}
func createLogsReceiver(
	_ context.Context,
	settings receiver.Settings,
	cfg component.Config,
	next consumer.Logs,
) (receiver.Logs, error) {
	receiverConfig := cfg.(*Config)
	if err := receiverConfig.Validate(); err != nil {
		return nil, err
	}
	return newWatchReceiver(receiverConfig, settings, next), nil
}
