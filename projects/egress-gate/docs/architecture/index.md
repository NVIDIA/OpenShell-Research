---
title: System architecture
description: Egress Gate components, boundaries, state, and concurrency.
agent_markdown: true
---

# System architecture

Egress Gate has one transport adapter and one protobuf-free runtime:

```text
OpenShell SupervisorMiddleware
        -> service/servicer.py
        -> HttpRequest + EgressGateConfig
        -> RequestProcessor
        -> GateRegistry-prepared Gate instances
        -> EgressResult
        -> service/servicer.py
```

## Component ownership

| Module | Responsibility |
| --- | --- |
| `request.py` | Immutable request, headers, and ordered `RequestPatch` |
| `result.py` | Gate evaluations, five-field findings, provenance, traces, and result invariants |
| `gates/base.py` | Gate lifecycle, capabilities, output validation, and UTF-8 helper |
| `gates/registry.py` | Trusted registration, exact pipeline schema, resources, and discovery |
| `gates/regex_body.py` | Bounded catalog loading, matching, overlap handling, caching, and replacement |
| `gates/request_rules.py` | Normalized request-fact matching, bounded path globs, deny precedence, and terminal rule decisions |
| `config.py` | Strict `pipeline.gates` and required default decision |
| `request_processor.py` | Shared deadline, current-request mutation, control flow, aggregation, and provenance |
| `service/` | Protobuf validation/conversion, worker slots, lifecycle, and wire serialization |

Only `service/` imports generated protobuf/gRPC bindings. The processor and
gates receive domain values and can be tested offline.

## Trust and state

Registry factories and custom gate modules are trusted deployment code.
Capabilities mechanically constrain outputs but do not sandbox Python reads.
Prepared gates may borrow application-owned concurrency-safe resources; Egress
Gate does not close them.

One validated policy and one prepared `RequestProcessor` are active at a time.
Preparation is serialized and a complete candidate is published only after
the shared deadline checks. A failed candidate leaves the existing policy
unchanged. Gate instances are reused across worker threads, so per-request
state must remain local to `evaluate`.

See [Request lifecycle](request-lifecycle.md) and [Service boundary](service-boundary.md).
