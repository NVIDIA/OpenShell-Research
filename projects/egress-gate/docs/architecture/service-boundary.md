---
title: Service boundary
description: Egress Gate gRPC methods, protobuf translation, active policy management, and worker scheduling.
agent_markdown: true
---

# Service boundary

The `service/` package adapts OpenShell's `SupervisorMiddleware` gRPC contract
to Egress Gate's protobuf-free domain model. No other handwritten package
imports gRPC or generated bindings.

The checked-in protocol and generated bindings are canonical OpenShell copies.

## gRPC methods

| RPC | Behavior |
| --- | --- |
| `Describe` | Advertise service identity, the pre-credentials HTTP binding, and the 4 MiB body limit |
| `ValidateConfig` | Validate policy configuration against the active engine registry and resources |
| `EvaluateHttpRequest` | Validate transport input, resolve the configured processor, process text, and return a decision |

`Describe` advertises only
`SUPERVISOR_MIDDLEWARE_PHASE_PRE_CREDENTIALS`.

Engine discovery and the finalized policy schema are available through:

```bash
egress-gate engines
egress-gate configuration-schema
```

## Configuration transport

OpenShell supplies the complete policy config as `google.protobuf.Struct` for
validation and evaluation. The service:

1. rejects an encoded config larger than 64 KiB
2. converts the `Struct` to a mapping
3. normalizes finite integral doubles in the safe integer range
4. validates the registry-built policy model
5. validates engine config against registered resources
6. checks replacement support for `replace`

`Struct` represents every number as a double. The service converts finite
integral values only within `-(2^53 - 1)` through `2^53 - 1` before strict
model validation. Other values remain floats.

## Active policy

The service retains one pair:

```text
validated EgressGateConfig + configured RequestProcessor
```

Equal validated configuration reuses this pair. A changed configuration enters
a serialized preparation path:

1. construct each configured engine from its exact config and registered
   resources
2. construct the candidate processor
3. activate the complete candidate atomically

Failed preparation leaves the active pair unchanged. An evaluation that already
captured the previous processor can finish with it while later evaluations use
the replacement.

## Request adaptation

For each evaluation, the service:

1. validates the phase
2. validates context, config, target, headers, and body limits
3. acquires a processing slot
4. validates config and resolves the processor in a worker
5. allows an empty body without engine invocation
6. decodes a non-empty body as strict UTF-8
7. runs `RequestProcessor.process(text)` in the same worker

Request metadata stays in the service layer. The request ID is used only for
content-safe logging.

## Result adaptation

The service maps `RequestProcessingResult` to `HttpRequestResult` and validates
the encoded replacement and finding representation before returning.

### Findings

Each aggregated detection summary becomes one OpenShell finding:

| Finding field | Value |
| --- | --- |
| `type` | `detected_entity` |
| `label` | `entity (source-stage)` |
| `confidence` | `low`, `medium`, `high`, or empty |
| `count` | Number of aggregated occurrences |

The protobuf has no fields for matched content, context, offsets, patterns, or
raw engine metadata. Engine-provided entity identifiers and configured stage
names do cross in `label`, so custom engines must keep identifiers independent
of request text.

If the complete result cannot fit OpenShell's finding bounds, the service
returns `egress_gate_limit_exceeded` without partial findings.

## Worker scheduling

The async gRPC server accepts at most 16 concurrent RPCs. Synchronous config
validation, processor preparation, UTF-8 decoding, and engine processing run in
a dedicated four-thread executor guarded by a four-slot semaphore.

This prevents synchronous engine work from blocking the gRPC event loop and
bounds active processing.

## Cancellation

Cancelling the async RPC does not stop Python code already running in a worker
thread. The service keeps the worker bridge alive and releases the semaphore
slot only when that worker exits.

Engines should pass the shared remaining timeout to interruptible collaborators.
A non-interruptible call continues to occupy its worker slot.

Server shutdown stops gRPC, waits for active executor work, and closes the
middleware executor.

## Error mapping

| Egress Gate error kind | gRPC status |
| --- | --- |
| Invalid input | `INVALID_ARGUMENT` |
| Internal failure | `INTERNAL` |

`ValidateConfig` returns `valid=false` with a content-safe reason instead of a
gRPC error for invalid policy configuration.

Unexpected failures map to the stable `unexpected_service_failure` error.
Collaborator exception messages and chains are not returned.

A gRPC failure is different from a successful policy deny:

- OpenShell applies middleware `on_error` after a gRPC failure.
- OpenShell follows the explicit deny after `egress_gate_blocked` or
  `egress_gate_limit_exceeded`.

## Server API

`EgressGateServer` owns the finalized registry, middleware adapter, gRPC
server, and shutdown lifecycle:

```python
from egress_gate.engines.registry import create_builtin_registry
from egress_gate.service import EgressGateServer

server = EgressGateServer(
    create_builtin_registry(),
    timeout_seconds=5,
)
server.serve_sync("127.0.0.1:50051")
```

Async applications use `await server.serve_async(address)`.

The server rejects an unfinalized registry, invalid listen address, or port
outside 1 through 65535. Bind and startup failures use the stable
`server_bind_failed` error.

See [Run and operate Egress Gate](../operations.md) for CLI startup and
gateway registration.

## Related pages

- [System architecture](index.md)
- [Request lifecycle](request-lifecycle.md)
- [Add a custom engine](../engines/custom.md)
- [Limits and failure behavior](../reference/limits-and-failures.md)
