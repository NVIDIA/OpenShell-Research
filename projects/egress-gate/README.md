# Egress Gate

Egress Gate is an extensible OpenShell supervisor middleware service. It
evaluates provider-bound HTTP requests during the pre-credentials phase. Each
request moves through an ordered pipeline of trusted gates. A gate can allow
the request, deny it, or return a validated mutation.

The current released OpenShell `Finding` contract has five fields:
`type`, `label`, `count`, `confidence`, and `severity`. Gate provenance stays
inside the runtime. Egress Gate does not add provenance to findings or labels.

## Quickstart

Requirements: Python 3.11+ and `uv` 0.11+.

```bash
uv sync --frozen
uv run egress-gate gates
uv run egress-gate configuration-schema
uv run egress-gate validate \
  --policy examples/regex-redaction/egress-gate-config.yaml
uv run egress-gate serve --listen 127.0.0.1:50051
uv run egress-gate evaluate \
  --policy examples/regex-redaction/egress-gate-config.yaml \
  --cases examples/regex-redaction/cases.yaml
```

Use `0.0.0.0` only when the OpenShell supervisor must reach the service across
network namespaces. The development server uses plaintext gRPC. Restrict its
listen port to trusted networks.

## Policy shape

The registry builds an exact strict schema from installed gate types:

```yaml
pipeline:
  gates:
    - name: identifiers
      config:
        gate: regex-body
        pattern_catalog: patterns.yaml
        mode: replace
        replacement:
          strategy: template
          template: "[{entity}]"
  default_decision: allow
```

The shipped registry contains exactly `regex-body`. It supports `detect`,
`deny`, and `replace`. Replacement mode preserves an explicit body-replacement
intent even when the resulting bytes equal the input. Add custom trusted gates
through `--registry-factory`.

```bash
uv run egress-gate --registry-factory my_gates:create_registry gates
uv run egress-gate --registry-factory my_gates:create_registry serve
```

OpenShell owns interception, routing, and credential attachment. Egress Gate
does not act as an HTTP proxy, inspect responses, or protect data already
written by a harness to disk.

## Python server API

```python
from egress_gate.gates import create_builtin_registry
from egress_gate.service import EgressGateServer

server = EgressGateServer(create_builtin_registry(), timeout_seconds=5)
server.serve_sync("127.0.0.1:50051")
```

The service creates one `Timeout` per evaluation and passes that deadline
through slot acquisition, policy preparation, and `RequestProcessor.process`.

## Documentation and examples

- [Overview](docs/index.md)
- [Configuration](docs/configuration.md)
- [Test policies offline](docs/evaluation.md)
- [Operations](docs/operations.md)
- [Gate authoring](docs/gates/custom.md)
- [Regex-body](docs/gates/regex.md)
- [Architecture](docs/architecture/index.md)
- [Limits and failures](docs/reference/limits-and-failures.md)
- [Regex redaction composition](examples/regex-redaction/README.md)
- [Minimal custom gate](examples/custom_gate/README.md)

## Development

```bash
make help
make test PYTEST_ARGS="tests/gates tests/test_request_processor.py"
make check
```

Only `service/` imports generated protobuf/gRPC bindings. Do not edit
`plans/egress-gate-refactor.md` as part of implementation work.
