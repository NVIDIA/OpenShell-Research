# Privacy Guard

Privacy Guard is an OpenShell supervisor middleware that detects, blocks, or
replaces configured entities in provider-bound HTTP request bodies before
OpenShell attaches provider credentials.

It processes the complete request body as UTF-8 text through an ordered
pipeline of entity-processing engines.

## What it does

| Policy action | Behavior |
| --- | --- |
| `detect` | Allow the original body and report bounded findings |
| `block` | Deny requests containing configured entities |
| `replace` | Allow the final body returned by replacement-capable engines |

Findings contain entity, stage, confidence, and count. They do not contain
matched text, surrounding text, offsets, patterns, headers, or credentials.

## Developer start

Requirements:

- Python 3.11 or newer
- `uv` 0.11 or newer

From this directory:

```bash
uv sync --locked
uv run privacy-guard engines
uv run privacy-guard configuration-schema
```

Start the built-in `RegexEngine` service locally:

```bash
uv run privacy-guard serve \
  --listen 127.0.0.1:50051
```

Use `0.0.0.0` when OpenShell sandbox supervisors outside the host network
namespace must reach the service. The development server uses plaintext gRPC;
restrict the port to trusted host and sandbox networks.

## Policy configuration

Privacy behavior comes from the OpenShell policy:

```yaml
entity_processing:
  stages:
    - name: identifiers
      config:
        engine: regex
        pattern_catalog:
          entities:
            - name: email
              rules:
                - pattern: '(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![\w.-])'
                  confidence: high
        replacement:
          strategy: template
          template: "[{entity}]"
on_detection:
  action: replace
```

`entity_processing.stages` runs in order. In replace mode, each stage receives
the text returned by the preceding stage. Detect and block invoke engines with
the detection-only strategy.

`RegexEngine` accepts an inline catalog or a relative `.yaml` or `.yml` path:

```yaml
pattern_catalog: patterns.yaml
```

Relative paths resolve beneath Privacy Guard's working directory. Absolute
paths, traversal, symlinks, unsafe YAML tags, aliases, duplicate keys, invalid
UTF-8, and oversized catalogs are rejected.

## CLI

```bash
uv run privacy-guard engines
uv run privacy-guard configuration-schema
uv run privacy-guard configure-gateway --host-ip YOUR_HOST_IPV4
uv run privacy-guard serve \
  --listen 0.0.0.0:50051 \
  --timeout-seconds 5
```

`configure-gateway` adds or updates a Privacy Guard registration in the
OpenShell gateway TOML. Its registration name must match the policy's
`middleware` field. Restart the gateway after changing registrations.

The processing timeout is one bound shared by every stage. It defaults to 1
second and cannot exceed 30 seconds. Configure a longer OpenShell middleware
timeout to include worker queueing, configuration validation, and processor
preparation.

Use a trusted registry factory for custom engines:

```bash
uv run privacy-guard \
  --registry-factory my_engines:create_registry \
  engines

uv run privacy-guard \
  --registry-factory my_engines:create_registry \
  serve
```

## Python server API

```python
from privacy_guard.engines.registry import create_builtin_registry
from privacy_guard.service import PrivacyGuardServer

server = PrivacyGuardServer(
    create_builtin_registry(),
    timeout_seconds=5,
)
server.serve_sync("127.0.0.1:50051")
```

Async applications use:

```python
await server.serve_async("127.0.0.1:50051")
```

## Documentation

- [Overview and end-to-end quickstart](../../docs/documentation/privacy-guard/index.md)
- [Configure policies](../../docs/documentation/privacy-guard/configuration.md)
- [Run and operate Privacy Guard](../../docs/documentation/privacy-guard/operations.md)
- [Use RegexEngine](../../docs/documentation/privacy-guard/engines/regex.md)
- [Add a custom engine](../../docs/documentation/privacy-guard/engines/custom.md)
- [System architecture](../../docs/documentation/privacy-guard/architecture/index.md)
- [Limits and failure behavior](../../docs/documentation/privacy-guard/reference/limits-and-failures.md)

## Runnable examples

- [`examples/regex-engine`](examples/regex-engine/README.md): detect and replace
  email addresses and customer IDs with the built-in engine.
- [`examples/custom-engine`](examples/custom-engine/README.md): implement,
  register, and run a typed custom engine.

## Logging

`--debug` enables content-safe diagnostic records.

`--debug-log-content` logs complete input and processed text. Use it only in a
controlled development environment.

Imported applications can configure the standard `privacy_guard` logger
themselves or use `privacy_guard.logging.configure_logging()`.

## Development

```bash
make help
make test PYTEST_ARGS="tests/test_request_processor.py"
make fix
make check
make check-py311
```

`make check` runs tests, formatting, lint, type checking, an import smoke check,
and a dependency audit.

The copied `proto/supervisor_middleware.proto` and generated bindings are owned
by OpenShell. Update them through the repository's
[`openshell-middleware-kit`](../openshell-middleware-kit/README.md), then run
`make check`.
