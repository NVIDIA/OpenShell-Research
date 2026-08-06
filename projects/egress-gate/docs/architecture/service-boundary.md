---
title: Service boundary
description: Protobuf conversion, validation, worker scheduling, and lifecycle.
agent_markdown: true
---

# Service boundary

The `service/` package is the only handwritten package that imports OpenShell
protobuf/gRPC bindings. It owns exact encoded wire limits and transport status
mapping. Domain models own protobuf-free invariants.

The OpenShell supervisor owns the intercepted request. Egress Gate receives its
request data over gRPC and works with local immutable `HttpRequest` snapshots.
The Egress Gate service adapter returns a decision and final mutations; the
supervisor applies allowed mutations to the intercepted request.

## RPCs

| RPC | Behavior |
| --- | --- |
| `Describe` | Return Egress Gate's pre-credentials HTTP binding |
| `ValidateConfig` | Validate a complete registry-backed pipeline without publishing it |
| `EvaluateHttpRequest` | Adapt one request, prepare/reuse policy, execute, and serialize |

The configuration arrives as `google.protobuf.Struct`. The adapter normalizes
safe integral doubles before strict domain validation and rejects oversized
encoded configuration before registry parsing.

## Shared deadline and workers

The service's `timeout_middleware_processing` value is returned in the
`MiddlewareBinding` from `Describe`. `EvaluateHttpRequest` converts that same
value into one monotonic `Timeout` used for semaphore acquisition, policy
preparation, replacement-lock waits, gate execution, and final result checks.
The gateway's separate `timeout_gateway_ceiling` can shorten, but never extend,
that processing time.
`RequestProcessor.process` accepts the caller-owned timeout and never creates or
stores one.

Synchronous work runs in a bounded four-slot executor. The gRPC server permits
sixteen concurrent RPCs. Cancellation does not stop Python code that already
runs in a worker. The worker owns its slot until it exits.

## Wire findings and mutations

The current OpenShell `Finding` contains exactly `type`, `label`, `count`,
`confidence`, and `severity`. `SourcedFinding.source_gate`, decision sources,
and traces belong to the pipeline processor and are not serialized. Decision
sources use a strict `kind`-discriminated union. The adapter rechecks protobuf
finding and header sizes before returning a response.

`RequestMutations` is Egress Gate's internal aggregate. A gate returns it with
`proceed` instead of modifying its input. The pipeline processor validates and
applies it to a new local `HttpRequest` snapshot for the next gate.

At the service boundary, the adapter maps the accumulated
`RequestMutations.replacement_body` to `HttpRequestResult.body` and `has_body`.
It maps each ordered header operation to
`HttpRequestResult.header_mutations`. `None` means no body replacement, while
empty bytes are emitted with `has_body=true`. The OpenShell supervisor applies
these wire mutations after an allow.

## Lifecycle and errors

The active policy contains one validated configuration and one prepared
pipeline processor. An equal configuration reuses the active pipeline
processor. The service prepares a changed candidate before it publishes that
candidate. An invalid candidate does not replace the active policy.

Invalid input maps to `INVALID_ARGUMENT`. Internal gate or service failures map
to `INTERNAL`. A pipeline processor limit denial is not a gRPC failure. It uses
`egress_gate_limit_exceeded`.
