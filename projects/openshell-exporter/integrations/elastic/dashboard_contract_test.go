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

func TestSOCDashboardExperienceContract(t *testing.T) {
	t.Parallel()

	file, err := os.Open("assets/soc-saved-objects.ndjson")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = file.Close() })

	typeCounts := make(map[string]int)
	dashboards := make(map[string]string)
	visualizations := make(map[string]string)
	allowedIndices := map[string]struct{}{
		"logs-openshell.security-*-v1":                             {},
		"traces-openshell.agent-*-v1":                              {},
		"logs-openshell.security-*-v1,traces-openshell.agent-*-v1": {},
	}
	wantDashboards := map[string]string{
		"openshell-soc-overview":      "OpenShell SOC - Agent Security Command Center",
		"openshell-soc-investigation": "OpenShell SOC - Agent Activity and Enforcement Timeline",
		"openshell-soc-trust":         "OpenShell SOC - Evidence Coverage and Trust",
	}
	wantVisualizations := map[string]string{
		"openshell-soc-evidence-flow":       "Security evidence over time",
		"openshell-soc-attention-now":       "What needs SOC attention now",
		"openshell-soc-evidence-mix":        "What OpenShell observed",
		"openshell-soc-denials-by-sandbox":  "Blocked actions by sandbox",
		"openshell-soc-agent-stages":        "Agent execution stages - privacy filtered",
		"openshell-soc-correlated-timeline": "Agent activity and enforcement timeline",
		"openshell-soc-trust-controls":      "Can the SOC trust this evidence?",
		"openshell-soc-delivery-latency":    "Delivery delay - source to Elastic",
	}

	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		if strings.TrimSpace(scanner.Text()) == "" {
			continue
		}
		var object struct {
			Type       string `json:"type"`
			ID         string `json:"id"`
			Attributes struct {
				Title           string `json:"title"`
				VisState        string `json:"visState"`
				PanelsJSON      string `json:"panelsJSON"`
				TimeRestore     bool   `json:"timeRestore"`
				TimeFrom        string `json:"timeFrom"`
				TimeTo          string `json:"timeTo"`
				RefreshInterval struct {
					Pause bool `json:"pause"`
					Value int  `json:"value"`
				} `json:"refreshInterval"`
			} `json:"attributes"`
			References []struct {
				Type string `json:"type"`
				ID   string `json:"id"`
			} `json:"references"`
		}
		if err := json.Unmarshal(scanner.Bytes(), &object); err != nil {
			t.Fatal(err)
		}
		typeCounts[object.Type]++
		switch object.Type {
		case "dashboard":
			dashboards[object.ID] = object.Attributes.Title
			if !json.Valid([]byte(object.Attributes.PanelsJSON)) || len(object.References) == 0 ||
				!object.Attributes.TimeRestore || object.Attributes.TimeFrom != "now-24h" ||
				object.Attributes.TimeTo != "now" || object.Attributes.RefreshInterval.Pause ||
				object.Attributes.RefreshInterval.Value != 30000 {
				t.Fatalf("dashboard %s has an invalid panel/reference graph", object.ID)
			}
		case "visualization":
			visualizations[object.ID] = object.Attributes.Title
			var state struct {
				Type   string `json:"type"`
				Params struct {
					Spec string `json:"spec"`
				} `json:"params"`
			}
			if err := json.Unmarshal([]byte(object.Attributes.VisState), &state); err != nil {
				t.Fatalf("visualization %s state: %v", object.ID, err)
			}
			var spec struct {
				Data struct {
					URL struct {
						Context   bool   `json:"%context%"`
						Timefield string `json:"%timefield%"`
						Index     string `json:"index"`
						Body      struct {
							Query json.RawMessage `json:"query"`
						} `json:"body"`
					} `json:"url"`
				} `json:"data"`
			}
			if state.Type != "vega" || json.Unmarshal([]byte(state.Params.Spec), &spec) != nil {
				t.Fatalf("visualization %s is not a valid Vega specification", object.ID)
			}
			if _, ok := allowedIndices[spec.Data.URL.Index]; !ok {
				t.Fatalf("visualization %s uses unsupported index %q", object.ID, spec.Data.URL.Index)
			}
			hasQuery := len(spec.Data.URL.Body.Query) > 0 && string(spec.Data.URL.Body.Query) != "null"
			if (spec.Data.URL.Context || spec.Data.URL.Timefield != "") && hasQuery {
				t.Fatalf("visualization %s combines automatic context/time with body.query", object.ID)
			}
			if hasQuery {
				for _, placeholder := range []string{
					"%dashboard_context-must_clause%",
					"%dashboard_context-filter_clause%",
					"%dashboard_context-must_not_clause%",
					"%timefilter%",
				} {
					if !strings.Contains(state.Params.Spec, placeholder) {
						t.Errorf("visualization %s query omits %s", object.ID, placeholder)
					}
				}
			}
			if object.ID == "openshell-soc-delivery-latency" &&
				(!strings.Contains(state.Params.Spec, "script_fields") ||
					!strings.Contains(state.Params.Spec, "delivery_delay_seconds")) {
				t.Fatal("delivery latency must be calculated by Elasticsearch")
			}
		}
	}
	if err := scanner.Err(); err != nil {
		t.Fatal(err)
	}
	if typeCounts["index-pattern"] != 2 || typeCounts["search"] != 4 ||
		typeCounts["visualization"] != 8 || typeCounts["dashboard"] != 3 {
		t.Fatalf("unexpected saved-object topology: %#v", typeCounts)
	}
	for id, title := range wantDashboards {
		if dashboards[id] != title {
			t.Errorf("dashboard %s=%q, want %q", id, dashboards[id], title)
		}
	}
	for id, title := range wantVisualizations {
		if visualizations[id] != title {
			t.Errorf("visualization %s=%q, want %q", id, visualizations[id], title)
		}
	}
}
