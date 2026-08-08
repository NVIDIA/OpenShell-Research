---
title: Limits and failure behavior
description: Bounded Egress Gate domain and service behavior.
agent_markdown: true
---

# Limits and failure behavior

Egress Gate-owned limits are fail-closed and content-safe. The `service/`
package checks exact encoded protobuf sizes. Domain models check scalar,
aggregate, and result limits. OpenShell owns the separate outer RPC ceiling and
applies its configured `on_error` behavior when that ceiling expires first.

| Area | Limit |
| --- | ---: |
| Request body | 4 MiB |
| JSON nesting depth | 128 |
| JSON value nodes | 100,000 |
| JSON field selectors per scan | 32 |
| Message content selectors per mapping | 32, plus the required messages selector |
| JSON path segments per selector | 32 |
| Selected JSON nodes or normalized message blocks | 4,096 |
| Pipeline gates | 10 |
| Finding groups per gate/result | 32 |
| Estimated finding wire size | 4 KiB |
| Result metadata entries | 64 |
| Result metadata aggregate strings | 32 KiB |
| Gate traces per result | 10 |
| Header mutations per gate evaluation | 64 |
| Offline `--timeout` | 10 milliseconds minimum; whole milliseconds |
| `timeout_middleware_processing` | 10 milliseconds minimum; whole milliseconds |
| Gateway registration timeout | Operator-configurable; helper default 30 seconds |
| Concurrent processing slots | 4 |

Request context and target aggregates, headers, replacement bodies, regex
catalogs, individual patterns, and diagnostic strings have additional bounded
limits in `constants.py`. Tests cover exact accepted boundaries and the first
rejected value.

## Outcomes

| Condition | Outcome |
| --- | --- |
| Invalid phase, envelope, policy, input encoding, or configured JSON format | gRPC `INVALID_ARGUMENT` |
| Gate contract or unexpected execution failure | gRPC `INTERNAL` |
| Internal processing deadline or pipeline processor limit | deny, source `runtime_limit`, code `egress_gate_limit_exceeded` |
| Gate terminal deny | deny, source `gate`, gate-owned reason code |
| Pipeline default deny | deny, source `pipeline_default`, code `egress_gate_default_deny` |
| Pipeline default allow | allow, source `pipeline_default`, no reason code |

Pipeline processor limit results contain no partial mutations, findings, or
trace details. Failed policy preparation leaves the active policy unchanged.
Stable error catalogs and reason codes never include request content or
arbitrary exception text.

An internal processing timeout returns the runtime-limit denial only while the
RPC remains active. If the gateway's independent outer RPC ceiling expires
first, OpenShell applies the middleware entry's `on_error` policy.

## Finding contract

The released OpenShell wire contract has five fields. The pipeline processor's
`SourcedFinding` and `DecisionSource` preserve provenance for internal tests,
traces, and logging only. Do not encode source or attributes into labels or
metadata while the canonical protocol remains five-field.
