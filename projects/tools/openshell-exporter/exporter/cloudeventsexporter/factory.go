// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package cloudeventsexporter

import (
	"context"
	"time"

	"go.opentelemetry.io/collector/component"
	"go.opentelemetry.io/collector/config/confighttp"
	"go.opentelemetry.io/collector/config/configoptional"
	"go.opentelemetry.io/collector/config/configretry"
	"go.opentelemetry.io/collector/exporter"
	"go.opentelemetry.io/collector/exporter/exporterhelper"
)

var componentType = component.MustNewType("cloudevents")

func NewFactory() exporter.Factory {
	return exporter.NewFactory(
		componentType,
		createDefaultConfig,
		exporter.WithLogs(createLogsExporter, component.StabilityLevelAlpha),
	)
}

func createDefaultConfig() component.Config {
	clientConfig := confighttp.NewDefaultClientConfig()
	clientConfig.Timeout = 15 * time.Second
	queueConfig := exporterhelper.NewDefaultQueueConfig()
	queueConfig.QueueSize = 10_000
	queueConfig.NumConsumers = 4
	return &Config{
		ClientConfig:      ClientConfig(clientConfig),
		RetryConfig:       configretry.NewDefaultBackOffConfig(),
		QueueConfig:       configoptional.Some(queueConfig),
		DefaultSource:     "openshell://local",
		AllowInsecureHTTP: false,
		MaxEvents:         500,
		MaxEventBytes:     1024 * 1024,
		MaxRequestBytes:   4 * 1024 * 1024,
	}
}

func createLogsExporter(
	ctx context.Context,
	settings exporter.Settings,
	cfg component.Config,
) (exporter.Logs, error) {
	exporterConfig := cfg.(*Config)
	if err := exporterConfig.Validate(); err != nil {
		return nil, err
	}
	implementation := &cloudEventsExporter{
		config:   exporterConfig,
		settings: settings,
		metrics:  newExporterMetrics(settings.MeterProvider),
	}
	return exporterhelper.NewLogs(
		ctx,
		settings,
		cfg,
		implementation.pushLogs,
		exporterhelper.WithStart(implementation.start),
		exporterhelper.WithShutdown(implementation.shutdown),
		exporterhelper.WithRetry(exporterConfig.RetryConfig),
		exporterhelper.WithQueue(exporterConfig.QueueConfig),
	)
}
