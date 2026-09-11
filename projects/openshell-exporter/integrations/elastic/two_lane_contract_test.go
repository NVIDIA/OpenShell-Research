// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
// SPDX-License-Identifier: Apache-2.0

package elastic_test

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func readContractFile(t *testing.T, path ...string) string {
	t.Helper()
	body, err := os.ReadFile(filepath.Join(path...))
	if err != nil {
		t.Fatal(err)
	}
	return string(body)
}

func requireContractStrings(t *testing.T, body string, required ...string) {
	t.Helper()
	for _, want := range required {
		if !strings.Contains(body, want) {
			t.Errorf("contract is missing %q", want)
		}
	}
}

func TestElasticReferenceUsesIndependentCloudEventsAndOTLPLanes(t *testing.T) {
	t.Parallel()

	compose := readContractFile(t, "..", "..", "examples", "demo", "real-gateway", "compose.elastic.yaml")
	requireContractStrings(t, compose,
		"docker.io/library/logstash:9.4.3@sha256:",
		"docker.io/elastic/apm-server:9.4.3@sha256:",
		"127.0.0.1:18443:8443",
		"127.0.0.1:18200:8200",
		"logstash-data:/usr/share/logstash/data",
		"apm-server-data:/usr/share/apm-server/data",
		"read_only: true",
		"no-new-privileges:true",
	)

	exporter := readContractFile(t, "..", "..", "examples", "demo", "real-gateway", "exporter.elastic.yaml")
	requireContractStrings(t, exporter,
		"cloudevents/elastic_logstash:",
		"https://logstash.demo.internal:8443/v1/events",
		"cert_file: /run/secrets/elastic-logstash-client.crt",
		"key_file: /run/secrets/elastic-logstash-client.key",
		"otlphttp/elastic_otlp:",
		"https://apm-server.demo.internal:8200",
		"authenticator: bearertokenauth/elastic_apm",
		"storage: file_storage",
	)
	if got := strings.Count(exporter, "sending_queue:"); got != 2 {
		t.Fatalf("expected independent sending queues for CloudEvents and OTLP, got %d", got)
	}
	if got := strings.Count(exporter, "storage: file_storage"); got != 2 {
		t.Fatalf("expected both sending queues to be persistent, got %d storage bindings", got)
	}
}

func TestLogstashPreservesCloudEventsAndDeduplicatesBySourceID(t *testing.T) {
	t.Parallel()

	pipeline := readContractFile(t, "logstash", "openshell-cloudevents.conf")
	requireContractStrings(t, pipeline,
		"ssl_client_authentication => \"required\"",
		"additional_codecs => {}",
		"action => \"create\"",
		"index => \"logs-openshell.security-default-v2\"",
		"document_id => \"%{[@metadata][document_id]}\"",
		"silence_errors_in_log => [\"version_conflict_engine_exception\"]",
		"cloudevents-intake-diagnostics.jsonl",
		"decode_size_limit_bytes => 4194304",
	)

	transform := readContractFile(t, "logstash", "cloud_events.rb")
	requireContractStrings(t, transform,
		"EXPECTED_CONTENT_TYPE = \"application/cloudevents-batch+json\"",
		"normalized_media_type(content_type) == EXPECTED_CONTENT_TYPE",
		"MAX_EVENTS = 500",
		"MAX_EVENT_BYTES = 1024 * 1024",
		"MAX_REQUEST_BYTES = 4 * 1024 * 1024",
		"Digest::SHA256.hexdigest",
		"\\u0000",
		"\"original\" => raw",
		"data[\"original\"]",
		"first_value(original, [\"policy_chunk_id\"], [\"policy\", \"chunk_id\"], [\"unmapped\", \"chunk_id\"])",
		"event #{index}: #{error}",
		"LogStash::Timestamp.new(Time.iso8601(received))",
	)

	entrypoint := readContractFile(t, "logstash", "entrypoint.sh")
	requireContractStrings(t, entrypoint, "mkdir -p \"$recovery_dir\"", "chmod 0750 \"$recovery_dir\"")

	logstash := readContractFile(t, "logstash", "logstash.yml")
	requireContractStrings(t, logstash,
		"queue.type: persisted",
		"queue.max_bytes: 536870912",
		"dead_letter_queue.enable: true",
		"dead_letter_queue.max_bytes: 268435456",
	)
}

func TestAPMLaneUsesNativeDataStreamCustomizationAndSeparateIdentity(t *testing.T) {
	t.Parallel()

	config := readContractFile(t, "apm-server", "apm-server.yml")
	requireContractStrings(t, config,
		"host: 0.0.0.0:8200",
		"secret_token: ${ELASTIC_APM_SECRET_TOKEN}",
		"username: openshell_otlp",
		"password: ${ELASTIC_OTLP_PASSWORD}",
		"ssl.certificate_authorities",
	)
	if strings.Contains(config, "output.elasticsearch:\n  pipeline:") {
		t.Fatal("APM managed data streams must use traces-apm@custom, not a generic output pipeline")
	}

	encoded, err := os.ReadFile(filepath.Join("assets", "apm-ingest-pipeline.json"))
	if err != nil {
		t.Fatal(err)
	}
	var pipeline struct {
		Description string           `json:"description"`
		Processors  []map[string]any `json:"processors"`
	}
	if err := json.Unmarshal(encoded, &pipeline); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(pipeline.Description, "native APM fields are retained") || len(pipeline.Processors) < 2 {
		t.Fatalf("unexpected additive APM pipeline: %+v", pipeline)
	}

	role := readContractFile(t, "assets", "otlp-ingest-role.json")
	requireContractStrings(t, role,
		"traces-apm*",
		"logs-apm*",
		"metrics-apm*",
		".apm-agent-configuration",
		"auto_configure",
		"create_doc",
		"read_pipeline",
	)
	if strings.Contains(role, "logs-openshell.security") {
		t.Fatal("APM output identity must not write the CloudEvents security index")
	}

	provision := readContractFile(t, "..", "..", "examples", "demo", "real-gateway", "elastic", "provision-elasticsearch.sh")
	requireContractStrings(t, provision,
		"/_ingest/pipeline/openshell-relay-apm-normalize-v1",
		"/_ingest/pipeline/traces-apm@custom",
		"/_security/role/openshell_otlp",
	)
}

func TestElasticDocumentationStatesLaneCoverageLimits(t *testing.T) {
	t.Parallel()

	guide := readContractFile(t, "README.md")
	requireContractStrings(t, guide,
		"Exporter ──HTTPS/mTLS──> Logstash",
		"Exporter ──OTLP/HTTPS──> Elastic APM intake",
		"What Logstash does not receive",
		"NeMo Relay OTLP traces",
		"Exporter Prometheus metrics",
		"Evidence missed by non-resumable `WatchSandbox`",
		"`WatchEvents`",
		"complete recursively redacted CloudEvent",
		"unknown future fields",
		"temporal context",
	)
}
