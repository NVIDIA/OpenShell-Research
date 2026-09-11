// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

// Program openshell-event-exporter is an OpenTelemetry Collector binary.
package main

import (
	"os"

	"go.opentelemetry.io/collector/component"
	"go.opentelemetry.io/collector/confmap"
	envprovider "go.opentelemetry.io/collector/confmap/provider/envprovider"
	fileprovider "go.opentelemetry.io/collector/confmap/provider/fileprovider"
	yamlprovider "go.opentelemetry.io/collector/confmap/provider/yamlprovider"
	"go.opentelemetry.io/collector/otelcol"
)

func main() {
	info := component.BuildInfo{
		Command:     "openshell-event-exporter",
		Description: "OpenShell Event Exporter",
		Version:     "0.0.5-rc.1",
	}

	set := otelcol.CollectorSettings{
		BuildInfo: info,
		Factories: components,
		ConfigProviderSettings: otelcol.ConfigProviderSettings{
			ResolverSettings: confmap.ResolverSettings{
				ProviderFactories: []confmap.ProviderFactory{
					envprovider.NewFactory(),
					fileprovider.NewFactory(),
					yamlprovider.NewFactory(),
				},
			},
		},
		ProviderModules: map[string]string{
			envprovider.NewFactory().Create(confmap.ProviderSettings{}).Scheme():  "go.opentelemetry.io/collector/confmap/provider/envprovider v1.66.0",
			fileprovider.NewFactory().Create(confmap.ProviderSettings{}).Scheme(): "go.opentelemetry.io/collector/confmap/provider/fileprovider v1.66.0",
			yamlprovider.NewFactory().Create(confmap.ProviderSettings{}).Scheme(): "go.opentelemetry.io/collector/confmap/provider/yamlprovider v1.66.0",
		},
		ConverterModules: []string{},
	}

	if err := run(set); err != nil {
		// The error message is logged by cobra, so we intentionally
		// avoid logging it again here to prevent duplicate output.
		os.Exit(1)
	}
}

func runInteractive(params otelcol.CollectorSettings) error {
	cmd := otelcol.NewCommand(params)
	if err := cmd.Execute(); err != nil {
		return err
	}

	return nil
}
