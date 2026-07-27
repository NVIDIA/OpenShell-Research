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
| `ValidateConfig` | Validates supplied policy configuration and registered resources |
| `EvaluateHttpRequest` | Validates transport input, resolves a processor, processes text, and returns a decision |

`Describe` advertises only
`SUPERVISOR_MIDDLEWARE_PHASE_PRE_CREDENTIALS`. Evaluations at any other phase
are invalid input.

The current manifest message has no field for the finalized policy schema or
engine discovery metadata. Those are available through the local
`privacy-guard configuration-schema` and `privacy-guard engines` commands.

## Configuration lifecycle

The current protocol carries a complete `google.protobuf.Struct` on both
validation and every request evaluation.

`ValidateConfig`:

1. rejects an encoded `Struct` larger than 64 KiB before conversion
2. converts the `Struct` to a mapping
3. validates the registry-built discriminated policy model
4. validates each exact engine config against registered resources
5. validates replacement support for a replace action
6. returns `valid=true`, or a content-safe reason

It does not construct engines, populate the processor cache, contact model
providers, download resources, or write artifacts. Validation runs in the
bounded worker pool so catalog parsing and engine-specific checks do not block
the async gRPC event loop.

During evaluation, the service validates and normalizes the same config,
computes its canonical SHA-256 fingerprint, and resolves a configured processor
from an LRU cache bounded by both 16 entries and 32 MiB of prepared-state
weight. A cache miss constructs engines directly from the exact stage configs
and operator-injected resources, then constructs the `RequestProcessor`.
Validation, cache resolution, engine construction, strict UTF-8 decoding, and
processing all run in the bounded worker pool. Validation still occurs for
every evaluation. Equivalent normalized Regex catalogs may reuse an entry in
the independent compiled-rule cache during validation and engine construction.

Regex maintains two independent LRU caches. Parsed file catalogs retain at most
64 entries and 8 MiB of source files. Compiled rules retain at most 128 catalogs
and 32 MiB; each entry weighs its canonical catalog bytes plus 4 KiB per rule.
Evicting a compiled-rule entry only removes that cache's reference. A processor
that already uses those rules remains valid.

Processor-cache admission counts canonical configuration bytes and the full
estimated Regex state held by every configured Regex stage. Each processor
receives that full charge even when an equivalent compiled tuple is also
referenced by the compiled-rule cache or another processor. This conservative
accounting intentionally favors simple, independent caches over exact
shared-memory accounting.

An otherwise valid entry larger than its cache's byte budget is built and used
for the current operation but is not retained. Eviction or a skipped entry
never makes a policy invalid. Content-safe debug events report only the cache
name and weight, plus the number of evicted entries.

The caches are protected for concurrent access and are not
correctness-relevant. Eviction or process restart simply causes reconstruction
from a later evaluation's expanded config. Operators with consistently larger
catalogs should expect more frequent preparation rather than unbounded
retention.

Each cached processor receives the server's operational processing timeout.
The default is 1 second shared across every configured stage. Operators may set
up to 30 seconds with `privacy-guard serve --timeout-seconds`; programmatic
applications pass the same `timeout_seconds` argument to `PrivacyGuardServer`.
OpenShell's outer middleware `timeout` must include the processing timeout plus
additional headroom for worker queueing, repeated configuration validation,
cache resolution, and engine construction so the supervisor does not end the
RPC first.

## Incoming requests

For each evaluation, the service:

1. validates the pre-credentials phase
2. validates the encoded context (4 KiB), configuration (64 KiB), target
   (32 KiB), headers (128 entries and 64 KiB), and body (4 MiB) limits
3. acquires one bounded worker slot
4. converts transport-safe integral numbers, validates the maximum ten stages,
   and resolves the supplied policy config in that worker
5. allows an empty body without invoking an engine
6. decodes a non-empty body as strict UTF-8
7. calls `RequestProcessor.process(text)` in the same worker

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
- `confidence`: the categorical value or empty when absent
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
configuration validation
and processor resolution
      |
      v
RequestProcessor.process
      |
      v
ordered engine pipeline
```

This keeps synchronous configuration and engine work off the async event loop
and bounds the number of active operations.

Cached processors, engine instances, and injected resources may be used by
multiple worker threads. They must retain no mutable per-request state and must
be safe for concurrent access.

## Cancellation

Cancelling an async RPC cannot stop Python code already running in its worker
thread. The service shields the worker bridge and releases the semaphore slot
only after that worker actually finishes.

Cancellation therefore cannot create more than four active synchronous
operations.
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

## Programmatic server lifecycle

`PrivacyGuardServer` is the high-level programmatic API. It owns:

```text
EngineRegistry
  -> PrivacyGuardMiddleware
  -> gRPC server
```

The built-in registry includes `RegexEngine`. Operators register custom engines
and resource-backed tool integrations before registry finalization, then pass
that registry to `PrivacyGuardServer`.

The registry is an explicit application-scoped dependency, not a global
singleton. `PrivacyGuardServer` and `PrivacyGuardMiddleware` reject unfinalized
registries. A deployment creates and finalizes one registry during startup;
cached processors then construct configured stage engines from that registry.
Different middleware applications in the same process may intentionally use
different engine inventories or runtime resources.

Synchronous applications use:

```python
from privacy_guard.engines.registry import create_builtin_registry
from privacy_guard.service import PrivacyGuardServer

server = PrivacyGuardServer(
    create_builtin_registry(),
    timeout_seconds=5,
)
server.serve_sync("127.0.0.1:50051")
```

Async applications call `await server.serve_async(address)` instead. The
explicit suffixes make the blocking behavior visible at each call site. Both
entry points use the same server instance and lifecycle.

The listen address uses `host:port` or bracketed IPv6 `[address]:port` form,
with an explicit port from 1 through 65535. Privacy Guard rejects zero and
out-of-range numeric ports before calling gRPC, then verifies that gRPC bound
the requested port before starting the service.

The server:

1. creates an unstarted async gRPC server with receive and concurrency limits
2. binds the configured address
3. starts and waits for termination
4. stops gRPC
5. closes the middleware executor

A bind or asynchronous gRPC startup `RuntimeError` becomes the stable
`server_bind_failed` error. Its catalog operation is the broader `server.start`
phase so the rendered failure does not mislabel a post-bind startup error.

The server module has no command framework, module-import-string, discovery,
schema-rendering, or command-logging responsibilities.

## Command-line application

`privacy_guard.cli` owns the Typer application. The `--registry-factory` option
identifies a Python function in `module:function` form. At startup, the CLI
imports the module, calls the function once, and uses the returned finalized
`EngineRegistry` for the selected command:

```bash
privacy-guard --registry-factory my_engines:create_registry engines
privacy-guard --registry-factory my_engines:create_registry configuration-schema
privacy-guard --registry-factory my_engines:create_registry serve
```

This is a deployment setting, not part of an OpenShell policy. The person
starting Privacy Guard chooses the function, and importing its module executes
Python code, so it must refer to code that the deployer controls. A policy
cannot select a factory or cause Privacy Guard to import a module.

Applications that start `PrivacyGuardServer` from Python do not need this CLI
option. They create the registry normally and pass it to the server:

```python
registry = create_registry()
PrivacyGuardServer(registry, timeout_seconds=5).serve_sync()
```

The CLI exposes:

```bash
privacy-guard engines
privacy-guard configuration-schema
privacy-guard serve --listen 127.0.0.1:50051 --timeout-seconds 5
```

Entity behavior comes from policy configuration, not server startup flags.
Engine implementations and operational resources come from the selected
application registry. The processing timeout is an operational server bound,
not policy-controlled behavior.

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
