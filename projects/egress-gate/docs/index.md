---
title: Egress Gate
description: Overview and quickstart for request-level OpenShell middleware.
agent_markdown: true
---

# Egress Gate

Egress Gate is an OpenShell supervisor middleware for the pre-credentials HTTP
request phase. OpenShell owns interception, routing, and credential
attachment; Egress Gate evaluates one immutable byte-oriented `HttpRequest`
and returns an explicit domain result.

It is not a forward proxy, TLS interceptor, response filter, or protection for
content already persisted by a harness. Configure storage and retention
controls separately.

## Runtime shape

```text
OpenShell protobuf/gRPC
        -> service adapter
        -> HttpRequest + strict pipeline config
        -> RequestProcessor + shared Timeout
        -> GateEvaluation sequence
        -> EgressResult
        -> OpenShell protobuf/gRPC
```

Only `service/` imports generated bindings. Gate and processor code is
protobuf-free and can be evaluated offline.

## Quickstart

From `projects/egress-gate/`:

```bash
uv sync --frozen
uv run egress-gate gates
uv run egress-gate configuration-schema
uv run egress-gate validate \
  --policy examples/deterministic-gate/egress-gate-config.yaml
uv run egress-gate serve --listen 127.0.0.1:50051
```

Use the [regex-body guide](gates/regex.md) for an OpenShell policy and a
file-backed catalog.

Use [offline evaluation](evaluation.md) to run bounded request corpora through
the same prepared `RequestProcessor` used by the service, without starting
gRPC or contacting an upstream provider.

## Core rules

- A policy has one through ten named gates and a required `default_decision`.
- Each gate sees the current request after preceding proceeding patches.
- `proceed` applies a patch; terminal `allow` and `deny` require empty patches.
- `None` body replacement means no replacement; `b""` is an explicit empty
  replacement.
- Runtime safety limits deny with source `runtime_limit` and
  `egress_gate_limit_exceeded`.
- Pipeline default deny uses source `pipeline_default` and
  `egress_gate_default_deny`.
- The released Finding wire contract has only five fields. Gate source and
  decision provenance remain runtime-internal.

## Further reading

- [Configuration](configuration.md)
- [Offline evaluation](evaluation.md)
- [Operations](operations.md)
- [Gate authoring](gates/custom.md)
- [Regex-body](gates/regex.md)
- [Architecture](architecture/index.md)
- [Request lifecycle](architecture/request-lifecycle.md)
- [Service boundary](architecture/service-boundary.md)
- [Limits and failures](reference/limits-and-failures.md)
