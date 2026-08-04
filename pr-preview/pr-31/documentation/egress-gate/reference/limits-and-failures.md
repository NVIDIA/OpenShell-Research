---
title: Limits and failure behavior
description: Bounded Egress Gate domain and service behavior.
agent_markdown: true
---

# Limits and failure behavior

Limits are fail-closed and content-safe. Exact encoded protobuf checks belong
to `service/`; scalar, aggregate, and result-model checks belong to the domain.

| Area | Limit |
| --- | ---: |
| Request body | 4 MiB |
| Pipeline gates | 10 |
| Finding groups per gate/result | 32 |
| Estimated finding wire size | 4 KiB |
| Result metadata entries | 64 |
| Result metadata aggregate strings | 32 KiB |
| Gate traces per result | 10 |
| Header mutations per patch | 64 |
| Processing timeout | 30 seconds maximum |
| Concurrent processing slots | 4 |

Request context and target aggregates, headers, replacement bodies, regex
catalogs, compiled cache weight, and diagnostic strings have additional
bounded limits in `constants.py`. Tests cover exact accepted boundaries and
the first rejected value.

## Outcomes

| Condition | Outcome |
| --- | --- |
| Invalid phase, envelope, policy, or input encoding | gRPC `INVALID_ARGUMENT` |
| Gate contract or unexpected execution failure | gRPC `INTERNAL` |
| Deadline or runtime limit | deny, source `runtime_limit`, code `egress_gate_limit_exceeded` |
| Gate terminal deny | deny, source `gate`, gate-owned reason code |
| Pipeline default deny | deny, source `pipeline_default`, code `egress_gate_default_deny` |
| Pipeline default allow | allow, source `pipeline_default`, no reason code |

Runtime-limit results contain no partial patch, findings, or trace details.
Failed policy preparation leaves the active policy unchanged. Stable error
catalogs and reason codes never include request content or arbitrary exception
text.

## Finding contract

The released OpenShell wire contract has five fields. The runtime's
`SourcedFinding` and `DecisionSource` preserve provenance for internal tests,
traces, and logging only. Do not encode source or attributes into labels or
metadata while the canonical protocol remains five-field.
