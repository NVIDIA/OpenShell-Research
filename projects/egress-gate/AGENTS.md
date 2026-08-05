# Egress Gate

Egress Gate is OpenShell pre-credentials middleware. It receives one bounded,
immutable byte-oriented `HttpRequest`, runs an ordered pipeline of trusted
request-level gates, and returns an explicit allow or deny result with optional
request mutations.

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
- Put public declarations before private helpers, implementation types, and
  module state when dependencies allow. Keep private implementation details at
  the bottom of a module rather than interrupting its public surface.
- Do not add validators or runtime type checks that duplicate strict Pydantic
  validation or an already validated caller boundary.
- Avoid speculative infrastructure, policy caches, observer interfaces, and
  transport abstractions.

## Project map

- `src/egress_gate/gates/`: `Gate`, helper bases, registry, and the regex gate
- `src/egress_gate/config.py`: strict `pipeline.gates` and `default_decision`
  policy models
- `src/egress_gate/request.py`: protobuf-free request and request-mutation models
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

Every gate declares a strict `GateConfig` with a literal `kind` discriminator,
an optional typed `GateResources` bundle, `GateCapabilities`, and its
`FindingTypeDefinition` declarations. `GateRegistry.finalize()` creates the
exact discriminated pipeline schema for the installed gates and prepares
validated gate instances from trusted application-owned resources.

Use a required `kind` field for every serialized discriminated union. Each
variant must declare one string literal and its exact fields. Use an enum on a
single model when the selected value does not change the serialized shape; do
not create a union only to replace an enum.

`Gate.evaluate()` receives the current `HttpRequest` and one shared `Timeout`.
It returns a validated `GateEvaluation` with explicit `proceed`, terminal
`allow`, or terminal `deny` control. Request mutations from a `proceed` result
are applied before the next gate; body replacement intent is preserved even
when replacement bytes are equal to the input. The pipeline processor adds
provenance through `RequestProcessor`, never through gate configuration or
gate-produced findings.

Custom gates are trusted and must be safe for concurrent calls. Tests should
exercise concurrent evaluation, but the Python implementation is not claimed to
be deeply immutable. Resource bundles contain operator-owned, concurrency-safe
dependencies and no request state or policy behavior.

## Current built-ins and boundaries

This slice ships exactly one built-in. `regex` selects one typed body, path,
query, or header scan and preserves bounded catalog loading, matching,
overlap resolution, and detect/deny actions. Body scans also support strict
UTF-8 replacement. Deterministic network request policy belongs to OpenShell.
Do not add more built-ins speculatively.

The OpenShell wire `Finding` remains the released five-field contract:
`type`, `label`, `count`, `confidence`, and `severity`. Gate provenance stays
internal to the pipeline processor in `SourcedFinding` and `DecisionSource`;
do not serialize source or attributes or encode them into labels or result
metadata.

The service adapts protobuf messages to `HttpRequest`, validates exact encoded
transport boundaries, and serializes `EgressResult`. Core domain and gate code
must not import gRPC or protobuf. The request model may contain body bytes and
visible pre-credentials headers, but provider credentials are outside this
middleware phase.

## Plan boundaries

The current implementation covers the gate contract, strict pipeline
configuration, finalized registry, regex behavior, request processing,
single active-policy replacement, and offline evaluation.
Semantic or LLM judgment is deferred and must not be added as a built-in,
example implementation, or default dependency. Do not edit `plans/` as part of
implementation work.
