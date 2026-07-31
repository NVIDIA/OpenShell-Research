# Privacy Guard

Privacy Guard is an OpenShell supervisor middleware that detects, blocks, or
replaces configured entities in provider-bound HTTP request bodies before
OpenShell attaches provider credentials.

It processes the complete request body as UTF-8 text through an ordered
pipeline of entity-processing engines.

> **Experimental:** Privacy Guard is a proof of concept. It reduces exposure on
> provider-bound network requests that OpenShell routes through the middleware;
> it does not guarantee that sensitive data cannot leak.

Privacy Guard does not intercept data before a harness writes it to disk.
Prompts, tool output, transcripts, and session histories may therefore retain
raw sensitive values even when the provider-bound request is later replaced or
blocked. Use harness persistence controls and appropriate storage isolation,
retention, and cleanup in addition to Privacy Guard.

## What it does

| Policy action | Behavior |
| --- | --- |
| `detect` | Allow the original body and report bounded findings |
| `block` | Deny requests containing configured entities |
| `replace` | Allow the final body returned by replacement-capable engines |

Findings contain entity, stage, confidence, and count. Framework-controlled
fields and the built-in `RegexEngine` do not add matched text, surrounding
text, offsets, patterns, headers, or credentials. Custom engines must use
stable entity identifiers that are not derived from request text.

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
uv run privacy-guard add-gateway-registration --host-ip YOUR_HOST_IPV4
uv run privacy-guard remove-gateway-registration --name privacy-guard
uv run privacy-guard serve \
  --listen 0.0.0.0:50051 \
  --timeout-seconds 4
```

`add-gateway-registration` adds or updates a Privacy Guard registration in the
OpenShell gateway TOML. Its registration name must match the policy's
`middleware` field. Restart the gateway after changing registrations.
`remove-gateway-registration` removes one registration by name while preserving
unrelated gateway settings.

The processing timeout is one bound shared by every stage. It defaults to 1
second and cannot exceed 30 seconds. `add-gateway-registration` writes a five-second
OpenShell middleware timeout, so use a shorter processing timeout or edit the
registration to provide more headroom. Rerunning `add-gateway-registration` restores
the five-second value.

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

- [Overview and end-to-end quickstart](docs/index.md)
- [Configure policies](docs/configuration.md)
- [Run and operate Privacy Guard](docs/operations.md)
- [Use RegexEngine](docs/engines/regex.md)
- [Add a custom engine](docs/engines/custom.md)
- [System architecture](docs/architecture/index.md)
- [Limits and failure behavior](docs/reference/limits-and-failures.md)

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
[`openshell-middleware-manager`](../openshell-middleware-manager/README.md), then run
`make check`.
