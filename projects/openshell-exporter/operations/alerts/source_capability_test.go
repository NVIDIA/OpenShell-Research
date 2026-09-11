// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package alerts

import (
	"os"
	"testing"

	"go.yaml.in/yaml/v3"
)

type sourceRuleFile struct {
	Groups []struct {
		Rules []sourceRule `yaml:"rules"`
	} `yaml:"groups"`
}

type sourceRule struct {
	Record      string            `yaml:"record"`
	Alert       string            `yaml:"alert"`
	Expr        string            `yaml:"expr"`
	For         string            `yaml:"for"`
	Annotations map[string]string `yaml:"annotations"`
}

func TestSourceCapabilityAlertContracts(t *testing.T) {
	t.Parallel()
	encoded, err := os.ReadFile("exporter.yaml")
	if err != nil {
		t.Fatal(err)
	}
	var file sourceRuleFile
	if err := yaml.Unmarshal(encoded, &file); err != nil {
		t.Fatal(err)
	}
	wanted := map[string]sourceRule{
		"OpenShellExporterSourceConfiguredButUnobserved": {
			Expr: "max by (source) (openshell_exporter_source_configured) == 1 and on (source) max by (source) (openshell_exporter_source_observed) == 0",
			For:  "10m", Annotations: map[string]string{"runbook": "docs/operations.md#source-inactive"},
		},
		"OpenShellExporterSourceStale": {
			Expr: "max by (source) (openshell_exporter_source_configured) == 1 and on (source) max by (source) (openshell_exporter_source_observed) == 1 and on (source) openshell_exporter:source_age_seconds > 600",
			For:  "5m", Annotations: map[string]string{"runbook": "docs/operations.md#source-inactive"},
		},
		"OpenShellExporterSourceGapReported": {
			Expr: "max by (source) (openshell_exporter_source_health_class) == 3",
			For:  "0m", Annotations: map[string]string{"runbook": "docs/operations.md#source-gap-reported"},
		},
	}
	foundRecording := false
	found := map[string]bool{}
	for _, group := range file.Groups {
		for _, rule := range group.Rules {
			if rule.Record == "openshell_exporter:source_age_seconds" {
				if rule.Expr != "max by (source) (time() - openshell_exporter_source_last_success_unixtime)" {
					t.Fatalf("source age expression=%q", rule.Expr)
				}
				foundRecording = true
			}
			expected, ok := wanted[rule.Alert]
			if !ok {
				continue
			}
			if rule.Expr != expected.Expr || rule.For != expected.For ||
				rule.Annotations["runbook"] != expected.Annotations["runbook"] {
				t.Fatalf("alert %q=%#v, want %#v", rule.Alert, rule, expected)
			}
			found[rule.Alert] = true
		}
	}
	if !foundRecording {
		t.Fatal("source age recording rule is missing")
	}
	for alert := range wanted {
		if !found[alert] {
			t.Fatalf("alert %q is missing", alert)
		}
	}
}
