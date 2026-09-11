// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package elastic_test

import (
	"bufio"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/santhosh-tekuri/jsonschema/v6"
)

type manifest struct {
	SchemaVersion                 string `json:"schema_version"`
	PackVersion                   string `json:"pack_version"`
	ValidatedElasticsearchVersion string `json:"validated_elasticsearch_version"`
	ValidatedKibanaVersion        string `json:"validated_kibana_version"`
	CloudEventsSchema             string `json:"cloud_events_schema"`
	OCSFVersion                   string `json:"ocsf_version"`
	RuleCount                     int    `json:"rule_count"`
	SavedObjectCount              int    `json:"saved_object_count"`
	RoleCount                     int    `json:"role_count"`
}

func assertConformanceHashes(t *testing.T, evidence map[string]any) {
	t.Helper()

	integration, ok := evidence["integration"].(map[string]any)
	if !ok {
		t.Fatalf("integration=%#v", evidence["integration"])
	}
	for field, path := range map[string]string{
		"asset_set_sha256":           "assets.sha256",
		"manifest_sha256":            "manifest.json",
		"verifier_sha256":            "verify.sh",
		"action_binder_sha256":       "bind-actions.sh",
		"schema_sha256":              "conformance.schema.json",
		"shift_checker_sha256":       "shift-check.sh",
		"shift_report_schema_sha256": "shift-report.schema.json",
	} {
		body, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		sum := sha256.Sum256(body)
		expected := hex.EncodeToString(sum[:])
		if integration[field] != expected {
			t.Fatalf("integration[%q]=%#v, want %q", field, integration[field], expected)
		}
	}
}

func TestProductionPackManifestAndChecksums(t *testing.T) {
	t.Parallel()

	encoded, err := os.ReadFile("manifest.json")
	if err != nil {
		t.Fatal(err)
	}
	var pack manifest
	if err := json.Unmarshal(encoded, &pack); err != nil {
		t.Fatal(err)
	}
	if pack.SchemaVersion != "1.0" || pack.PackVersion != "1.10.0" ||
		pack.ValidatedElasticsearchVersion != "9.4.3" ||
		pack.ValidatedKibanaVersion != "9.4.3" ||
		pack.CloudEventsSchema != "1.0" || pack.OCSFVersion != "1.8.0" ||
		pack.RuleCount != 11 || pack.SavedObjectCount != 26 || pack.RoleCount != 3 {
		t.Fatalf("unexpected production pack manifest: %+v", pack)
	}

	checksumFile, err := os.Open("assets.sha256")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = checksumFile.Close() })

	seen := make(map[string]struct{})
	scanner := bufio.NewScanner(checksumFile)
	for scanner.Scan() {
		fields := strings.Fields(scanner.Text())
		if len(fields) != 2 {
			t.Fatalf("malformed checksum line %q", scanner.Text())
		}
		path := filepath.Clean(fields[1])
		if !strings.HasPrefix(path, "assets/") || strings.Contains(path, "..") {
			t.Fatalf("unsafe checksum path %q", path)
		}
		if _, duplicate := seen[path]; duplicate {
			t.Fatalf("duplicate checksum path %q", path)
		}
		seen[path] = struct{}{}
		body, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		sum := sha256.Sum256(body)
		if hex.EncodeToString(sum[:]) != fields[0] {
			t.Fatalf("checksum mismatch for %s", path)
		}
		if strings.HasSuffix(path, ".json") && !json.Valid(body) {
			t.Fatalf("%s is not valid JSON", path)
		}
	}
	if err := scanner.Err(); err != nil {
		t.Fatal(err)
	}
	if len(seen) != 18 {
		t.Fatalf("reviewed asset count = %d, want 18", len(seen))
	}
}

func TestSOCRoleSeparationContract(t *testing.T) {
	t.Parallel()

	type indexPrivileges struct {
		Privileges []string `json:"privileges"`
	}
	type roleAsset struct {
		Elasticsearch struct {
			Cluster []string          `json:"cluster"`
			Indices []indexPrivileges `json:"indices"`
			RunAs   []string          `json:"run_as"`
		} `json:"elasticsearch"`
		Kibana []struct {
			Base    []string            `json:"base"`
			Feature map[string][]string `json:"feature"`
			Spaces  []string            `json:"spaces"`
		} `json:"kibana"`
	}

	profiles := map[string]map[string]string{
		"soc-viewer-role.json": {
			"discover_v2": "read", "dashboard_v2": "read", "siemV5": "read",
			"securitySolutionTimeline": "read", "securitySolutionNotes": "read",
			"securitySolutionAlertsV1": "read", "securitySolutionCasesV3": "read",
			"securitySolutionRulesV4": "read",
		},
		"soc-analyst-role.json": {
			"discover_v2": "read", "dashboard_v2": "read", "siemV5": "read",
			"securitySolutionTimeline": "all", "securitySolutionNotes": "all",
			"securitySolutionAlertsV1": "all", "securitySolutionCasesV3": "all",
			"securitySolutionRulesV4": "read", "actions": "read",
		},
		"soc-detection-engineer-role.json": {
			"discover_v2": "read", "dashboard_v2": "read", "siemV5": "read",
			"securitySolutionTimeline": "all", "securitySolutionNotes": "all",
			"securitySolutionAlertsV1": "all", "securitySolutionCasesV3": "all",
			"securitySolutionRulesV4": "all", "actions": "read",
		},
	}

	for file, expected := range profiles {
		file, expected := file, expected
		t.Run(file, func(t *testing.T) {
			encoded, err := os.ReadFile(filepath.Join("assets", file))
			if err != nil {
				t.Fatal(err)
			}
			var role roleAsset
			if err := json.Unmarshal(encoded, &role); err != nil {
				t.Fatal(err)
			}
			if len(role.Elasticsearch.Cluster) != 0 || len(role.Elasticsearch.RunAs) != 0 ||
				len(role.Elasticsearch.Indices) != 1 ||
				strings.Join(role.Elasticsearch.Indices[0].Privileges, ",") != "read,view_index_metadata" {
				t.Fatalf("%s has unsafe Elasticsearch privileges: %+v", file, role.Elasticsearch)
			}
			if len(role.Kibana) != 1 || len(role.Kibana[0].Base) != 0 ||
				len(role.Kibana[0].Feature) != len(expected) {
				t.Fatalf("%s has unexpected Kibana privilege shape: %+v", file, role.Kibana)
			}
			for feature, access := range expected {
				actual := role.Kibana[0].Feature[feature]
				if len(actual) != 1 || actual[0] != access {
					t.Errorf("%s feature %s=%v, want [%s]", file, feature, actual, access)
				}
			}
		})
	}
}

func TestProductionInstallerSafetyContract(t *testing.T) {
	t.Parallel()

	body, err := os.ReadFile("install.sh")
	if err != nil {
		t.Fatal(err)
	}
	script := string(body)
	for _, required := range []string{
		"plan|platform|rules|apply",
		"OPENSHELL_CHANGE_TICKET",
		"ELASTICSEARCH_BOOTSTRAP_AUTH_HEADER_FILE",
		"KIBANA_BOOTSTRAP_AUTH_HEADER_FILE",
		"KIBANA_DETECTION_AUTH_HEADER_FILE",
		"Authorization: (ApiKey|Bearer)",
		"must not be group/world accessible",
		"Elasticsearch and Kibana bootstrap identities must be distinct",
		"detection and Kibana bootstrap identities must be distinct",
		"require_https_url",
		"logs-openshell.security-${environment}-v1",
		"traces-openshell.agent-${environment}-v1",
		"openshell_ingest_${role_suffix}",
		".indices[0].names = [$security, $trace]",
		".kibana[].spaces = [$space]",
		"resource_already_exists_exception",
		"Environment: \" + $environment",
		"existing_status",
		"No identity, credential, index, rule, or saved object is deleted.",
	} {
		if !strings.Contains(script, required) {
			t.Errorf("production installer is missing %q", required)
		}
	}
	for _, forbidden := range []string{
		"--request DELETE",
		"ELASTIC_PASSWORD",
		"KIBANA_PASSWORD",
		"--user ",
		"http://",
		"/_ilm/policy",
		"/_slm/policy",
		"openshell gateway",
	} {
		if strings.Contains(script, forbidden) {
			t.Errorf("production installer contains forbidden behavior %q", forbidden)
		}
	}
}

func TestProductionVerifierIsReadOnlyAndFailClosed(t *testing.T) {
	t.Parallel()

	body, err := os.ReadFile("verify.sh")
	if err != nil {
		t.Fatal(err)
	}
	script := string(body)
	for _, required := range []string{
		"OPENSHELL_MIN_ELASTIC_NODES:-3",
		"OPENSHELL_MIN_ELASTIC_DATA_NODES:-2",
		".status == \"green\"",
		"OPENSHELL_SNAPSHOT_REPOSITORY",
		"OPENSHELL_RETENTION_APPROVAL",
		"OPENSHELL_SECURITY_LIFECYCLE_POLICY",
		"OPENSHELL_AGENT_LIFECYCLE_POLICY",
		"index lifecycle management is not running",
		"index lifecycle policy is not healthy",
		"indices_lifecycle_managed",
		"ELASTICSEARCH_INGEST_AUTH_HEADER_FILE",
		"_security/user/_has_privileges",
		".cluster.manage_security == false",
		".cluster.manage_index_templates == false",
		".index[$index].create_doc == true",
		".index[$index].read == false",
		".index[$index].delete_index == false",
		"execution_summary.last_execution.status",
		"timestamp_override",
		"timestamp_override_fallback_disabled",
		"api_key_owner == $owner",
		"OPENSHELL_DETECTION_PRINCIPAL",
		"openshell_soc_viewer_${role_suffix}",
		"soc_role_separation_verified",
		"runtime ingest identity is a superuser",
		"read and runtime ingest identities must be distinct",
	} {
		if !strings.Contains(script, required) {
			t.Errorf("production verifier is missing %q", required)
		}
	}
	for _, forbidden := range []string{
		"--request PUT",
		"--request DELETE",
		"/_bulk",
		"/_doc",
		"ELASTIC_PASSWORD",
		"--user ",
		"http://",
	} {
		if strings.Contains(script, forbidden) {
			t.Errorf("read-only production verifier contains forbidden behavior %q", forbidden)
		}
	}

	ruleFile, err := os.Open("assets/soc-rules.ndjson")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = ruleFile.Close() })
	rules := 0
	ruleScanner := bufio.NewScanner(ruleFile)
	for ruleScanner.Scan() {
		if strings.TrimSpace(ruleScanner.Text()) == "" {
			continue
		}
		var rule map[string]any
		if err := json.Unmarshal(ruleScanner.Bytes(), &rule); err != nil {
			t.Fatal(err)
		}
		if rule["enabled"] != true {
			t.Fatalf("rule %v is not enabled", rule["rule_id"])
		}
		ruleType, _ := rule["type"].(string)
		override, hasOverride := rule["timestamp_override"]
		fallback, hasFallback := rule["timestamp_override_fallback_disabled"]
		if ruleType == "query" {
			if override != "event.ingested" || fallback != true {
				t.Fatalf("event-level rule %v has timestamp override=%v fallback=%v", rule["rule_id"], override, fallback)
			}
		} else if hasOverride || hasFallback {
			t.Fatalf("temporal aggregate rule %v unexpectedly overrides its timestamp", rule["rule_id"])
		}
		ruleID, _ := rule["rule_id"].(string)
		query, _ := rule["query"].(string)
		fields, _ := rule["investigation_fields"].(map[string]any)
		fieldNames, _ := fields["field_names"].([]any)
		hasField := func(want string) bool {
			for _, field := range fieldNames {
				if field == want {
					return true
				}
			}
			return false
		}
		switch ruleID {
		case "openshell-policy-denial-destination-probe-v1":
			threshold, _ := rule["threshold"].(map[string]any)
			cardinality, _ := threshold["cardinality"].([]any)
			if threshold["value"] != float64(3) || len(cardinality) != 1 {
				t.Fatalf("destination-probe threshold is incomplete: %#v", threshold)
			}
			cardinalityRule, _ := cardinality[0].(map[string]any)
			if cardinalityRule["field"] != "destination.address" || cardinalityRule["value"] != float64(3) {
				t.Fatalf("destination-probe cardinality is incomplete: %#v", cardinalityRule)
			}
			for _, required := range []string{"event.id", "openshell.sandbox.id", "destination.address", "openshell.policy.version"} {
				if !hasField(required) {
					t.Errorf("destination-probe investigation omits %q", required)
				}
			}
		case "openshell-policy-change-denial-sequence-v1":
			if !strings.Contains(query, "maxspan=15m") ||
				strings.Index(query, "policy-change") >= strings.LastIndex(query, "policy-denial") {
				t.Fatalf("policy-change sequence does not preserve expected order: %s", query)
			}
		case "openshell-denial-source-gap-sequence-v1":
			if rule["severity"] != "critical" || rule["risk_score"] != float64(91) ||
				!strings.Contains(query, "maxspan=5m") ||
				strings.Index(query, "policy-denial") >= strings.LastIndex(query, "source-gap") {
				t.Fatalf("denial-gap sequence is incomplete: %#v", rule)
			}
		}
		rules++
	}
	if err := ruleScanner.Err(); err != nil {
		t.Fatal(err)
	}
	if rules != 11 {
		t.Fatalf("rule count = %d, want 11", rules)
	}
}

func TestDemoConsumesCanonicalProductionAssets(t *testing.T) {
	t.Parallel()

	compose, err := os.ReadFile("../../examples/demo/real-gateway/compose.elastic.yaml")
	if err != nil {
		t.Fatal(err)
	}
	for _, asset := range []string{
		"component-template.json",
		"index-template.json",
		"trace-component-template.json",
		"trace-index-template.json",
		"ingest-role.json",
		"soc-rules.ndjson",
		"soc-saved-objects.ndjson",
		"soc-investigation-saved-objects.ndjson",
		"policy-engine-saved-objects.ndjson",
		"soc-viewer-role.json",
		"soc-analyst-role.json",
		"soc-detection-engineer-role.json",
		"soc-webhook-body.json",
	} {
		expected := "../../../integrations/elastic/assets/" + asset
		if !strings.Contains(string(compose), expected) {
			t.Errorf("demo does not consume canonical asset %s", asset)
		}
	}
}

func TestConformanceEvidenceContract(t *testing.T) {
	t.Parallel()

	schemaEncoded, err := os.ReadFile("conformance.schema.json")
	if err != nil {
		t.Fatal(err)
	}
	var schemaDocument map[string]any
	if err := json.Unmarshal(schemaEncoded, &schemaDocument); err != nil {
		t.Fatal(err)
	}
	if schemaDocument["$id"] != "urn:openshell:elastic-soc-conformance:1" {
		t.Fatalf("schema id=%#v", schemaDocument["$id"])
	}
	compiler := jsonschema.NewCompiler()
	if err := compiler.AddResource(
		"urn:openshell:elastic-soc-conformance:1",
		schemaDocument,
	); err != nil {
		t.Fatal(err)
	}
	compiled, err := compiler.Compile("urn:openshell:elastic-soc-conformance:1")
	if err != nil {
		t.Fatal(err)
	}

	exampleEncoded, err := os.ReadFile("conformance.example.json")
	if err != nil {
		t.Fatal(err)
	}
	var example map[string]any
	if err := json.Unmarshal(exampleEncoded, &example); err != nil {
		t.Fatal(err)
	}
	if err := compiled.Validate(example); err != nil {
		t.Fatalf("validate conformance example: %v", err)
	}
	assertConformanceHashes(t, example)
	external := example["external_gates"].(map[string]any)
	external["status"] = "passed"
	if err := compiled.Validate(example); err == nil {
		t.Fatal("schema accepted a production-readiness claim for unexecuted external gates")
	}

	body, err := os.ReadFile("verify.sh")
	if err != nil {
		t.Fatal(err)
	}
	script := string(body)
	for _, required := range []string{
		"OPENSHELL_EVIDENCE_OUTPUT",
		"evidence is append-only",
		"ln \"$temporary_output\" \"$evidence_output\"",
		"chmod 0600 \"$temporary_output\"",
		"asset_set_sha256",
		"manifest_sha256",
		"verifier_sha256",
		"action_binder_sha256",
		"schema_sha256",
		"lifecycle_management_running",
		"shift_checker_sha256",
		"shift_report_schema_sha256",
		"indices_lifecycle_managed",
		"security_lifecycle_policy",
		"agent_lifecycle_policy",
		`external_gates: {`,
		`status: "unexecuted"`,
		"evidence sha256",
	} {
		if !strings.Contains(script, required) {
			t.Errorf("conformance evidence writer is missing %q", required)
		}
	}
}

func TestVerifierPublishesAppendOnlyEvidence(t *testing.T) {
	t.Parallel()

	if _, err := exec.LookPath("jq"); err != nil {
		t.Skip("jq is required for the verifier integration contract")
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
  case "$argument" in
    https://*) url=$argument ;;
  esac
done
case "$url" in
  https://es.test/)
    printf '%s
' '{"version":{"number":"9.4.3"}}'
    ;;
  */_cluster/health)
    printf '%s
' '{"status":"green","number_of_nodes":3,"number_of_data_nodes":2,"timed_out":false}'
    ;;
  */_cluster/health/*)
    printf '%s
' '{"status":"green","active_primary_shards":1,"unassigned_shards":0}'
    ;;
  */_snapshot/security-evidence)
    printf '%s
' '{"security-evidence":{}}'
    ;;
  */_ilm/status)
    printf '%s
' '{"operation_mode":"RUNNING"}'
    ;;
  */_ilm/policy/openshell-security-production)
    printf '%s
' '{"openshell-security-production":{"version":1,"policy":{"phases":{"hot":{"actions":{}}}}}}'
    ;;
  */_ilm/policy/openshell-agent-production)
    printf '%s
' '{"openshell-agent-production":{"version":1,"policy":{"phases":{"hot":{"actions":{}}}}}}'
    ;;
  */_ilm/explain)
    path=${url#https://es.test/}
    index=${path%%/*}
    case "$index" in
      logs-openshell.security-*) policy=openshell-security-production ;;
      traces-openshell.agent-*) policy=openshell-agent-production ;;
      *) printf 'unexpected ILM index: %s\n' "$index" >&2; exit 1 ;;
    esac
    printf '{"indices":{"%s":{"index":"%s","managed":true,"policy":"%s","phase":"hot","action":"complete","step":"complete"}}}
' "$index" "$index" "$policy"
    ;;
  */_component_template/*)
    name=${url##*/}
    printf '{"component_templates":[{"name":"%s","component_template":{"version":1,"_meta":{"managed_by":"openshell-event-exporter-reference"}}}]}
' "$name"
    ;;
  */_index_template/*)
    name=${url##*/}
    printf '{"index_templates":[{"name":"%s","index_template":{"version":1,"_meta":{"managed_by":"openshell-event-exporter-reference"}}}]}
' "$name"
    ;;
  */_settings?flat_settings=true)
    path=${url#https://es.test/}
    index=${path%%/*}
    case "$index" in
      logs-openshell.security-*) policy=openshell-security-production ;;
      traces-openshell.agent-*) policy=openshell-agent-production ;;
      *) printf 'unexpected settings index: %s\n' "$index" >&2; exit 1 ;;
    esac
    printf '{"%s":{"settings":{"index.number_of_replicas":"1","index.lifecycle.name":"%s"}}}
' "$index" "$policy"
    ;;
  */_security/role/openshell_ingest_production)
    printf '%s
' '{"openshell_ingest_production":{"cluster":["monitor"],"indices":[{"names":["logs-openshell.security-production-v1","traces-openshell.agent-production-v1"],"privileges":["create_doc","view_index_metadata"]}],"applications":[],"run_as":[]}}'
    ;;
  */_security/_authenticate)
    printf '%s
' '{"username":"openshell_ingest","roles":["openshell_ingest_production"]}'
    ;;
  */_security/user/_has_privileges)
    printf '%s
' '{"has_all_requested":false,"cluster":{"monitor":true,"manage_security":false,"manage_index_templates":false},"index":{"logs-openshell.security-production-v1":{"create_doc":true,"view_index_metadata":true,"read":false,"write":false,"delete":false,"delete_index":false,"manage":false},"traces-openshell.agent-production-v1":{"create_doc":true,"view_index_metadata":true,"read":false,"write":false,"delete":false,"delete_index":false,"manage":false}}}'
    ;;
  */api/status)
    printf '%s
' '{"version":{"number":"9.4.3"},"status":{"overall":{"level":"available"}}}'
    ;;
  */api/security/role/openshell_soc_viewer_production)
    printf '%s
' '{"elasticsearch":{"cluster":[],"indices":[{"names":["logs-openshell.security-production-v1","traces-openshell.agent-production-v1"],"privileges":["read","view_index_metadata"]}],"run_as":[]},"kibana":[{"base":[],"spaces":["openshell-production"],"feature":{"discover_v2":["read"],"dashboard_v2":["read"],"siemV5":["read"],"securitySolutionTimeline":["read"],"securitySolutionNotes":["read"],"securitySolutionAlertsV1":["read"],"securitySolutionCasesV3":["read"],"securitySolutionRulesV4":["read"]}}]}'
    ;;
  */api/security/role/openshell_soc_analyst_production)
    printf '%s
' '{"elasticsearch":{"cluster":[],"indices":[{"names":["logs-openshell.security-production-v1","traces-openshell.agent-production-v1"],"privileges":["read","view_index_metadata"]}],"run_as":[]},"kibana":[{"base":[],"spaces":["openshell-production"],"feature":{"discover_v2":["read"],"dashboard_v2":["read"],"siemV5":["read"],"securitySolutionTimeline":["all"],"securitySolutionNotes":["all"],"securitySolutionAlertsV1":["all"],"securitySolutionCasesV3":["all"],"securitySolutionRulesV4":["read"],"actions":["read"]}}]}'
    ;;
  */api/security/role/openshell_soc_detection_engineer_production)
    printf '%s
' '{"elasticsearch":{"cluster":[],"indices":[{"names":["logs-openshell.security-production-v1","traces-openshell.agent-production-v1"],"privileges":["read","view_index_metadata"]}],"run_as":[]},"kibana":[{"base":[],"spaces":["openshell-production"],"feature":{"discover_v2":["read"],"dashboard_v2":["read"],"siemV5":["read"],"securitySolutionTimeline":["all"],"securitySolutionNotes":["all"],"securitySolutionAlertsV1":["all"],"securitySolutionCasesV3":["all"],"securitySolutionRulesV4":["all"],"actions":["read"]}}]}'
    ;;
  */api/data_views/data_view/openshell-security)
    printf '%s
' '{"data_view":{"id":"openshell-security","title":"logs-openshell.security-production-v1","timeFieldName":"@timestamp"}}'
    ;;
  */api/data_views/data_view/openshell-agent-traces)
    printf '%s
' '{"data_view":{"id":"openshell-agent-traces","title":"traces-openshell.agent-production-v1","timeFieldName":"@timestamp"}}'
    ;;
  */api/saved_objects/dashboard/*)
    dashboard_id=${url##*/}
    case "$dashboard_id" in
      openshell-soc-overview)
        title='OpenShell SOC - Agent Security Command Center'; panels=7; references=6
        ;;
      openshell-soc-investigation)
        title='OpenShell SOC - Agent Activity and Enforcement Timeline'; panels=4; references=3
        ;;
      openshell-soc-incident-investigation)
        title='OpenShell SOC - Alert-to-Sandbox Investigation'; panels=6; references=5
        ;;
      openshell-soc-analyst-triage)
        title='OpenShell SOC - Analyst Triage and Investigation'; panels=11; references=10
        ;;
      openshell-soc-trust)
        title='OpenShell SOC - Evidence Coverage and Trust'; panels=4; references=3
        ;;
      *) printf 'unexpected dashboard: %s\n' "$dashboard_id" >&2; exit 1 ;;
    esac
    panels_json=$(jq -cn --argjson count "$panels" '[range(0;$count) | {}]')
    references_json=$(jq -cn --argjson count "$references" '[range(0;$count) | {id:("ref-" + (.|tostring))}]')
    jq -cn --arg id "$dashboard_id" --arg title "$title" --arg panels "$panels_json" --argjson references "$references_json" '{id:$id,attributes:{title:$title,panelsJSON:$panels},references:$references}'
    ;;
  */api/detection_engine/rules?rule_id=*)
    rule_id=${url##*=}
    version=2
    timestamp_fields=',"timestamp_override":"event.ingested","timestamp_override_fallback_disabled":true'
    case "$rule_id" in
      openshell-policy-denial-burst-v1|openshell-policy-denial-destination-probe-v1|openshell-policy-change-denial-sequence-v1|openshell-denial-source-gap-sequence-v1) version=1; timestamp_fields= ;;
      openshell-agent-policy-denial-sequence-v1) version=3; timestamp_fields= ;;
    esac
    if [[ "${FAKE_TIMESTAMP_DRIFT_RULE:-}" == "$rule_id" ]]; then
      timestamp_fields=
    fi
    printf '{"enabled":true,"version":%s,"tags":["Environment: production"],"index":["logs-openshell.security-production-v1","traces-openshell.agent-production-v1"],"execution_summary":{"last_execution":{"status":"succeeded"}},"id":"rule-object"%s}\n' "$version" "$timestamp_fields"
    ;;
  */api/alerting/rule/rule-object)
    printf '%s
' '{"api_key_owner":"openshell-detection-production","api_key_created_by_user":false}'
    ;;
  *)
    printf 'unexpected fake curl URL: %s
' "$url" >&2
    exit 1
    ;;
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
	caPath := writeSecret("ca.pem", "test-ca")
	esRead := writeSecret("es-read.header", "Authorization: Bearer es-reader")
	kibanaRead := writeSecret("kibana-read.header", "Authorization: Bearer kibana-reader")
	esIngest := writeSecret("es-ingest.header", "Authorization: Bearer es-ingest")
	evidencePath := filepath.Join(tempDir, "conformance.json")

	command := exec.Command("./verify.sh")
	command.Env = []string{
		"PATH=" + binDir + ":/usr/bin:/bin",
		"ELASTICSEARCH_URL=https://es.test",
		"KIBANA_URL=https://kibana.test",
		"ELASTIC_CA_FILE=" + caPath,
		"ELASTICSEARCH_READ_AUTH_HEADER_FILE=" + esRead,
		"KIBANA_READ_AUTH_HEADER_FILE=" + kibanaRead,
		"OPENSHELL_SECURITY_LIFECYCLE_POLICY=openshell-security-production",
		"OPENSHELL_AGENT_LIFECYCLE_POLICY=openshell-agent-production",
		"ELASTICSEARCH_INGEST_AUTH_HEADER_FILE=" + esIngest,
		"OPENSHELL_SNAPSHOT_REPOSITORY=security-evidence",
		"OPENSHELL_RETENTION_APPROVAL=PRIVACY-456",
		"OPENSHELL_DETECTION_PRINCIPAL=openshell-detection-production",
		"OPENSHELL_EVIDENCE_OUTPUT=" + evidencePath,
	}
	output, err := command.CombinedOutput()
	if err != nil {
		t.Fatalf("first conformance run: %v\n%s", err, output)
	}
	if !strings.Contains(string(output), "OpenShell Elastic SOC conformance passed") ||
		!strings.Contains(string(output), "evidence sha256:") {
		t.Fatalf("unexpected conformance output:\n%s", output)
	}

	evidenceEncoded, err := os.ReadFile(evidencePath)
	if err != nil {
		t.Fatal(err)
	}
	var evidence map[string]any
	if err := json.Unmarshal(evidenceEncoded, &evidence); err != nil {
		t.Fatal(err)
	}
	schemaEncoded, err := os.ReadFile("conformance.schema.json")
	if err != nil {
		t.Fatal(err)
	}
	var schemaDocument map[string]any
	if err := json.Unmarshal(schemaEncoded, &schemaDocument); err != nil {
		t.Fatal(err)
	}
	compiler := jsonschema.NewCompiler()
	if err := compiler.AddResource(
		"urn:openshell:elastic-soc-conformance:1",
		schemaDocument,
	); err != nil {
		t.Fatal(err)
	}
	compiled, err := compiler.Compile("urn:openshell:elastic-soc-conformance:1")
	if err != nil {
		t.Fatal(err)
	}
	if err := compiled.Validate(evidence); err != nil {
		t.Fatalf("validate generated evidence: %v", err)
	}
	assertConformanceHashes(t, evidence)
	controls, ok := evidence["controls"].(map[string]any)
	if !ok ||
		controls["lifecycle_management_running"] != true ||
		controls["indices_lifecycle_managed"] != true ||
		controls["security_lifecycle_policy"] != "openshell-security-production" ||
		controls["agent_lifecycle_policy"] != "openshell-agent-production" {
		t.Fatalf("lifecycle controls=%#v", evidence["controls"])
	}

	info, err := os.Stat(evidencePath)
	if err != nil {
		t.Fatal(err)
	}
	if info.Mode().Perm() != 0o600 {
		t.Fatalf("evidence permissions=%#o, want 0600", info.Mode().Perm())
	}
	samePolicy := exec.Command("./verify.sh")
	samePolicy.Env = append([]string{}, command.Env...)
	for index, value := range samePolicy.Env {
		if strings.HasPrefix(value, "OPENSHELL_AGENT_LIFECYCLE_POLICY=") {
			samePolicy.Env[index] = "OPENSHELL_AGENT_LIFECYCLE_POLICY=openshell-security-production"
		}
	}
	samePolicyOutput, samePolicyErr := samePolicy.CombinedOutput()
	if samePolicyErr == nil ||
		!strings.Contains(string(samePolicyOutput), "require distinct lifecycle policies") {
		t.Fatalf("same lifecycle policy did not fail closed: %v\n%s", samePolicyErr, samePolicyOutput)
	}

	second := exec.Command("./verify.sh")
	second.Env = command.Env
	secondOutput, secondErr := second.CombinedOutput()
	if secondErr == nil ||
		!strings.Contains(string(secondOutput), "already exists; evidence is append-only") {
		t.Fatalf("second conformance run did not fail closed: %v\n%s", secondErr, secondOutput)
	}

	drift := exec.Command("./verify.sh")
	drift.Env = append([]string{}, command.Env...)
	driftPath := filepath.Join(tempDir, "timestamp-drift-conformance.json")
	for index, value := range drift.Env {
		if strings.HasPrefix(value, "OPENSHELL_EVIDENCE_OUTPUT=") {
			drift.Env[index] = "OPENSHELL_EVIDENCE_OUTPUT=" + driftPath
		}
	}
	drift.Env = append(drift.Env, "FAKE_TIMESTAMP_DRIFT_RULE=openshell-policy-denial-v1")
	driftOutput, driftErr := drift.CombinedOutput()
	if driftErr == nil || !strings.Contains(string(driftOutput), "detection rule drift") {
		t.Fatalf("timestamp drift did not fail conformance: %v\n%s", driftErr, driftOutput)
	}
	if _, err := os.Stat(driftPath); !os.IsNotExist(err) {
		t.Fatalf("failed conformance unexpectedly published evidence: %v", err)
	}
}

func TestProductionActionBinderSafetyContract(t *testing.T) {
	t.Parallel()

	body, err := os.ReadFile("bind-actions.sh")
	if err != nil {
		t.Fatal(err)
	}
	script := string(body)
	for _, required := range []string{
		"plan|apply|verify",
		"existing customer-managed .webhook connector",
		"OPENSHELL_SOC_WEBHOOK_CONNECTOR_ID",
		"KIBANA_DETECTION_AUTH_HEADER_FILE",
		"OPENSHELL_CHANGE_TICKET has an invalid identifier",
		"reviewed Elastic asset checksum mismatch",
		"connector_type_id == \".webhook\"",
		"action_type_id:\".webhook\"",
		"is_missing_secrets",
		"--request PATCH",
		"onActiveAlert",
		"refusing replacement",
		"connector secrets remained customer-managed",
	} {
		if !strings.Contains(script, required) {
			t.Errorf("production action binder is missing %q", required)
		}
	}
	for _, forbidden := range []string{
		"--request POST",
		"--request PUT",
		"--request DELETE",
		"--user ",
		"ELASTIC_PASSWORD",
		"http://",
		"/policy",
		"openshell gateway",
	} {
		if strings.Contains(script, forbidden) {
			t.Errorf("production action binder contains forbidden behavior %q", forbidden)
		}
	}

	template, err := os.ReadFile("assets/soc-webhook-body.json")
	if err != nil {
		t.Fatal(err)
	}
	var notification map[string]any
	if err := json.Unmarshal(template, &notification); err != nil {
		t.Fatal(err)
	}
	encoded := string(template)
	for _, required := range []string{
		`"schema_version": "1.0"`,
		`"producer": "elastic-security"`,
		"{{context.rule.rule_id}}",
		"{{context.rule.severity}}",
		"{{alert.id}}",
		"{{{context.results_link}}}",
	} {
		if !strings.Contains(encoded, required) {
			t.Errorf("SOC webhook template is missing %q", required)
		}
	}
	for _, forbidden := range []string{"event.original", "prompt", "response", "tool_arguments", "credentials", "authorization"} {
		if strings.Contains(strings.ToLower(encoded), forbidden) {
			t.Errorf("SOC webhook template contains privacy-sensitive field %q", forbidden)
		}
	}
}
