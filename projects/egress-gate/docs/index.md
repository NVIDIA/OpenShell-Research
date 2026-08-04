---
title: Egress Gate
description: Overview and quickstart for request-level OpenShell middleware.
agent_markdown: true
---

# Egress Gate

Egress Gate is an extensible OpenShell supervisor middleware service for the
pre-credentials HTTP request phase. OpenShell owns interception, routing, and
credential attachment. Egress Gate evaluates one immutable byte-oriented
`HttpRequest` and returns an explicit result.

An application can install trusted custom gates. Each gate has a strict
configuration type and an explicit control result.

It is not a forward proxy, TLS interceptor, or response filter. It does not
protect content that a harness already wrote to disk. Configure storage and
retention controls separately.

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
  --policy examples/regex-redaction/egress-gate-config.yaml
uv run egress-gate serve --listen 127.0.0.1:50051
```

Use the [regex-body guide](gates/regex.md) for an OpenShell policy and a
file-backed catalog.

Use [offline evaluation](evaluation.md) to run bounded request corpora through
the same prepared `RequestProcessor` used by the service, without starting
gRPC or contacting an upstream provider.

## Core rules

- A policy has one through ten named gates and a required `default_decision`.
- Each gate sees the request after Egress Gate applies patches from earlier
  gates.
- `proceed` applies a patch. Terminal `allow` and `deny` require empty patches.
- `None` body replacement means no replacement. `b""` is an explicit empty
  replacement.
- When a runtime safety limit occurs, Egress Gate denies the request. The result
  uses source `runtime_limit` and code `egress_gate_limit_exceeded`.
- Pipeline default deny uses source `pipeline_default` and code
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
