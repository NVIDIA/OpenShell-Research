# Egress Gate

Egress Gate is OpenShell pre-credentials middleware. It receives one bounded,
immutable byte-oriented `HttpRequest`, runs an ordered pipeline of trusted
request-level gates, and returns an explicit allow, deny, or mutation result.

## Development commands

Run commands from `projects/egress-gate/`.

- List targets: `make help`
- Run all checks: `make check`
- Check Python 3.11: `make check-py311`
- Run focused tests: `make test PYTEST_ARGS=tests/test_request_processor.py`

Run focused tests while working and `make check` before handoff.

## Engineering approach

- Backwards compatibility with the removed legacy policy API is not a concern.
  Do not restore old schemas, imports, names, aliases, or obsolete pipeline terms.
- Gates are trusted application code. Capabilities enforce declared output
  behavior and finding declarations; read capabilities are discovery metadata,
  not Python isolation or a security sandbox.
- Keep request bytes and headers in immutable domain models. Only the service
  package imports gRPC or generated protobuf bindings.
- Use the smallest owner for each validation. The service owns exact encoded
  protobuf limits; domain models own bounded scalar and aggregate invariants.
- Avoid speculative infrastructure, policy caches, observer interfaces, and
  transport abstractions.

## Project map

- `src/egress_gate/gates/`: `Gate`, helper bases, registry, regex-body, and
  request-rules
- `src/egress_gate/config.py`: strict `pipeline.gates` and `default_decision`
  policy models
- `src/egress_gate/request.py`: protobuf-free request and ordered patch models
- `src/egress_gate/result.py`: gate evaluations, five-field findings, provenance,
  traces, metadata, and final results
- `src/egress_gate/request_processor.py`: shared deadline, current-request
  mutation flow, terminal controls, aggregation, and default decisions
- `src/egress_gate/cli.py`: content-safe offline policy-corpus evaluation
- `src/egress_gate/service/`: gRPC lifecycle and the only protobuf adapter
- `src/egress_gate/timeout.py`: monotonic shared request deadline
- `src/egress_gate/errors.py`: stable content-safe error catalog
- `src/egress_gate/gateway_config.py`: safe OpenShell gateway TOML management
- `tests/`: domain, gate, processor, service, and boundary tests

Before changing `request_processor.py`, `gates/`, or `service/`, read the
architecture overview and matching topic page under `docs/architecture/`.

## Gate contract

Every gate declares a strict `GateConfig` with a literal `gate` discriminator,
an optional typed `GateResources` bundle, `GateCapabilities`, and its
`FindingTypeDefinition` declarations. `GateRegistry.finalize()` creates the
exact discriminated pipeline schema for the installed gates and prepares
validated gate instances from trusted application-owned resources.

`Gate.evaluate()` receives the current `HttpRequest` and one shared `Timeout`.
It returns a validated `GateEvaluation` with explicit `proceed`, terminal
`allow`, or terminal `deny` control. A proceeding patch is applied before the
next gate; body replacement intent is preserved even when replacement bytes are
equal to the input. Runtime provenance is added by `RequestProcessor`, never by
gate configuration or gate-produced findings.

Custom gates are trusted and must be safe for concurrent calls. Tests should
exercise concurrent evaluation, but the Python implementation is not claimed to
be deeply immutable. Resource bundles contain operator-owned, concurrency-safe
dependencies and no request state or policy behavior.

## Current built-ins and boundaries

This slice ships exactly two built-ins. `regex-body` preserves bounded catalog
loading, regex matching, overlap resolution, UTF-8 body handling, and
detect/deny/replace modes. `request-rules` owns deterministic matching over
normalized request facts, deny precedence, and terminal rule decisions. Do not
add more built-ins speculatively.

The OpenShell wire `Finding` remains the released five-field contract:
`type`, `label`, `count`, `confidence`, and `severity`. Gate provenance is
runtime-internal in `SourcedFinding` and `DecisionSource`; do not serialize
source or attributes or encode them into labels or result metadata.

The service adapts protobuf messages to `HttpRequest`, validates exact encoded
transport boundaries, and serializes `EgressResult`. Core domain and gate code
must not import gRPC or protobuf. The request model may contain body bytes and
visible pre-credentials headers, but provider credentials are outside this
middleware phase.

## Plan boundaries

The current implementation covers the gate contract, strict pipeline
configuration, finalized registry, regex-body and request-rules behavior,
request processing, single active-policy replacement, and offline evaluation.
The custom semantic gate remains example-owned; semantic types are not part of
the core package or default registry. Do not edit `plans/` as part of
implementation work.
