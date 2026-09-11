// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package relayprocessor

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"go.opentelemetry.io/collector/pdata/pcommon"
	sdkmetric "go.opentelemetry.io/otel/sdk/metric"
	"go.opentelemetry.io/otel/sdk/metric/metricdata"
	"go.uber.org/zap"
	"go.uber.org/zap/zapcore"
	"go.uber.org/zap/zaptest/observer"
)

func TestBoundedOTLPSource(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name            string
		telemetrySource string
		resourceKey     string
		resourceValue   string
		want            string
	}{
		{name: "relay", telemetrySource: "nemo_relay", resourceKey: "service.name", resourceValue: "customer-controlled", want: "relay_otlp"},
		{name: "gateway", telemetrySource: "openshell_native", resourceKey: "service.name", resourceValue: "openshell-gateway", want: "gateway_otlp"},
		{name: "driver", telemetrySource: "openshell_native", resourceKey: "openshell.component", resourceValue: "compute-driver", want: "driver_otlp"},
		{name: "native unclassified", telemetrySource: "openshell_native", resourceKey: "service.name", resourceValue: "customer-controlled", want: "native_otlp_unclassified"},
		{name: "other processor", telemetrySource: "custom-customer-value", resourceKey: "service.name", resourceValue: "secret-url", want: "other_otlp"},
	}
	for _, test := range tests {
		test := test
		t.Run(test.name, func(t *testing.T) {
			t.Parallel()
			resource := pcommon.NewMap()
			resource.PutStr(test.resourceKey, test.resourceValue)
			if got := boundedOTLPSource(test.telemetrySource, resource); got != test.want {
				t.Fatalf("bounded source=%q, want %q", got, test.want)
			}
		})
	}
}

func TestRelaySourceCapabilitiesAreBounded(t *testing.T) {
	t.Parallel()
	tests := []struct {
		telemetrySource string
		wantSources     []string
	}{
		{telemetrySource: "nemo_relay", wantSources: []string{"relay_otlp"}},
		{telemetrySource: "openshell_native", wantSources: []string{"gateway_otlp", "driver_otlp"}},
		{telemetrySource: "customer-specific-source", wantSources: []string{"other_otlp"}},
	}
	for _, test := range tests {
		capabilities := relaySourceCapabilities(test.telemetrySource)
		if len(capabilities) != len(test.wantSources) {
			t.Fatalf("source %q capabilities=%#v", test.telemetrySource, capabilities)
		}
		for index, capability := range capabilities {
			if capability.Source != test.wantSources[index] {
				t.Fatalf("source %q capability[%d]=%q, want %q", test.telemetrySource, index, capability.Source, test.wantSources[index])
			}
		}
	}
}

func TestRelayStartupCapabilitySummaryAndMetrics(t *testing.T) {
	t.Parallel()
	core, observed := observer.New(zapcore.InfoLevel)
	reader := sdkmetric.NewManualReader()
	provider := sdkmetric.NewMeterProvider(sdkmetric.WithReader(reader))
	config := createDefaultConfig().(*Config)
	config.GatewayID = "gateway-must-not-appear"
	processor, err := newProcessor(config, zap.New(core), provider)
	if err != nil {
		t.Fatal(err)
	}
	if err := processor.start(context.Background(), nil); err != nil {
		t.Fatal(err)
	}

	entries := observed.AllUntimed()
	if len(entries) != 1 || entries[0].Message != "OpenShell OTLP source capability summary" {
		t.Fatalf("startup entries=%#v", entries)
	}
	encoded, err := json.Marshal(entries[0].ContextMap())
	if err != nil {
		t.Fatal(err)
	}
	text := string(encoded)
	if strings.Contains(text, config.GatewayID) || !strings.Contains(text, `"source":"relay_otlp"`) {
		t.Fatalf("unexpected startup summary: %s", text)
	}

	var resourceMetrics metricdata.ResourceMetrics
	if err := reader.Collect(context.Background(), &resourceMetrics); err != nil {
		t.Fatal(err)
	}
	want := map[string]bool{
		"openshell.exporter.source.enabled":         false,
		"openshell.exporter.source.configured":      false,
		"openshell.exporter.source.observed":        false,
		"openshell.exporter.source.health_class":    false,
		"openshell.exporter.source.capability_info": false,
	}
	for _, scope := range resourceMetrics.ScopeMetrics {
		for _, metric := range scope.Metrics {
			if _, ok := want[metric.Name]; ok {
				want[metric.Name] = true
			}
		}
	}
	for name, found := range want {
		if !found {
			t.Fatalf("startup did not record metric %q", name)
		}
	}
}
