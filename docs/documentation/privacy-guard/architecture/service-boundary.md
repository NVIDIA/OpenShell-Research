---
title: Service boundary
description: gRPC adaptation, configuration caching, worker scheduling, and server lifecycle.
agent_markdown: true
---

# Service boundary

Outside the generated `bindings/` package, `service/` is the only layer that
imports gRPC or generated protobuf bindings. It translates between OpenShell's
transport contract and Privacy Guard's one-text domain contract.

The `.proto` file and generated bindings checked into this project are copied
from OpenShell. Protocol changes must land upstream and be adopted from a new
canonical copy; Privacy Guard must not create a private fork.

## gRPC methods

Privacy Guard implements the three methods currently defined by
`SupervisorMiddleware`:

| RPC | Behavior |
| --- | --- |
| `Describe` | Advertises service identity, one pre-credentials HTTP binding, and the 4 MiB body limit |
| `ValidateConfig` | Purely validates expanded policy configuration and registered resources |
| `EvaluateHttpRequest` | Validates transport input, resolves a processor, processes text, and returns a decision |

`Describe` advertises only
`SUPERVISOR_MIDDLEWARE_PHASE_PRE_CREDENTIALS`. Evaluations at any other phase
are invalid input.

The current manifest message has no field for the finalized policy schema or
engine discovery metadata. Those are available through the local
`privacy-guard schema` and `privacy-guard engines` commands.

## Configuration lifecycle

The current protocol carries a complete `google.protobuf.Struct` on both
validation and every request evaluation.

`ValidateConfig`:

1. converts the `Struct` to a mapping
2. validates the registry-built discriminated policy model
3. validates each exact engine config against registered resources
4. validates replacement support for a replace action
5. returns `valid=true`, or a content-safe reason

It does not construct engines, populate the processor cache, contact model
providers, download resources, or write artifacts.

During evaluation, the service validates the same expanded config, computes its
canonical SHA-256 fingerprint, and resolves a configured processor from a
bounded 128-entry LRU cache. A cache miss constructs engines directly from the
exact stage configs and operator-injected resources, then constructs the
`RequestProcessor`.

The cache is protected for concurrent access and is not correctness-relevant.
Eviction or process restart simply causes reconstruction from a later
evaluation's expanded config.

## Incoming requests

For each evaluation, the service:

1. validates the pre-credentials phase
2. validates the transport body byte limit
3. validates and resolves the expanded policy config
4. allows an empty body without invoking an engine
5. decodes a non-empty body as strict UTF-8
6. schedules `RequestProcessor.process(text)` in the bounded worker pool

Request context, target, headers, middleware name, and protobuf values remain at
the service boundary. The request ID is used only for content-safe operational
logging.

## Outgoing results

The processor returns `RequestProcessingResult`. The service maps it to
`HttpRequestResult`:

| Domain result | Protobuf result |
| --- | --- |
| Detect allow | `DECISION_ALLOW`, `has_body=false` |
| Block allow with no detections | `DECISION_ALLOW`, `has_body=false` |
| Replace allow | `DECISION_ALLOW`, `has_body=true`, final UTF-8 body |
| Policy block | `DECISION_DENY`, `privacy_guard_blocked`, no body |
| Limit deny | `DECISION_DENY`, `privacy_guard_limit_exceeded`, no body or partial findings |

The service checks the encoded replacement size again before serialization.

### Findings

The processor has already aggregated occurrences by source stage, entity, and
confidence. For each `EntityDetectionSummary`, the service emits:

- `type`: `detected_entity`
- `label`: `entity (source-stage)`
- `confidence`: the categorical value, a bounded numeric representation, or
  empty when absent
- `count`: aggregate occurrence count

The current OpenShell `Finding` message has no dedicated source field.
Stage provenance is therefore secondary text in the bounded label while the
entity remains primary. Matched content, offsets, patterns, and raw engine
metadata never cross the protobuf boundary.

## Concurrency model

The gRPC server accepts at most 16 concurrent RPCs. Synchronous processing uses
a dedicated executor and semaphore with four active slots:

```text
gRPC event loop
      |
      | acquire processing slot
      v
4-slot semaphore
      |
      v
4-thread executor
      |
      v
RequestProcessor.process
      |
      v
ordered engine pipeline
```

This keeps synchronous engine work off the async event loop and bounds the
number of active processor runs.

Cached processors, engine instances, and injected resources may be used by
multiple worker threads. They must retain no mutable per-request state and must
be safe for concurrent access.

## Cancellation

Cancelling an async RPC cannot stop Python code already running in its worker
thread. The service shields the worker bridge and releases the semaphore slot
only after that worker actually finishes.

Cancellation therefore cannot create more than four active processor runs.
An engine should pass the remaining shared timeout to any delegated API that
supports bounded execution. A non-preemptible call continues to occupy its slot
until it exits.

During shutdown, the server stops gRPC and waits for active executor work.

## Error mapping

`PrivacyGuardError` classifies each cataloged failure as invalid input or
internal failure:

| Error kind | gRPC status |
| --- | --- |
| `invalid_input` | `INVALID_ARGUMENT` |
| `internal` | `INTERNAL` |

This mapping applies to evaluation. `ValidateConfig` instead returns
`valid=false` with a content-safe reason.

Unexpected failures become `unexpected_service_failure`. The service does not
return caught collaborator messages or exception chains.

A gRPC failure is distinct from a successful policy deny. OpenShell applies the
middleware registration's failure behavior when an RPC fails. A policy deny is
a successful RPC result that explicitly stops the request.

## Server lifecycle and discovery

`MiddlewareServer` is the high-level API. It owns:

```text
EngineRegistry
  -> PrivacyGuardMiddleware
  -> gRPC server
```

The built-in registry includes `RegexEngine`. Operators register custom engines
and resource-backed tool integrations before registry finalization, then pass
that registry to `MiddlewareServer`.

The registry is an explicit application-scoped dependency, not a global
singleton. `MiddlewareServer` and `PrivacyGuardMiddleware` reject unfinalized
registries. A deployment creates and finalizes one registry during startup;
cached processors then construct configured stage engines from that registry.
Different middleware applications in the same process may intentionally use
different engine inventories or runtime resources.

The CLI accepts an operator registry factory in `module:factory` form. The
factory is invoked once, must return a finalized `EngineRegistry`, and supplies
the same engine inventory to discovery, schema generation, or serving:

```bash
privacy-guard --registry-factory my_engines:create_registry engines
privacy-guard --registry-factory my_engines:create_registry schema
privacy-guard --registry-factory my_engines:create_registry serve
```

The factory is trusted operator code imported into the Privacy Guard process.
It is not a policy-controlled plugin hook.

The server:

1. creates an unstarted async gRPC server with receive and concurrency limits
2. binds the configured address
3. starts and waits for termination
4. stops gRPC
5. closes the middleware executor

A bind failure becomes the stable `server_bind_failed` error.

The CLI exposes:

```bash
privacy-guard engines
privacy-guard schema
privacy-guard serve --listen 127.0.0.1:50051
```

Entity behavior comes from policy configuration, not server startup flags.
Engine implementations and operational resources come from the selected
application registry.

## Upstream protocol work

The intended large-catalog and discovery experience requires coordinated
OpenShell changes for:

- a preparation operation that accepts expanded configuration larger than the
  current 64 KiB evaluation limit
- canonical configuration fingerprints on evaluations
- typed cache-miss recovery
- finalized policy schema and engine discovery in the manifest
- a dedicated finding source field

These are protocol evolution items, not local implementation hooks. Until they
land upstream, Privacy Guard continues to validate the per-evaluation config,
repopulate its local cache when needed, expose discovery through the CLI, and
render stage provenance inside the finding label.

## Testing the boundary

Service tests should cover:

- protobuf/domain translation
- manifest fields supported by the current protocol
- pure policy validation
- phase, body-size, and UTF-8 validation
- cache hit and reconstruction behavior
- detect, replacement, block, and limit serialization
- finding encoding and limits
- gRPC status mapping
- worker-slot behavior under cancellation
- startup, bind failure, and shutdown

Engine algorithms and ordered policy semantics belong in engine and processor
tests unless transport translation is essential to the case.

[Back to the architecture overview](index.md)
