---
title: Service boundary
description: Protobuf conversion, validation, worker scheduling, and lifecycle.
agent_markdown: true
---

# Service boundary

The `service/` package is the only handwritten package that imports OpenShell
protobuf/gRPC bindings. It owns exact encoded wire limits and transport status
mapping. Domain models own protobuf-free invariants.

## RPCs

| RPC | Behavior |
| --- | --- |
| `Describe` | Advertise Egress Gate and the pre-credentials HTTP binding |
| `ValidateConfig` | Validate a complete registry-backed pipeline without publishing it |
| `EvaluateHttpRequest` | Adapt one request, prepare/reuse policy, execute, and serialize |

The configuration arrives as `google.protobuf.Struct`. The adapter normalizes
safe integral doubles before strict domain validation and rejects oversized
encoded configuration before registry parsing.

## Shared deadline and workers

`EvaluateHttpRequest` creates one monotonic `Timeout`. That same deadline is
used for semaphore acquisition, policy preparation, replacement-lock waits,
gate execution, and final result checks. `RequestProcessor.process` accepts the
caller-owned timeout and never creates or stores one.

Synchronous work runs in a bounded four-slot executor. The gRPC server permits
sixteen concurrent RPCs. Cancellation does not stop Python code that already
runs in a worker. The worker owns its slot until it exits.

## Wire findings and mutations

The current OpenShell `Finding` contains exactly `type`, `label`, `count`,
`confidence`, and `severity`. `SourcedFinding.source_gate`, decision sources,
and traces are runtime values and are not serialized. Decision sources use a
strict `kind`-discriminated union. The adapter rechecks protobuf finding and
header sizes before returning a response.

`RequestPatch` operations serialize in their validated order. `None` means no
replacement, while empty bytes are emitted with `has_body=true`.

## Lifecycle and errors

The active policy contains one validated configuration and one prepared
processor. An equal configuration reuses the active processor. The service
prepares a changed candidate before it publishes that candidate. An invalid
candidate does not replace the active policy.

Invalid input maps to `INVALID_ARGUMENT`. Internal gate or service failures map
to `INTERNAL`. A runtime-limit deny is not a gRPC failure. It uses
`egress_gate_limit_exceeded`.
