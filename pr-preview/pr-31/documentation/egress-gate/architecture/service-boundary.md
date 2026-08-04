---
title: Service boundary
description: Protobuf conversion, validation, worker scheduling, and lifecycle.
agent_markdown: true
---

# Service boundary

The `service/` package is the only handwritten package allowed to import
OpenShell protobuf/gRPC bindings. It owns exact encoded wire limits and
transport status mapping; domain models own protobuf-free invariants.

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

Synchronous work runs in a bounded four-slot executor while the gRPC server
limits concurrent RPCs to sixteen. Cancellation does not stop Python code
already running in a worker; the slot remains owned until the worker exits.

## Wire findings and mutations

The current OpenShell `Finding` contains exactly `type`, `label`, `count`,
`confidence`, and `severity`. `SourcedFinding.source_gate`, decision source,
and traces are runtime values and are not serialized. The adapter rechecks
protobuf finding and header sizes before returning a response.

`RequestPatch` operations serialize in their validated order. `None` means no
replacement, while empty bytes are emitted with `has_body=true`.

## Lifecycle and errors

The active policy is one validated config plus one prepared processor. Equal
configs reuse it; changed candidates are prepared completely before atomic
publication. Invalid candidates do not replace the active pair.

Invalid input maps to `INVALID_ARGUMENT`; internal gate or service failures map
to `INTERNAL`. A successful runtime-limit deny is not a gRPC failure and uses
`egress_gate_limit_exceeded`.
