# Egress Gate

Egress Gate is an extensible OpenShell supervisor middleware service. It
evaluates provider-bound HTTP requests during the pre-credentials phase. Each
request moves through an ordered pipeline of trusted gates. A gate can allow
the request, deny it, or propose validated request mutations.

Gates do not modify request objects in place. The pipeline processor applies
proposed mutations to new local request snapshots. The service returns the
final mutations to the OpenShell supervisor, which applies them to the
intercepted request.

The current released OpenShell `Finding` contract has five fields:
`type`, `label`, `count`, `confidence`, and `severity`. Gate provenance stays
inside the pipeline processor. Egress Gate does not add provenance to findings
or labels.

## Installed quickstart

After installing the package with your deployment's Python 3.11+ tooling, these
commands work from any directory and do not depend on repository-only files:

```bash
egress-gate gates list
egress-gate gates schema
egress-gate validate --policy /absolute/path/to/your-policy.yaml
egress-gate serve --listen 127.0.0.1:50051
```

## Source-checkout quickstart

The example policies, cases, and extended documentation are repository assets;
they are not installed with the Python distribution. From
`projects/egress-gate/` in a source checkout, `uv` 0.11+ prepares the project
environment before it starts each command:

```bash
uv run egress-gate gates list
uv run egress-gate gates schema
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
gates:
  - name: identifiers
    kind: regex
    scan:
      kind: body
      action:
        kind: replace
        template: "[{entity}]"
    pattern_catalog: patterns.yaml
default_decision: allow
```

The shipped registry contains exactly `regex`. Its `scan` selects the body,
path, query, or selected request headers. Each scan contains its `action`.
Every scan supports `detect` and `deny`. A body scan also supports `replace`.
The typed configuration prevents unsupported combinations. A replace action
preserves an explicit body-replacement intent even when the resulting bytes
equal the input. Add custom trusted gates through `--registry`.

Small stateless gates can use the optional `registry.gate` helper. Gates that
need initialization, helper bases, or typed resources use the full class-based
`Gate` API.

```bash
uv run egress-gate --registry my_gates:registry gates list
uv run egress-gate --registry my_gates:registry serve
```

OpenShell owns interception, routing, and credential attachment. Egress Gate
does not act as an HTTP proxy, inspect responses, or protect data already
written by a harness to disk.

## Python server API

```python
from egress_gate.gates import create_builtin_registry
from egress_gate.service import EgressGateServer

server = EgressGateServer(
    create_builtin_registry(),
    timeout_middleware_processing=10,
)
server.serve_sync("127.0.0.1:50051")
```

`timeout_middleware_processing` is the actual service setting, in seconds. The
service turns it into one `Timeout` per evaluation and passes that same deadline
through slot acquisition, policy preparation, and `RequestProcessor.process`.
The OpenShell gateway enforces its separately configured
`timeout_gateway_ceiling` as an upper bound.

## Documentation and examples

- [Overview](https://github.com/NVIDIA/OpenShell-Research/blob/main/projects/egress-gate/docs/index.md)
- [Configuration](https://github.com/NVIDIA/OpenShell-Research/blob/main/projects/egress-gate/docs/configuration.md)
- [Test policies offline](https://github.com/NVIDIA/OpenShell-Research/blob/main/projects/egress-gate/docs/evaluation.md)
- [Operations](https://github.com/NVIDIA/OpenShell-Research/blob/main/projects/egress-gate/docs/operations.md)
- [Gate authoring](https://github.com/NVIDIA/OpenShell-Research/blob/main/projects/egress-gate/docs/gates/custom.md)
- [Regex gate](https://github.com/NVIDIA/OpenShell-Research/blob/main/projects/egress-gate/docs/gates/regex.md)
- [Architecture](https://github.com/NVIDIA/OpenShell-Research/blob/main/projects/egress-gate/docs/architecture/index.md)
- [Limits and failures](https://github.com/NVIDIA/OpenShell-Research/blob/main/projects/egress-gate/docs/reference/limits-and-failures.md)
- [Regex redaction composition](https://github.com/NVIDIA/OpenShell-Research/tree/main/projects/egress-gate/examples/regex-redaction)
- [Function-based custom gate](https://github.com/NVIDIA/OpenShell-Research/tree/main/projects/egress-gate/examples/custom-gate)
- [Class-based custom gate](https://github.com/NVIDIA/OpenShell-Research/tree/main/projects/egress-gate/examples/class-based-gate)

## Development

```bash
make help
make test PYTEST_ARGS="tests/gates tests/test_request_processor.py"
make check
```

Only `service/` imports generated protobuf/gRPC bindings. Do not edit
`plans/egress-gate-refactor.md` as part of implementation work.
