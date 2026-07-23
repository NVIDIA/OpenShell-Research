---
title: Service boundary
description: gRPC adaptation, worker scheduling, result aggregation, and server lifecycle.
agent_markdown: true
---

# Service boundary

Outside the generated `bindings/` package, `service/` is the only layer that
imports gRPC or generated protobuf bindings. It translates between OpenShell's
transport contract and Privacy Guard's domain types.

## gRPC methods

Privacy Guard implements three `SupervisorMiddleware` methods:

| RPC | Behavior |
| --- | --- |
| `Describe` | Advertises the service name, version, pre-credentials HTTP binding, and body limit |
| `ValidateConfig` | Parses policy and asks the processor to validate referenced formats and entities |
| `EvaluateHttpRequest` | Validates transport input, runs the processor, and returns a decision |

`Describe` advertises only
`SUPERVISOR_MIDDLEWARE_PHASE_PRE_CREDENTIALS`. The service rejects evaluations
for any other phase.

## Incoming requests

For each evaluation, the servicer:

1. validates the phase and body size
2. converts the protobuf `Struct` into a strict `PolicyConfig`
3. extracts the request ID, content type, and body
4. constructs an `InterceptedRequest`
5. passes that domain record to `RequestProcessor`

Transport-only fields that processing does not need remain at the service
boundary.

`content_type` is retained as request context, but the processor selects the
format from policy. It does not infer format from this header.

## Outgoing results

The processor returns `ProcessingResult`. The servicer converts it to
`HttpRequestResult`:

| Domain result | Protobuf result |
| --- | --- |
| Allow without replacement | `DECISION_ALLOW`, `has_body=false` |
| Allow with replacement | `DECISION_ALLOW`, `has_body=true`, replacement bytes |
| Deny | `DECISION_DENY`, no body, stable reason code |

The service checks replacement size again before serialization.

### Finding aggregation

Domain findings include text-block paths and exact offsets. The protobuf result
contains count-based audit findings.

The service groups findings by:

- scanner name
- entity
- optional `pattern_name` metadata
- confidence

It emits:

- `type`: scanner name
- `label`: entity or `entity/pattern-name`
- `confidence`
- `count`

Paths, offsets, and matched request content never cross the protobuf boundary.

## Concurrency model

The gRPC server allows up to 16 concurrent RPCs. Synchronous processing runs in
a dedicated pool with four workers.

```text
gRPC event loop
      |
      | acquire scan slot
      v
4-slot semaphore
      |
      v
4-thread executor
      |
      v
RequestProcessor.process
```

This prevents scanner work from blocking the async event loop and bounds active
synchronous scans.

Scanner and format-handler instances are shared by those workers. They must be
safe for concurrent use.

## Cancellation

Cancelling an async RPC cannot stop Python code already running in a worker
thread. The service shields the worker future and keeps its semaphore slot until
the worker actually exits.

This rule prevents cancellation from creating more than four active scans.

During shutdown, the service stops gRPC and waits for the scan executor.

## Error mapping

`PrivacyGuardError` classifies each cataloged failure as invalid input or
internal failure.

| Error kind | gRPC status |
| --- | --- |
| `invalid_input` | `INVALID_ARGUMENT` |
| `internal` | `INTERNAL` |

This mapping applies to `EvaluateHttpRequest`. `ValidateConfig` reports failures
in `ValidateConfigResponse` with `valid=false` and a safe reason.

Unexpected exceptions become the content-safe
`unexpected_service_failure` error. The service does not return arbitrary
exception text.

A gRPC error is different from a policy deny. OpenShell applies its configured
middleware failure mode when the RPC fails. A policy deny is a successful RPC
whose result explicitly stops the request.

## Server lifecycle

`MiddlewareServer` is the high-level API for custom scanners. It constructs:

```text
Scanner
  -> RequestProcessor
  -> PrivacyGuardMiddleware
  -> gRPC server
```

The CLI uses the same API for built-in scanners.

The server:

1. creates an unstarted async gRPC server
2. binds the configured address
3. starts and waits for termination
4. stops gRPC
5. closes the middleware executor

A bind failure becomes the stable `server_bind_failed` error.

## Testing the boundary

Service tests should cover:

- protobuf/domain translation
- manifest contents
- policy validation RPCs
- phase and body-size validation
- allow, replacement, and deny serialization
- finding aggregation and encoded limits
- gRPC status mapping
- worker-slot behavior under cancellation
- startup, bind failure, and shutdown

Keep scanner detection and processor policy cases out of service tests unless
the case requires transport translation.

[Back to the architecture overview](index.md)
