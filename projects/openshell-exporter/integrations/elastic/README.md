# Elastic SOC integration pack

This optional pack maps the exporter contracts into Elastic without adding Elastic to the exporter itself.

~~~text
Exporter ──HTTPS/mTLS──> Logstash -> Elasticsearch security index -> Kibana
Exporter ──OTLP/HTTPS──> Elastic APM intake -> Elasticsearch trace index -> Kibana
Exporter metrics -> Prometheus -> Grafana
~~~

The checked [manifest](manifest.json) is authoritative for pack version, Elastic versions, index patterns, asset counts, and role counts. The current pack targets Elasticsearch and Kibana `9.4.3`, CloudEvents envelope `1.0`, and OCSF `1.8.0`.

## What the pack preserves

- deterministic CloudEvent document identity from `source` and `id`;
- complete recursively redacted CloudEvent and source objects;
- gateway, workspace, sandbox, policy, and provenance fields;
- OCSF, validation, and redaction metadata;
- source, observation, and ingestion times;
- native Relay trace topology and privacy-approved attributes;
- unknown future fields.

## Security roles

Use separate identities for:

1. temporary Elasticsearch bootstrap;
2. temporary Kibana bootstrap;
3. detection-rule installation and execution;
4. Logstash runtime writes;
5. APM Server runtime writes.

Map people to environment-scoped viewer, analyst, or detection-engineer roles. The runtime writers must not administer the cluster or write each other’s indices.

Authentication headers are read from mode-0600 files. Do not put API keys in environment variables or command arguments.

## Plan

Planning validates reviewed asset checksums and prints the exact changes without network calls:

~~~sh
export OPENSHELL_ELASTIC_ENVIRONMENT=production
export OPENSHELL_KIBANA_SPACE=openshell-production
./integrations/elastic/install.sh plan
~~~

## Install platform assets

Set HTTPS endpoints, an explicit CA, separate bootstrap header files, and a change ticket:

~~~sh
export ELASTICSEARCH_URL=https://elastic.example:9243
export KIBANA_URL=https://kibana.example
export ELASTIC_CA_FILE=/secure/elastic-ca.pem
export ELASTICSEARCH_BOOTSTRAP_AUTH_HEADER_FILE=/secure/es-bootstrap.header
export KIBANA_BOOTSTRAP_AUTH_HEADER_FILE=/secure/kibana-bootstrap.header
export OPENSHELL_CHANGE_TICKET=SEC-1234
./integrations/elastic/install.sh platform
~~~

Then install detection rules with a separate identity:

~~~sh
export KIBANA_DETECTION_AUTH_HEADER_FILE=/secure/kibana-detection.header
./integrations/elastic/install.sh rules
~~~

Each header file contains exactly one line:

~~~text
Authorization: ApiKey BASE64_VALUE
~~~

The operator must create and attach separate lifecycle policies for security evidence and agent traces. The installer intentionally has no lifecycle-management privilege.

## Verify an external installation

The read-only verifier requires independent read and ingest identities, a snapshot repository, retention approval, distinct lifecycle policies, and the expected detection principal:

~~~sh
export ELASTICSEARCH_READ_AUTH_HEADER_FILE=/secure/es-read.header
export KIBANA_READ_AUTH_HEADER_FILE=/secure/kibana-read.header
export ELASTICSEARCH_INGEST_AUTH_HEADER_FILE=/secure/es-ingest.header
export OPENSHELL_SNAPSHOT_REPOSITORY=security-evidence
export OPENSHELL_RETENTION_APPROVAL=PRIVACY-456
export OPENSHELL_SECURITY_LIFECYCLE_POLICY=openshell-security-production
export OPENSHELL_AGENT_LIFECYCLE_POLICY=openshell-agent-production
export OPENSHELL_DETECTION_PRINCIPAL=openshell-detection-production
./integrations/elastic/verify.sh
~~~

Set `OPENSHELL_EVIDENCE_OUTPUT` to a new protected path to write a non-overwritable, credential-free conformance report.

## Dashboards and operations

Kibana dashboards cover analyst triage, command-center overview, activity and enforcement timelines, evidence trust, alert-to-sandbox investigation, and policy-engine evidence. Direct-ID joins and temporal context are labeled separately.

Grafana displays only exporter operational metrics: source activity, queues, validation, reconnects, gaps, storage, and destination delivery. It does not receive evidence records.

## What Logstash does not receive

NeMo Relay OTLP traces use APM intake. Exporter Prometheus metrics use
Prometheus/Grafana. Evidence missed by non-resumable `WatchSandbox` cannot be
recovered here; resumable `WatchEvents` is unavailable in the pinned SDK.

## Limitations

The local Elastic demo is not an external destination, retention, scale, privacy, or production-readiness gate. Run the [external destination conformance harness](../../conformance/README.md) against the independently operated environment.
