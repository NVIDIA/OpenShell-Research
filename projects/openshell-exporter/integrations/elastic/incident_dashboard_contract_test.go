// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package elastic_test

import (
	"bufio"
	"encoding/json"
	"os"
	"strings"
	"testing"
)

func TestIncidentInvestigationSavedObjects(t *testing.T) {
	t.Parallel()

	file, err := os.Open("assets/soc-investigation-saved-objects.ndjson")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = file.Close() })

	expected := map[string]string{
		"openshell-soc-latest-sandbox-state":   "visualization",
		"openshell-soc-incident-timeline":      "visualization",
		"openshell-soc-sandbox-provenance":     "search",
		"openshell-soc-incident-investigation": "dashboard",
		"openshell-soc-analyst-triage":         "dashboard",
	}
	seen := make(map[string]bool, len(expected))
	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		var object struct {
			ID         string `json:"id"`
			Type       string `json:"type"`
			Attributes struct {
				Title      string   `json:"title"`
				VisState   string   `json:"visState"`
				PanelsJSON string   `json:"panelsJSON"`
				Columns    []string `json:"columns"`
			} `json:"attributes"`
			References []struct {
				ID string `json:"id"`
			} `json:"references"`
		}
		if err := json.Unmarshal(scanner.Bytes(), &object); err != nil {
			t.Fatal(err)
		}
		if expected[object.ID] != object.Type || seen[object.ID] {
			t.Fatalf("unexpected or duplicate object %s/%s", object.Type, object.ID)
		}
		seen[object.ID] = true

		switch object.Type {
		case "visualization":
			var state struct {
				Type   string `json:"type"`
				Params struct {
					Spec string `json:"spec"`
				} `json:"params"`
			}
			if err := json.Unmarshal([]byte(object.Attributes.VisState), &state); err != nil {
				t.Fatal(err)
			}
			var spec map[string]any
			if state.Type != "vega" || json.Unmarshal([]byte(state.Params.Spec), &spec) != nil {
				t.Fatalf("%s is not a valid Vega object", object.ID)
			}
			for _, placeholder := range []string{
				"%dashboard_context-must_clause%",
				"%dashboard_context-filter_clause%",
				"%dashboard_context-must_not_clause%",
				"%timefilter%",
			} {
				if !strings.Contains(state.Params.Spec, placeholder) {
					t.Errorf("%s omits %s", object.ID, placeholder)
				}
			}
			for _, forbidden := range []string{
				"prompt.content",
				"response.content",
				"tool.arguments",
				"tool.result",
				"authorization",
				"credential",
			} {
				if strings.Contains(strings.ToLower(state.Params.Spec), forbidden) {
					t.Errorf("%s queries forbidden content %q", object.ID, forbidden)
				}
			}
			if object.ID == "openshell-soc-latest-sandbox-state" {
				for _, required := range []string{
					"openshell.sandbox.phase",
					"openshell.sandbox.resource_version",
					"CURRENT ≤5m",
					"STALE >5m",
					"UNKNOWN · SOURCE GAP",
				} {
					if !strings.Contains(state.Params.Spec, required) {
						t.Errorf("latest-state view omits %q", required)
					}
				}
			}
			if object.ID == "openshell-soc-incident-timeline" {
				for _, required := range []string{
					"Direct trace/session/request ID",
					"Sandbox context only",
					"openshell.acquisition.kind",
					"openshell.acquisition.durability",
					"openshell.cloud_event.source",
					"event.id",
				} {
					if !strings.Contains(state.Params.Spec, required) {
						t.Errorf("incident timeline omits %q", required)
					}
				}
			}
		case "search":
			for _, required := range []string{
				"@timestamp",
				"event.ingested",
				"event.id",
				"openshell.acquisition.kind",
				"openshell.acquisition.durability",
				"openshell.cloud_event.source",
				"openshell.validation.status",
				"openshell.redaction.applied",
			} {
				if !containsString(object.Attributes.Columns, required) {
					t.Errorf("provenance search omits %q", required)
				}
			}
		case "dashboard":
			var panels []map[string]any
			if err := json.Unmarshal([]byte(object.Attributes.PanelsJSON), &panels); err != nil {
				t.Fatal(err)
			}
			switch object.ID {
			case "openshell-soc-incident-investigation":
				if object.Attributes.Title != "OpenShell SOC - Alert-to-Sandbox Investigation" ||
					len(panels) != 6 || len(object.References) != 5 {
					t.Fatalf("incident dashboard topology is incomplete")
				}
			case "openshell-soc-analyst-triage":
				if object.Attributes.Title != "OpenShell SOC - Analyst Triage and Investigation" ||
					len(panels) != 11 || len(object.References) != 10 {
					t.Fatalf("analyst triage dashboard topology is incomplete")
				}
				for _, required := range []string{
					"/app/security/alerts",
					"/app/security/cases",
					"openshell.sandbox.id",
					"Missing evidence is a monitoring incident",
				} {
					if !strings.Contains(object.Attributes.PanelsJSON, required) {
						t.Errorf("analyst triage dashboard omits %q", required)
					}
				}
			}
		}
	}
	if err := scanner.Err(); err != nil {
		t.Fatal(err)
	}
	if len(seen) != len(expected) {
		t.Fatalf("seen=%v, want=%v", seen, expected)
	}
}

func containsString(values []string, want string) bool {
	for _, value := range values {
		if value == want {
			return true
		}
	}
	return false
}
