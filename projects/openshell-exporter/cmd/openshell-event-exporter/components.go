// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package main

import (
	cloudeventsexporter "github.com/NVIDIA/OpenShell-Research/projects/openshell-exporter/exporter/cloudeventsexporter"
	storagehealthextension "github.com/NVIDIA/OpenShell-Research/projects/openshell-exporter/extension/storagehealthextension"
	evidencecontractprocessor "github.com/NVIDIA/OpenShell-Research/projects/openshell-exporter/processor/evidencecontractprocessor"
	openshellprocessor "github.com/NVIDIA/OpenShell-Research/projects/openshell-exporter/processor/openshellprocessor"
	relayprocessor "github.com/NVIDIA/OpenShell-Research/projects/openshell-exporter/processor/relayprocessor"
	watchsandboxreceiver "github.com/NVIDIA/OpenShell-Research/projects/openshell-exporter/receiver/watchsandboxreceiver"
	fileexporter "github.com/open-telemetry/opentelemetry-collector-contrib/exporter/fileexporter"
	loadbalancingexporter "github.com/open-telemetry/opentelemetry-collector-contrib/exporter/loadbalancingexporter"
	bearertokenauthextension "github.com/open-telemetry/opentelemetry-collector-contrib/extension/bearertokenauthextension"
	healthcheckextension "github.com/open-telemetry/opentelemetry-collector-contrib/extension/healthcheckextension"
	filestorage "github.com/open-telemetry/opentelemetry-collector-contrib/extension/storage/filestorage"
	attributesprocessor "github.com/open-telemetry/opentelemetry-collector-contrib/processor/attributesprocessor"
	resourceprocessor "github.com/open-telemetry/opentelemetry-collector-contrib/processor/resourceprocessor"
	filelogreceiver "github.com/open-telemetry/opentelemetry-collector-contrib/receiver/filelogreceiver"
	k8sobjectsreceiver "github.com/open-telemetry/opentelemetry-collector-contrib/receiver/k8sobjectsreceiver"
	"go.opentelemetry.io/collector/component"
	"go.opentelemetry.io/collector/connector"
	"go.opentelemetry.io/collector/exporter"
	otlpexporter "go.opentelemetry.io/collector/exporter/otlpexporter"
	otlphttpexporter "go.opentelemetry.io/collector/exporter/otlphttpexporter"
	"go.opentelemetry.io/collector/extension"
	"go.opentelemetry.io/collector/otelcol"
	"go.opentelemetry.io/collector/processor"
	memorylimiterprocessor "go.opentelemetry.io/collector/processor/memorylimiterprocessor"
	"go.opentelemetry.io/collector/receiver"
	otlpreceiver "go.opentelemetry.io/collector/receiver/otlpreceiver"
	otelconftelemetry "go.opentelemetry.io/collector/service/telemetry/otelconftelemetry"
)

type aliasProvider interface{ DeprecatedAlias() component.Type }

func makeModulesMap[T component.Factory](factories map[component.Type]T, modules map[component.Type]string) map[component.Type]string {
	for compType, factory := range factories {
		if ap, ok := any(factory).(aliasProvider); ok {
			alias := ap.DeprecatedAlias()
			if alias.String() != "" {
				modules[alias] = modules[compType]
			}
		}
	}
	return modules
}

func components() (otelcol.Factories, error) {
	var err error
	factories := otelcol.Factories{
		Telemetry: otelconftelemetry.NewFactory(),
	}

	factories.Extensions, err = otelcol.MakeFactoryMap[extension.Factory](
		bearertokenauthextension.NewFactory(),
		healthcheckextension.NewFactory(),
		filestorage.NewFactory(),
		storagehealthextension.NewFactory(),
	)
	if err != nil {
		return otelcol.Factories{}, err
	}
	factories.ExtensionModules = makeModulesMap(factories.Extensions, map[component.Type]string{
		bearertokenauthextension.NewFactory().Type(): "github.com/open-telemetry/opentelemetry-collector-contrib/extension/bearertokenauthextension v0.160.0",
		healthcheckextension.NewFactory().Type():     "github.com/open-telemetry/opentelemetry-collector-contrib/extension/healthcheckextension v0.160.0",
		filestorage.NewFactory().Type():              "github.com/open-telemetry/opentelemetry-collector-contrib/extension/storage/filestorage v0.160.0",
		storagehealthextension.NewFactory().Type():   "github.com/NVIDIA/OpenShell-Research/projects/openshell-exporter v0.0.5-rc.1",
	})

	factories.Receivers, err = otelcol.MakeFactoryMap[receiver.Factory](
		otlpreceiver.NewFactory(),
		filelogreceiver.NewFactory(),
		k8sobjectsreceiver.NewFactory(),
		watchsandboxreceiver.NewFactory(),
	)
	if err != nil {
		return otelcol.Factories{}, err
	}
	factories.ReceiverModules = makeModulesMap(factories.Receivers, map[component.Type]string{
		otlpreceiver.NewFactory().Type():         "go.opentelemetry.io/collector/receiver/otlpreceiver v0.160.0",
		filelogreceiver.NewFactory().Type():      "github.com/open-telemetry/opentelemetry-collector-contrib/receiver/filelogreceiver v0.160.0",
		k8sobjectsreceiver.NewFactory().Type():   "github.com/open-telemetry/opentelemetry-collector-contrib/receiver/k8sobjectsreceiver v0.160.0",
		watchsandboxreceiver.NewFactory().Type(): "github.com/NVIDIA/OpenShell-Research/projects/openshell-exporter v0.0.5-rc.1",
	})

	factories.Exporters, err = otelcol.MakeFactoryMap[exporter.Factory](
		otlpexporter.NewFactory(),
		otlphttpexporter.NewFactory(),
		fileexporter.NewFactory(),
		loadbalancingexporter.NewFactory(),
		cloudeventsexporter.NewFactory(),
	)
	if err != nil {
		return otelcol.Factories{}, err
	}
	factories.ExporterModules = makeModulesMap(factories.Exporters, map[component.Type]string{
		otlpexporter.NewFactory().Type():          "go.opentelemetry.io/collector/exporter/otlpexporter v0.160.0",
		otlphttpexporter.NewFactory().Type():      "go.opentelemetry.io/collector/exporter/otlphttpexporter v0.160.0",
		fileexporter.NewFactory().Type():          "github.com/open-telemetry/opentelemetry-collector-contrib/exporter/fileexporter v0.160.0",
		loadbalancingexporter.NewFactory().Type(): "github.com/open-telemetry/opentelemetry-collector-contrib/exporter/loadbalancingexporter v0.160.0",
		cloudeventsexporter.NewFactory().Type():   "github.com/NVIDIA/OpenShell-Research/projects/openshell-exporter v0.0.5-rc.1",
	})

	factories.Processors, err = otelcol.MakeFactoryMap[processor.Factory](
		attributesprocessor.NewFactory(),
		memorylimiterprocessor.NewFactory(),
		resourceprocessor.NewFactory(),
		evidencecontractprocessor.NewFactory(),
		openshellprocessor.NewFactory(),
		relayprocessor.NewFactory(),
	)
	if err != nil {
		return otelcol.Factories{}, err
	}
	factories.ProcessorModules = makeModulesMap(factories.Processors, map[component.Type]string{
		attributesprocessor.NewFactory().Type():       "github.com/open-telemetry/opentelemetry-collector-contrib/processor/attributesprocessor v0.160.0",
		memorylimiterprocessor.NewFactory().Type():    "go.opentelemetry.io/collector/processor/memorylimiterprocessor v0.160.0",
		resourceprocessor.NewFactory().Type():         "github.com/open-telemetry/opentelemetry-collector-contrib/processor/resourceprocessor v0.160.0",
		evidencecontractprocessor.NewFactory().Type(): "github.com/NVIDIA/OpenShell-Research/projects/openshell-exporter v0.0.5-rc.1",
		openshellprocessor.NewFactory().Type():        "github.com/NVIDIA/OpenShell-Research/projects/openshell-exporter v0.0.5-rc.1",
		relayprocessor.NewFactory().Type():            "github.com/NVIDIA/OpenShell-Research/projects/openshell-exporter v0.0.5-rc.1",
	})

	factories.Connectors, err = otelcol.MakeFactoryMap[connector.Factory]()
	if err != nil {
		return otelcol.Factories{}, err
	}
	factories.ConnectorModules = makeModulesMap(factories.Connectors, map[component.Type]string{})

	return factories, nil
}
