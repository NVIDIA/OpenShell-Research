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

## Request mutation flow

1. The OpenShell supervisor sends the intercepted request to Egress Gate.
2. Each gate reads the current immutable `HttpRequest` snapshot and can propose
   `RequestMutations`. A gate does not modify its input in place.
3. The Egress Gate pipeline processor validates and applies those mutations to
   a new local `HttpRequest` snapshot.
4. The next gate sees the updated snapshot.
5. If the pipeline allows the request, the service adapter maps the accumulated
   mutations to OpenShell's `HttpRequestResult`.
6. The OpenShell supervisor applies the returned mutations to the intercepted
   request before it attaches credentials.

A denial returns no request mutations.

## Request path

<figure class="documentation-figure documentation-figure--portrait">
  <img src="assets/diagrams/request-path.svg" alt="The OpenShell supervisor sends an intercepted request to Egress Gate, receives the final decision and mutations, and applies allowed mutations before it attaches credentials.">
  <figcaption>Egress Gate builds local request snapshots. The OpenShell supervisor owns and updates the intercepted request.</figcaption>
</figure>

Only `service/` imports generated bindings. Gate and pipeline processor code is
protobuf-free and can be evaluated offline.

## Quickstart

From `projects/egress-gate/`:

`uv run` prepares the project environment before each command.

```bash title="Inspect, validate, and serve"
uv run egress-gate gates list
uv run egress-gate gates schema
uv run egress-gate validate \
  --policy examples/regex-redaction/egress-gate-config.yaml
uv run egress-gate serve --listen 127.0.0.1:50051
```

Use the [regex guide](gates/regex.md) for an OpenShell policy and a
file-backed catalog.

Use [offline policy tests](evaluation.md) to check saved request examples with
the same prepared `RequestProcessor` used by the service. No request goes to an
upstream provider.

## Core rules

- A policy has one through ten named gates and a required `default_decision`.
- Each gate receives the current read-only request snapshot.
- Only `proceed` can propose request mutations. Terminal `allow` and `deny`
  require an empty mutation set.
- `None` body replacement means no replacement. `b""` is an explicit empty
  replacement.
- When the pipeline processor reaches a safety limit, Egress Gate denies the
  request. The result uses source `runtime_limit` and code
  `egress_gate_limit_exceeded`.
- Pipeline default deny uses source `pipeline_default` and code
  `egress_gate_default_deny`.
- The released Finding wire contract has only five fields. Gate source and
  decision provenance remain internal to the pipeline processor.

## Further reading

- [Configuration](configuration.md)
- [Test policies offline](evaluation.md)
- [Operations](operations.md)
- [Gate authoring](gates/custom.md)
- [Regex gate](gates/regex.md)
- [Architecture](architecture/index.md)
- [Request lifecycle](architecture/request-lifecycle.md)
- [Service boundary](architecture/service-boundary.md)
- [Limits and failures](reference/limits-and-failures.md)
