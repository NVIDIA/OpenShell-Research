// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package elastic_test

import (
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/santhosh-tekuri/jsonschema/v6"
)

func compileShiftReportSchema(t *testing.T) *jsonschema.Schema {
	t.Helper()
	encoded, err := os.ReadFile("shift-report.schema.json")
	if err != nil {
		t.Fatal(err)
	}
	var document map[string]any
	if err := json.Unmarshal(encoded, &document); err != nil {
		t.Fatal(err)
	}
	compiler := jsonschema.NewCompiler()
	if err := compiler.AddResource("urn:openshell:elastic-soc-shift-report:1.1", document); err != nil {
		t.Fatal(err)
	}
	compiled, err := compiler.Compile("urn:openshell:elastic-soc-shift-report:1.1")
	if err != nil {
		t.Fatal(err)
	}
	return compiled
}

func readShiftReport(t *testing.T, schema *jsonschema.Schema, path string) map[string]any {
	t.Helper()
	encoded, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var report map[string]any
	if err := json.Unmarshal(encoded, &report); err != nil {
		t.Fatal(err)
	}
	if err := schema.Validate(report); err != nil {
		t.Fatalf("validate shift report: %v", err)
	}
	return report
}

func TestShiftCheckerIsReadOnlyAndFailClosed(t *testing.T) {
	t.Parallel()
	body, err := os.ReadFile("shift-check.sh")
	if err != nil {
		t.Fatal(err)
	}
	script := string(body)
	for _, required := range []string{
		"OPENSHELL_SHIFT_REPORT_OUTPUT",
		"OPENSHELL_MAX_EVIDENCE_AGE_SECONDS",
		"OPENSHELL_AGENT_TRACES_ENABLED",
		"source_recent_document_count",
		"delivery_recent_document_count",
		"latest_source_timestamp",
		"latest_ingested_timestamp",
		"ELASTICSEARCH_ANALYST_AUTH_HEADER_FILE",
		"KIBANA_ANALYST_AUTH_HEADER_FILE",
		"execution_summary.last_execution.status",
		"api/cases/_find?owner=securitySolution",
		"refusing to replace existing shift report",
		"ln \"$work_dir/report.json\" \"$report_output\"",
		"No alert, case, rule, connector, evidence, or OpenShell state is changed.",
	} {
		if !strings.Contains(script, required) {
			t.Errorf("shift checker is missing %q", required)
		}
	}
	for _, forbidden := range []string{
		"--request POST",
		"--request PUT",
		"--request PATCH",
		"--request DELETE",
		"/_update",
		"/_bulk",
		"openshell gateway",
		"openshell policy",
	} {
		if strings.Contains(script, forbidden) {
			t.Errorf("read-only shift checker contains forbidden behavior %q", forbidden)
		}
	}
}

func TestShiftCheckerPublishesAppendOnlyOperationalEvidence(t *testing.T) {
	t.Parallel()
	if _, err := exec.LookPath("jq"); err != nil {
		t.Skip("jq is required for the shift-check integration contract")
	}
	tempDir := t.TempDir()
	binDir := filepath.Join(tempDir, "bin")
	if err := os.Mkdir(binDir, 0o700); err != nil {
		t.Fatal(err)
	}
	fakeCurl := `#!/usr/bin/env bash
set -euo pipefail
url=
for argument in "$@"; do
  case "$argument" in https://*) url=$argument ;; esac
done
case "$url" in
  https://es.test/logs-openshell.security-production-v1/_search)
    source_recent=5
    delivery_recent=5
    if [[ "${FAKE_STALE_SECURITY:-false}" == true ]]; then
      source_recent=0
      delivery_recent=0
    fi
    [[ "${FAKE_SOURCE_STALE_SECURITY:-false}" == false ]] || source_recent=0
    [[ "${FAKE_DELIVERY_STALE_SECURITY:-false}" == false ]] || delivery_recent=0
    printf '{"hits":{"total":{"value":120}},"aggregations":{"latest_source":{"value_as_string":"2026-08-21T12:00:00Z"},"latest_ingested":{"value_as_string":"2026-08-21T12:00:02Z"},"source_recent":{"doc_count":%s},"delivery_recent":{"doc_count":%s}}}\n' "$source_recent" "$delivery_recent"
    ;;
  https://es.test/traces-openshell.agent-production-v1/_search)
    printf '%s\n' '{"hits":{"total":{"value":18}},"aggregations":{"latest_source":{"value_as_string":"2026-08-21T12:00:00Z"},"latest_ingested":{"value_as_string":"2026-08-21T12:00:03Z"},"source_recent":{"doc_count":3},"delivery_recent":{"doc_count":3}}}'
    ;;
  https://kibana.test/s/openshell-production/api/detection_engine/rules?rule_id=*)
    rule_id=${url##*=}
    arrival_safe=true
    case "$rule_id" in
      openshell-policy-denial-burst-v1|openshell-agent-policy-denial-sequence-v1|openshell-policy-denial-destination-probe-v1|openshell-policy-change-denial-sequence-v1|openshell-denial-source-gap-sequence-v1) arrival_safe=false ;;
    esac
    if [[ "${FAKE_TIMESTAMP_DRIFT_RULE:-}" == "$rule_id" ]]; then
      arrival_safe=false
    fi
    actions='[]'
    case "$rule_id" in
      openshell-validation-failure-v1|openshell-source-gap-v1|openshell-policy-denial-burst-v1|openshell-agent-policy-denial-sequence-v1|openshell-policy-denial-destination-probe-v1|openshell-denial-source-gap-sequence-v1)
        body=$(jq -c --arg environment production '.environment = $environment' assets/soc-webhook-body.json)
        actions=$(jq -cn --arg body "$body" '[{action_type_id:".webhook",group:"default",id:"customer-soc-webhook",params:{body:$body},frequency:{summary:false,notifyWhen:"onActiveAlert",throttle:null}}]')
        ;;
    esac
    jq -cn --argjson actions "$actions" --argjson arrival_safe "$arrival_safe" '{enabled:true,execution_summary:{last_execution:{status:"succeeded"}},actions:$actions} + (if $arrival_safe then {timestamp_override:"event.ingested",timestamp_override_fallback_disabled:true} else {} end)'
    ;;
  https://kibana.test/s/openshell-production/api/actions/connector/customer-soc-webhook)
    printf '%s\n' '{"id":"customer-soc-webhook","connector_type_id":".webhook","is_deprecated":false,"is_connector_type_deprecated":false,"is_missing_secrets":false}'
    ;;
  'https://kibana.test/s/openshell-production/api/cases/_find?owner=securitySolution&perPage=1')
    printf '%s\n' '{"total":7,"count_open_cases":2,"count_in_progress_cases":1,"count_closed_cases":4,"cases":[]}'
    ;;
  *) printf 'unexpected fake curl URL: %s\n' "$url" >&2; exit 1 ;;
esac
`
	fakeCurlPath := filepath.Join(binDir, "curl")
	if err := os.WriteFile(fakeCurlPath, []byte(fakeCurl), 0o700); err != nil {
		t.Fatal(err)
	}
	writeSecret := func(name, contents string) string {
		t.Helper()
		path := filepath.Join(tempDir, name)
		if err := os.WriteFile(path, []byte(contents+"\n"), 0o600); err != nil {
			t.Fatal(err)
		}
		return path
	}
	ca := writeSecret("ca.pem", "test-ca")
	esHeader := writeSecret("es.header", "Authorization: Bearer analyst-es")
	kibanaHeader := writeSecret("kibana.header", "Authorization: Bearer analyst-kibana")
	reportPath := filepath.Join(tempDir, "shift-report.json")
	baseEnv := []string{
		"PATH=" + binDir + ":/usr/bin:/bin",
		"ELASTICSEARCH_URL=https://es.test",
		"KIBANA_URL=https://kibana.test",
		"ELASTIC_CA_FILE=" + ca,
		"ELASTICSEARCH_ANALYST_AUTH_HEADER_FILE=" + esHeader,
		"KIBANA_ANALYST_AUTH_HEADER_FILE=" + kibanaHeader,
		"OPENSHELL_SOC_WEBHOOK_CONNECTOR_ID=customer-soc-webhook",
		"OPENSHELL_AGENT_TRACES_ENABLED=true",
		"OPENSHELL_MAX_EVIDENCE_AGE_SECONDS=900",
	}
	command := exec.Command("./shift-check.sh", "run")
	command.Env = append(append([]string{}, baseEnv...), "OPENSHELL_SHIFT_REPORT_OUTPUT="+reportPath)
	output, err := command.CombinedOutput()
	if err != nil {
		t.Fatalf("shift check: %v\n%s", err, output)
	}
	if !strings.Contains(string(output), "shift check: passed") || !strings.Contains(string(output), "sha256:") {
		t.Fatalf("unexpected shift-check output:\n%s", output)
	}
	schema := compileShiftReportSchema(t)
	report := readShiftReport(t, schema, reportPath)
	if report["status"] != "passed" {
		t.Fatalf("status=%#v", report["status"])
	}
	checks := report["checks"].(map[string]any)
	security := checks["security_evidence"].(map[string]any)
	if checks["detections"].(map[string]any)["healthy_rules"] != float64(11) ||
		checks["escalation_routing"].(map[string]any)["verified_actions"] != float64(6) ||
		checks["cases"].(map[string]any)["open"] != float64(2) ||
		security["source_recent_document_count"] != float64(5) ||
		security["delivery_recent_document_count"] != float64(5) ||
		security["latest_source_timestamp"] != "2026-08-21T12:00:00Z" ||
		security["latest_ingested_timestamp"] != "2026-08-21T12:00:02Z" {
		t.Fatalf("unexpected checks: %#v", checks)
	}
	security["delivery_recent_document_count"] = float64(0)
	if err := schema.Validate(report); err == nil {
		t.Fatal("schema accepted a healthy lane without recent destination acceptance")
	}
	security["delivery_recent_document_count"] = float64(5)
	info, err := os.Stat(reportPath)
	if err != nil {
		t.Fatal(err)
	}
	if info.Mode().Perm() != 0o600 {
		t.Fatalf("report permissions=%#o, want 0600", info.Mode().Perm())
	}
	second := exec.Command("./shift-check.sh", "run")
	second.Env = command.Env
	secondOutput, secondErr := second.CombinedOutput()
	if secondErr == nil || !strings.Contains(string(secondOutput), "refusing to replace existing shift report") {
		t.Fatalf("second shift check did not fail closed: %v\n%s", secondErr, secondOutput)
	}

	freshnessFailures := []struct {
		name        string
		environment string
		wantStatus  string
		wantFailure string
	}{
		{name: "both-stale", environment: "FAKE_STALE_SECURITY=true", wantStatus: "stale", wantFailure: "source and destination acceptance are stale"},
		{name: "source-stale", environment: "FAKE_SOURCE_STALE_SECURITY=true", wantStatus: "source_stale", wantFailure: "source activity is stale"},
		{name: "delivery-stale", environment: "FAKE_DELIVERY_STALE_SECURITY=true", wantStatus: "delivery_stale", wantFailure: "destination acceptance is stale"},
	}
	for _, testCase := range freshnessFailures {
		failurePath := filepath.Join(tempDir, testCase.name+"-shift-report.json")
		failure := exec.Command("./shift-check.sh", "run")
		failure.Env = append(append([]string{}, baseEnv...),
			"OPENSHELL_SHIFT_REPORT_OUTPUT="+failurePath,
			testCase.environment,
		)
		failureOutput, failureErr := failure.CombinedOutput()
		if failureErr == nil || !strings.Contains(string(failureOutput), "shift check: failed") {
			t.Fatalf("%s evidence did not fail closed: %v\n%s", testCase.name, failureErr, failureOutput)
		}
		failureReport := readShiftReport(t, schema, failurePath)
		lane := failureReport["checks"].(map[string]any)["security_evidence"].(map[string]any)
		failures := strings.Join(anyStrings(failureReport["failures"].([]any)), " ")
		if failureReport["status"] != "failed" || lane["status"] != testCase.wantStatus ||
			!strings.Contains(failures, testCase.wantFailure) {
			t.Fatalf("%s report=%#v", testCase.name, failureReport)
		}
	}

	driftPath := filepath.Join(tempDir, "timestamp-drift-shift-report.json")
	drift := exec.Command("./shift-check.sh", "run")
	drift.Env = append(append([]string{}, baseEnv...),
		"OPENSHELL_SHIFT_REPORT_OUTPUT="+driftPath,
		"FAKE_TIMESTAMP_DRIFT_RULE=openshell-policy-denial-v1",
	)
	driftOutput, driftErr := drift.CombinedOutput()
	if driftErr == nil || !strings.Contains(string(driftOutput), "shift check: failed") {
		t.Fatalf("timestamp drift did not fail closed: %v\n%s", driftErr, driftOutput)
	}
	driftReport := readShiftReport(t, schema, driftPath)
	if driftReport["status"] != "failed" ||
		!strings.Contains(strings.Join(anyStrings(driftReport["failures"].([]any)), " "), "detection rule") {
		t.Fatalf("timestamp drift report=%#v", driftReport)
	}
}

func anyStrings(values []any) []string {
	result := make([]string, 0, len(values))
	for _, value := range values {
		result = append(result, value.(string))
	}
	return result
}
