---
title: System architecture
description: Privacy Guard components, boundaries, state, and request-processing structure.
agent_markdown: true
---

# System architecture

Privacy Guard is a pre-credentials OpenShell middleware. It translates an
OpenShell HTTP-request evaluation into one text-processing run and returns an
allow, replacement, or deny result.

This is an experimental network-middleware boundary. It does not mediate
harness filesystem writes, so raw prompts and session histories may be stored
before a request reaches Privacy Guard.

## System boundary

![Privacy Guard component architecture. The service layer translates
OpenShell gRPC messages, the request processor applies policy, and registered
engines detect or replace entities without depending on the transport
layer.](../assets/diagrams/component-architecture.svg)

OpenShell owns request routing and provider credential attachment. Privacy
Guard does not send provider requests.

## Components

Source paths below are relative to
`projects/privacy-guard/src/privacy_guard/`.

| Component | Responsibility |
| --- | --- |
| `cli.py` | CLI commands, registry-factory loading, engine discovery, schema output, and server startup |
| `gateway_config.py` | Safe updates to OpenShell gateway middleware registrations |
| `service/` | gRPC lifecycle, protobuf conversion, transport validation, active configuration, worker scheduling, and result serialization |
| `request_processor.py` | Ordered stage execution, shared timeout, aggregation, and policy action |
| `engines/base.py` | Engine lifecycle and result-contract enforcement |
| `engines/registry.py` | Engine and resource registration, policy schema construction, discovery, and engine creation |
| `engines/regex.py` | Regex catalog validation, matching, overlap handling, caching, and replacement |
| `config.py` | Policy stages and action models |
| `base.py` | Shared strict immutable domain-model base |
| `timeout.py` | Monotonic shared request deadline |
| `errors.py` | Stable content-safe error catalog |
| `logging.py` | Standard-library logging configuration and content-safe records |

Only `service/` imports gRPC and generated protobuf bindings. Engines and the
request processor use domain models and can be tested without the transport
layer.

## Configuration and runtime resources

Privacy behavior comes from the OpenShell policy:

- ordered stages
- concrete engine configuration
- entity definitions
- replacement recipes
- final `detect`, `block`, or `replace` action

Operational resources come from the running application:

- installed engine implementations
- clients and SDK adapters
- endpoints and credential providers
- approved models or profiles
- processing timeout

`EngineRegistry.finalize()` constructs one Pydantic discriminated union from
the exact config type registered for each engine. `stage.config.engine` selects
the union member.

## Text boundary

The service validates request bytes and decodes a non-empty body as strict
UTF-8. The request processor receives exactly one `str`.

Headers, target, request ID, content type, and protobuf values do not cross the
processor boundary. Privacy Guard does not parse JSON or create structured
regions.

Detect and block leave the original bytes untouched. Replace UTF-8 encodes the
final text and returns it as the new body.

## Processing pipeline

![Privacy Guard processing pipeline from validated policy and input text
through strategy selection, one shared timeout, ordered engine stages,
validation, aggregation, and the final policy
decision.](../assets/diagrams/processing-pipeline.svg)

Engines receive `DETECT` or `REPLACE`, not the user-facing policy action.
Blocking is applied after detection by `RequestProcessor`.

## State

Privacy Guard retains:

- one active validated policy
- one configured `RequestProcessor`
- immutable configured engine instances
- operator-injected resources
- a bounded compiled Regex catalog cache

It does not retain request text, detections, replacement mappings, or
cross-request entity memory.

Equal validated configuration reuses the active processor. A changed
configuration is fully prepared and atomically activated. Failed preparation
does not replace the active processor.

## Concurrency

The gRPC server accepts at most 16 concurrent RPCs. Synchronous configuration
and processing use a four-slot worker pool.

Engine instances and resources can be used by multiple worker threads. They
must keep per-request state local to the call and support concurrent access.
Policy preparation is serialized; evaluations already using the previous
processor may finish while later evaluations use the replacement.

## Result boundary

The processor returns only:

- allow or deny
- final replacement text when applicable
- stage-qualified detection summaries
- a stable deny reason when applicable

Framework-controlled detection summaries omit matched text, context, offsets,
patterns, and raw engine metadata. Custom engines must keep entity identifiers
stable and independent of request text.

## Read next

- [Request lifecycle](request-lifecycle.md)
- [Service boundary](service-boundary.md)
- [Configure policies](../configuration.md)
- [Add a custom engine](../engines/custom.md)
- [Limits and failure behavior](../reference/limits-and-failures.md)
