---
title: "Event and receiver contract"
description: "OpenShell Event Exporter — event and receiver contract."
agent_markdown: true
---

# Event and receiver contract

OpenShell evidence is CloudEvents 1.0; traces remain native OTLP. The exact
envelope is [JSON Schema v1](https://github.com/NVIDIA/OpenShell-Research/blob/main/projects/openshell-exporter/api/schemas/event-envelope-v1.schema.json), with
[checked examples](https://github.com/NVIDIA/OpenShell-Research/tree/main/projects/openshell-exporter/api/examples).

## Identity and payload

`source` identifies the acquisition lane; `id` is calculated from original
evidence before redaction. File identities include source coordinates and a
body hash; sandbox ID or OCSF `metadata.uid` alone is insufficient.

`dataschema` is `urn:openshell:event-envelope:1`. `data` carries acquisition
provenance, OpenShell identity, validation/redaction status, correlation IDs,
observation time, and the complete recursively redacted `original`.
`time` is source time and is omitted when unavailable. Preserve unknown fields.
Additive v1 fields are compatible; changing existing meaning needs a new version.

Malformed evidence is marked and retained. Redaction is not DLP. Shared direct
identifiers support joins; same-sandbox temporal proximity is not causal proof.

## Event types

All use the `com.nvidia.openshell.` prefix:

- `ocsf.CLASS_UID.v1` (`ocsf.unknown.v1` when class identity is unavailable)
- `sandbox.lifecycle.v1`, `gateway.log.v1`, `sandbox.log.v1`, `sandbox.file_log.v1`
- `platform.event.v1`, `stream.warning.v1`, `source.capability.v1`
- `kubernetes.context.v1`, `nemo_relay.log.v1`

Policy evidence types:

```text
com.nvidia.openshell.policy.draft_updated.v1
com.nvidia.openshell.policy.draft.snapshot.v1
com.nvidia.openshell.policy.draft.chunk.v1
com.nvidia.openshell.policy.draft.history.v1
com.nvidia.openshell.policy.status.v1
com.nvidia.openshell.policy.revision.v1
com.nvidia.openshell.policy.reconciliation.warning.v1
```

## HTTP delivery

Accept `POST /v1/events`, `Content-Type: application/cloudevents-batch+json`, with
a JSON array. Default limits: 500 events, 1 MiB per event, 4 MiB per request.
Oversized events are excluded from HTTP delivery and retained in redacted recovery.

| Result | Behavior |
|---|---|
| 2xx | Entire batch acknowledged |
| Network error, timeout, 408, 425, 429, 5xx | Retry |
| Other 3xx/4xx, including 413 | Permanent rejection; redirects are not followed |

Authenticate with verified TLS and destination-specific credentials. Validate
and persist the entire batch before acknowledging; partial acknowledgements are
unsupported. Deduplicate by `(source,id)` across restarts. Preserve source,
observation, and ingestion times separately. Keep event bodies out of logs.
