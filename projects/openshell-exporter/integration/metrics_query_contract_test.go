// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package integration

import (
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
)

var (
	otelInstrumentPattern   = regexp.MustCompile(`openshell\.exporter\.[a-z0-9_.]+`)
	prometheusMetricPattern = regexp.MustCompile(`\bopenshell_exporter_[a-z0-9_]+\b`)
)

func TestCustomMetricConsumersReferenceImplementedInstruments(t *testing.T) {
	implemented := make(map[string]struct{})
	for _, relative := range []string{
		"../exporter/cloudeventsexporter/metrics.go",
		"../extension/storagehealthextension/extension.go",
		"../processor/openshellprocessor/metrics.go",
		"../processor/relayprocessor/metrics.go",
		"../receiver/watchsandboxreceiver/metrics.go",
	} {
		encoded, err := os.ReadFile(filepath.Clean(relative))
		if err != nil {
			t.Fatal(err)
		}
		for _, instrument := range otelInstrumentPattern.FindAllString(string(encoded), -1) {
			implemented[strings.ReplaceAll(instrument, ".", "_")] = struct{}{}
		}
	}
	if len(implemented) < 25 {
		t.Fatalf("found only %d implemented custom metrics", len(implemented))
	}

	for _, relative := range []string{
		"../docs/operations.md",
		"../examples/demo/real-gateway/monitoring/grafana/dashboards/openshell-exporter-operations.json",
		"../operations/alerts/exporter.yaml",
	} {
		encoded, err := os.ReadFile(filepath.Clean(relative))
		if err != nil {
			t.Fatal(err)
		}
		references := prometheusMetricPattern.FindAllString(string(encoded), -1)
		if len(references) == 0 {
			t.Errorf("%s contains no custom metric references", relative)
			continue
		}
		for _, reference := range references {
			base := reference
			for _, suffix := range []string{"_bucket", "_count", "_sum"} {
				base = strings.TrimSuffix(base, suffix)
			}
			if _, ok := implemented[base]; !ok {
				t.Errorf("%s references custom metric %q without a matching instrument", relative, reference)
			}
		}
	}
}
