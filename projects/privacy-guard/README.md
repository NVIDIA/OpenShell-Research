# Privacy Guard

Privacy Guard is an OpenShell supervisor middleware that detects and optionally
replaces sensitive entities in provider-bound request text before credentials
are attached.

This release is a clean-break redesign. It does not preserve the former
`Scanner`, `FormatHandler`, JSON traversal, `observe`, `redact`, startup catalog,
or scanner-profile APIs. A processor run accepts one UTF-8 text body and runs
the policy's entity-processing stages in order.

## Policy experience

The OpenShell policy owns entity behavior: ordered stages, each engine's exact
configuration, and the final `detect`, `block`, or `replace` action.

```yaml
entity_processing:
  stages:
    - name: identifiers
      config:
        engine: regex
        pattern_catalog:
          entities:
            - name: email
              patterns:
                - pattern: '(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![\w.-])'
                  confidence: high
        replacement:
          strategy: template
          template: "[{entity}]"
on_detection:
  action: replace
```

`entity_processing.stages` is ordered. In replace mode, each stage receives the
preceding stage's processed text. Detect and block run the same engines with the
detection-only strategy, so replacement recipes may remain configured but
dormant.

`pattern_catalog` accepts either the structured catalog or a relative path to a
complete `.yaml` or `.yml` catalog. File-backed catalogs are resolved beneath
Privacy Guard's working directory and normalized to the same
`RegexPatternCatalog` model as inline input. Absolute paths, traversal, symlinks,
unsafe YAML tags, aliases, duplicate keys, invalid UTF-8, and oversized files
are rejected. The
[RegexEngine end-to-end example](examples/regex-engine/README.md) passes its
catalog as `patterns.yaml`.

## Architecture

```text
OpenShell HttpRequestEvaluation
  -> strict UTF-8 decode
  -> finalized Pydantic policy union (config.engine discriminator)
  -> canonical config fingerprint and bounded processor cache
  -> RequestProcessor.process(one text string)
       -> stage 1 engine.run(current text)
       -> stage 2 engine.run(stage 1 text)
       -> ...
  -> policy action: detect, block, or replace
  -> safe aggregated entity findings
  -> OpenShell HttpRequestResult
```

The policy action never crosses the engine boundary. Engines receive only
`EntityProcessingStrategy.DETECT` or `EntityProcessingStrategy.REPLACE`.
Blocking is a request-level disposition owned by `RequestProcessor`.

The copied `proto/supervisor_middleware.proto` and generated bindings are owned
by OpenShell. Update them only through the repository's middleware-kit workflow;
never hand-edit them. Today's protocol carries a `google.protobuf.Struct`
configuration on each evaluation, so Privacy Guard validates and caches it
internally. Large-catalog preparation RPCs, evaluation fingerprints, manifest
schema fields, and a dedicated finding-source field require a coordinated
change in the canonical OpenShell protocol rather than a private proto fork.

## Built-in engines

### RegexEngine

`RegexEngine` compiles configured patterns once and supports overlapping
detection and deterministic template replacement. It preserves numeric
backreferences by wrapping each configured pattern in a non-capturing group
followed by a private named marker. Pattern names are optional diagnostic
identities; `pattern` is the only field containing the regex string. Equivalent
normalized catalogs reuse a bounded compiled-rule cache across validation and
engine construction.

The third-party `regex` backend provides enforceable per-search timeouts.
Explicit `ignore_case`, `multiline`, `dot_all`, and `ascii` flags are supported;
inline flags and user-defined named groups are rejected to protect the wrapper
contract.

Privacy Guard owns the catalog schema but maintains no authoritative patterns.

## Custom engines

Custom engines are a first-class extension point. Authors declare one typed
config, optional typed resources, `supported_strategies`, and `_run`. They do not
write `__init__`; `_initialize` is optional, and `@override` is not required.
The public wrapper validates strategy support, input, timeout, spans, output
size, mutation behavior, and result cardinality for every returned result.
`TextProcessingResult.from_detections()` is an optional safe materializer for
lazy detection streams, not the enforcement boundary.

Resource-backed engines define an `EngineResources` subclass containing their
operator-owned runtime dependencies. Resource bundles may contain initialized
tool clients, SDK adapters, models, endpoints, or credential providers, but
must contain no policy behavior or per-request state and must be safe for
concurrent use. Resource-free engines omit the second
`EntityProcessingEngine` generic argument entirely.

The first NeMo Anonymizer integration will be implemented as a custom engine,
not as a built-in or placeholder abstraction in Privacy Guard.

Application startup registers engines and operator-owned resources, then
returns one finalized registry:

```python
from privacy_guard.engines.registry import EngineRegistry


def create_registry() -> EngineRegistry:
    registry = EngineRegistry(include_builtin_engines=True)
    registry.register(AcmeEngine, resources=AcmeResources(client=client))
    return registry.finalize()
```

Set `include_builtin_engines=True` when a custom deployment should extend the
standard engine inventory. Leave it at the default `False` for an intentionally
isolated registry.

Pass that factory to every CLI operation so discovery, schema generation, and
the running server use the same engine inventory. The module must be installed
or otherwise present on Python's import path:

```bash
uv run privacy-guard --registry-factory my_engines:create_registry engines
uv run privacy-guard --registry-factory my_engines:create_registry configuration-schema
uv run privacy-guard --registry-factory my_engines:create_registry serve
```

The [custom engine end-to-end example](examples/custom-engine/README.md)
contains one Python file with a typed detection config, engine implementation,
and registry factory, plus its OpenShell policy and walkthrough. It also shows
the explicit `PYTHONPATH` setup needed when the factory is a standalone local
module rather than an installed package.

The registry is application-scoped, not a process-global singleton. A
`PrivacyGuardServer` requires an explicit finalized registry. The finalized
registry builds a Pydantic discriminated union containing the exact config type
of every registered engine, so `stage.config` round-trips without dropping
engine-specific or replacement-variant fields.

The base installation has an explicit built-in registry containing
`RegexEngine`:

```python
from privacy_guard.engines.registry import create_builtin_registry

registry = create_builtin_registry()
```

## Programmatic server

The server is a library API independent of the command-line application:

```python
from privacy_guard.engines.registry import create_builtin_registry
from privacy_guard.service import PrivacyGuardServer

server = PrivacyGuardServer(
    create_builtin_registry(),
    timeout_seconds=5,
)
server.serve_sync("127.0.0.1:50051")
```

`serve_sync()` is the blocking entry point. Async applications use the
explicitly named asynchronous counterpart:

```python
await server.serve_async("127.0.0.1:50051")
```

Custom applications pass their own finalized registry to
`PrivacyGuardServer`. The server owns the middleware adapter, bounded gRPC
transport, worker executor, and shutdown lifecycle; it does not load Python
modules, select engines, generate schemas, or parse command-line options.

Privacy Guard emits records through Python's standard `privacy_guard` logger
and does not configure logging when imported. Applications may use their own
standard-library logging setup or opt into Privacy Guard's concise console
format:

```python
from privacy_guard.logging import ColorMode, LoggingConfig, configure_logging

configure_logging(LoggingConfig(level="DEBUG", color_mode=ColorMode.AUTO))
```

Repeated calls replace only the handler installed by `configure_logging()`;
application-owned handlers are preserved. The CLI uses this same configuration
path. The default format includes the date, time, padded level, logger name,
and message. It adds level-aware ANSI colors when writing to an interactive
terminal and stays plain when redirected. Set `color_mode=ColorMode.ALWAYS` or
`color_mode=ColorMode.NEVER` for an explicit choice instead of the default
`ColorMode.AUTO`. Its default level is `INFO`, and `--debug` enables
content-safe `DEBUG` diagnostics. `--debug-log-content` additionally logs
complete request and processed text and should be enabled only in a controlled
environment.

## CLI

```bash
uv run privacy-guard engines
uv run privacy-guard configuration-schema
uv run privacy-guard serve \
  --listen 127.0.0.1:50051 \
  --timeout-seconds 5
```

Entity behavior is supplied by OpenShell policy config, not server startup
flags. Deployment startup owns only installed engine implementations and
operator resources such as model profiles, endpoints, clients, and credentials.
The processing timeout is an operational bound shared by every stage; it
defaults to 1 second and may be increased to at most 30 seconds. When increasing
it, configure OpenShell's middleware `timeout` with additional headroom for
worker queueing and configuration or engine preparation so the supervisor does
not end the RPC first. Use `--registry-factory module:factory` for a custom
engine installation.
Registry factories execute operator Python code; load only trusted modules.
The `privacy_guard.cli` module owns the executable application and adapts its
`serve` command to the programmatic server API.

## Safety and limits

- Input and replacement bodies are limited to 4 MiB.
- One monotonic `Timeout` is shared across every stage and result validation.
- Regex searches receive the remaining timeout and fail atomically.
- Intermediate text and detection cardinality are bounded.
- Detect and block never return a body mutation; replace returns final text.
- Findings expose entity, bounded confidence, count, and stage provenance, but
  never matched text, surrounding text, offsets, patterns, or raw tool metadata.
- Engine instances and injected resources must be safe for concurrent requests.
- Cross-request entity memory is intentionally out of scope.

## Updating the OpenShell protocol

Privacy Guard uses
[`openshell-middleware-kit`](../openshell-middleware-kit/README.md) to keep its
copied protocol and generated Python bindings aligned with an OpenShell release.
Install the repository's local `omkit`, then update:

```bash
uv tool install --force ../openshell-middleware-kit
omkit update --openshell-version v0.0.90
```

The updater replaces only the copied protocol, generated bindings, lockfile, and
`.openshell-middleware-manifest.json` from a validated temporary copy. Review
those generated changes and run `make check`.

## Development validation

The project Makefile exposes the normal workflow:

```bash
make help
make test PYTEST_ARGS="tests/test_request_processor.py"
make fix
make check
make check-py311
```

`make check` delegates to `scripts/check.sh`, the authoritative local and CI
gate. It runs tests, formatting, lint, `ty`, import smoke, and package builds.
