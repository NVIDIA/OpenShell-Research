# Egress Gate refactor and reframing plan

## Status

Implemented design specification. The phased sequence below records the
intended construction and acceptance boundaries; it is not a remaining-work
checklist and must not be replayed against the completed refactor.

This plan intentionally makes no provision for backwards compatibility. The
superseded package name, Python imports, CLI, policy schema, public classes,
examples, documentation routes, and tests may all be removed or replaced. Do
not add compatibility aliases, schema translation, deprecation warnings,
legacy command names, or dual implementations.

## Executive decision

Build **Egress Gate** as an extensible OpenShell middleware that evaluates and
transforms sandbox HTTP egress during the pre-credentials request phase.

The product is not a fixed DLP service and is not a standalone forward proxy.
It is an OpenShell supervisor middleware designed directly around OpenShell's
pre-credentials HTTP request contract, with:

- a bounded immutable OpenShell HTTP request model
- an ordered pipeline of strongly typed, registry-provided gates
- explicit proceed, allow, and deny control flow
- validated request mutations
- audit-safe findings and decision metadata
- application-owned resources for custom integrations
- bounded preparation, atomic policy replacement, execution, and concurrency
- a protobuf-free processing core behind the OpenShell gRPC service boundary

Body inspection and redaction are a first-party configuration of the built-in
regex gate, not a separate compatibility layer. The generic registry remains
available for organization-specific gates without shipping another concrete
integration. Deterministic destination and request constraints remain in
OpenShell policy, which already owns egress enforcement.

The refactor does **not** implement an HTTP/HTTPS MITM proxy. OpenShell owns
request interception, routing, egress enforcement, and credential attachment;
Egress Gate owns policy evaluation through the supervisor-middleware
contract. A standalone proxy is outside this project's scope and roadmap. If a
separate proxy project is ever proposed, it must not shape this middleware's
gate API or package structure.

## Working names

Use these names throughout the refactor unless product naming is deliberately
revisited before implementation starts:

| Surface | New name |
| --- | --- |
| Product | Egress Gate |
| Project directory | `projects/egress-gate/` |
| Python distribution | `egress-gate` |
| Python package | `egress_gate` |
| CLI | `egress-gate` |
| Service manifest name | `egress-gate` |
| Documentation route | `documentation/egress-gate/` |
| GitHub workflow | `egress-gate.yml` |

The regex redaction composition uses the same binary, service, import path, and
configuration schema as every other Egress Gate policy.

## Goals

1. Preserve and strengthen customization as a first-class feature.
2. Make regex-based detect, deny, and replace setups concise.
3. Let custom gates reason about the complete bounded request rather than only
   one decoded text body.
4. Retain strict typed configuration generated from the exact installed gate
   registry.
5. Keep policy behavior in policy configuration and operational dependencies
   in application-owned resources.
6. Make gate order, mutation visibility, terminal decisions, defaults, and
   failures explicit and mechanically testable.
7. Preserve content-safe findings, errors, and logs by default.
8. Reuse one prepared active policy and make policy changes atomic without
   restarting Egress Gate.
9. Keep OpenShell protobuf and gRPC details inside `service/` while modeling
    the processing domain directly on OpenShell's request semantics.
10. Provide offline policy evaluation and shadow operation without requiring a
    raw production-traffic database.

## Project non-goals

- Preserving any superseded API or configuration.
- Supporting runtimes or transports outside OpenShell.
- Implementing a forward proxy or TLS interception.
- Inspecting or transforming HTTP responses; the current OpenShell protocol
  exposes only pre-credentials HTTP requests.
- Inspecting files, transcripts, tool calls, or harness persistence.
- Acting as a WAF, network firewall, identity provider, credential broker, or
  general authorization server.
- Providing vendor-specific LLM SDKs or implementing semantic/LLM judgment,
  including as a runnable example. The only built-in gate is regex body.
- Duplicating deterministic host, port, method, path, query, or process rules
  already owned by OpenShell policy. Organization-specific request logic may
  still be implemented through the custom-gate API when OpenShell policy is
  insufficient.
- Persisting request bodies, headers, query strings, or response content by
  default.
- Automatically publishing policies inferred from observed traffic.
- Hot-reloading installed Python code or registry factories.
- Serving genuinely different active policy configurations concurrently from
  one Egress Gate service; deploy separate service instances for that case.
- Carrying the names or branding of external comparison tools into Egress Gate
  code, configuration, examples, tests, logs, metrics, or product documentation.

## Design principles

### Terminology is part of the contract

Use the nouns consistently in code, configuration, documentation, diagnostics,
and tests:

- **Egress Gate** is the OpenShell middleware product and service.
- A **gate type** is one registered `Gate` implementation selected by the
  literal `config.gate` discriminator. `GateType` is that stable discriminator,
  never a Python class name.
- A **configured gate** or **gate instance** is one named pipeline entry with a
  gate type and exact configuration. `GateName` is its stable instance identity.
- A **gate** is the general extension noun when the type/instance distinction is
  irrelevant. It evaluates the current request and may produce findings,
  propose mutations, proceed, allow, or deny. A gate need not make a terminal
  decision.
- A **pipeline** is the ordered composition of configured gates plus its
  required default decision.
- `GateEvaluation` is the validated output of one gate invocation.
- `EgressResult` is the final middleware-domain result after the pipeline has
  terminated or applied its default decision.

Diagnostics, traces, discovery, and any future metrics use `GateName` and
`GateType`, not implementation class names. Reserve **pipeline** or
**composition** for multi-gate behavior; do not call an entire pipeline a gate.

Do not use “stage” as an internal or public synonym for gate. OpenShell's
pre-credentials **phase** remains a separate protocol concept and should
still be called a phase.

### The current implementation is the design reference

The refactor changes the product scope and public contract, but it should keep
the general taste of the current implementation. Its layout and extension
mechanics were deliberate and are the starting point for the redesign, not
legacy structure to discard casually.

In particular, preserve these qualities unless the new behavior provides a
concrete reason to change one:

- a small, readable package map with behavior-owning modules
- strict immutable Pydantic domain models
- public orchestration methods wrapping protected extension hooks
- exact typed configuration for every registered implementation
- optional typed operational resources injected at construction
- no arbitrary custom constructors
- registry finalization before serving
- OpenShell protobuf and gRPC isolation under `service/`
- one shared monotonic timeout across an evaluation
- stable content-safe errors, logs, and findings
- tests that mirror source boundaries
- focused dependencies and an understandable import graph
- public declarations before private implementation details where practical

Broader scope does not justify framework-shaped abstraction layers, deep
directory nesting, generic dependency injection, event buses, or a
proliferation of interfaces. Extend the current design in the smallest coherent
way: widen the processor input from one text value to one immutable HTTP
request, widen the registry from entity engines to request gates, and retain
the existing construction, validation, execution, and service seams where they
still own the same behavior.

### The runtime owns policy execution

The runtime owns ordering, deadlines, gate invocation, gate-evaluation
validation, mutation application, decision termination, finding aggregation,
error translation, and final result construction. A custom gate cannot
redefine those mechanics.

### Gates own one explicit behavior

A gate is configured for one request-level responsibility, such as matching
request facts, inspecting or rewriting body text, or integrating a custom
external decision service. Prefer specific gate classes over a generic
callback or interceptor API.

### The current request is the only gate input

Each gate sees the request after mutations from all preceding gates. This
preserves ordered replacement behavior and enables a redaction gate to rewrite
a body before a later custom gate sees it.

The runtime may retain the original OpenShell request privately so it can
produce one final mutation result for OpenShell. Gates must not receive an
implicit escape hatch to the unmodified original request.

### Control flow is explicit

Every successful gate returns one of:

- `proceed`: apply validated mutations and invoke the next gate
- `allow`: require an empty patch, stop the pipeline, and allow the current
  request including mutations already applied by earlier proceeding gates
- `deny`: require an empty patch, stop the pipeline, and deny the request

If every gate proceeds, the policy's required `default_decision` determines
the result. There is no implicit allow and no hidden fallback.

An early terminal allow intentionally skips later gates. Configuration and
documentation must make this visible because it bypasses every later custom or
built-in gate.

### Failures are not decisions

Invalid input, invalid configuration, gate contract violations, gate
execution errors, and unexpected failures remain evaluation failures. The
OpenShell middleware registration's `on_error` setting owns their request
effect. A failure must not silently become `proceed` or `allow`.

Expected runtime safety-limit exhaustion returns a stable fail-closed deny when
the request envelope and policy were otherwise valid, following the existing
fail-closed approach. Do not introduce a general passthrough fallback.

Use this normative outcome matrix:

| Condition | Outcome | Active-policy effect | `on_error` |
| --- | --- | --- | --- |
| Invalid phase, envelope, policy, or incoming request bound | gRPC `INVALID_ARGUMENT` | None | Applies |
| Invalid candidate gate configuration | gRPC `INVALID_ARGUMENT` | Candidate is not published | Applies |
| Deadline expiry during validation, slot wait, replacement-lock wait, preparation, or the final pre-publication check | Deny with source `runtime_limit` and code `egress_gate_limit_exceeded` | Candidate is not published | Does not apply |
| Deadline expiry after candidate publication, including gate execution or result construction | Deny with source `runtime_limit` and code `egress_gate_limit_exceeded` | The atomically published candidate remains active; do not roll it back | Does not apply |
| Runtime input or domain-conversion limit before candidate publication | Deny with source `runtime_limit` and code `egress_gate_limit_exceeded` | Candidate is not published | Does not apply |
| Runtime mutation, finding, or encoded-output limit after candidate publication | Deny with source `runtime_limit` and code `egress_gate_limit_exceeded` | The published candidate remains active; do not roll it back | Does not apply |
| Gate contract violation, gate execution failure, or unexpected internal failure | gRPC `INTERNAL` | Any already published candidate remains active; do not roll it back | Applies |
| Explicit gate or pipeline decision | Return the corresponding `EgressResult` | A complete candidate may already have been published | Does not apply |
| RPC cancellation | Propagate cancellation; synchronous work still owns its slot until exit | Do not publish an unpublished candidate | Gateway cancellation behavior applies |

Check the deadline after acquiring the replacement lock and again immediately
before candidate publication. An expired or cancelled request cannot prepare or
publish a new active policy. Runnable registrations and examples configure
OpenShell's `on_error` to deny.

### Data minimization is the default

Findings and logs exclude request content by default. Any traffic-discovery or
content-capture mode must be separately named, opt-in, bounded, and documented
as expanding the trust boundary.

### Semantic judgment is deferred

Do not implement or document a concrete semantic/LLM gate in this refactor,
including under `examples/`. A later proposal must define its own data
minimization, failure, evaluation, and prompt-injection boundaries before any
implementation is added.

## Target architecture

```text
OpenShell gateway
        |
        v
SupervisorMiddleware service boundary
  - validates protobuf bounds and phase
  - converts the evaluation to immutable domain models
  - resolves a prepared policy pipeline
        |
        v
RequestProcessor
  - shared deadline
  - ordered gate execution
  - request mutation validation
  - terminal decision handling
  - finding aggregation
        |
        +--> regex body gate
        +--> custom organization gate
        |
        v
EgressResult
  - allow or deny
  - body/header mutations
  - audit-safe findings
  - stable reason code
        |
        +--> OpenShell HttpRequestResult serialization
        +--> content-safe operational logging
```

## Core domain model

Create protobuf-free immutable models in focused top-level modules such as
`request.py` and `result.py`. They should directly represent the bounded fields
and semantics of OpenShell's `HttpRequestEvaluation` and `HttpRequestResult`,
without pretending to be a generic cross-transport HTTP abstraction. Keep the
package flat unless a directory owns a real family of implementations. Only
`service/` may import gRPC or generated protobuf bindings.

### `HttpRequest`

The gate-visible request contains:

```python
class HttpRequest(StrictDomainModel):
    context: RequestContext
    target: HttpTarget
    headers: tuple[HttpHeader, ...]
    body: bytes
```

`RequestContext` contains the bounded request ID, sandbox ID, and originating
process information already supplied by OpenShell. `HttpTarget` contains
scheme, host, port, method, path, and raw query. `HttpHeader` preserves ordered
repeated fields.

The request remains byte-oriented. Text gates explicitly perform strict
decoding according to their configuration and content requirements. The core
runtime must not assume every HTTP body is UTF-8.

### `RequestPatch`

A gate proposes mutations rather than mutating the request object:

```python
class ExistingHeaderAction(StrEnum):
    APPEND = "append"
    OVERWRITE = "overwrite"
    SKIP = "skip"


class WriteHeaderMutation(StrictDomainModel):
    operation: Literal["write"] = "write"
    name: HeaderName
    value: HeaderValue
    on_existing: ExistingHeaderAction


class RemoveHeaderMutation(StrictDomainModel):
    operation: Literal["remove"] = "remove"
    name: HeaderName


HeaderMutation = Annotated[
    WriteHeaderMutation | RemoveHeaderMutation,
    Field(discriminator="operation"),
]


class RequestPatch(StrictDomainModel):
    replacement_body: bytes | None = None
    header_mutations: tuple[HeaderMutation, ...] = ()
```

`replacement_body=None` means no body replacement, while `b""` explicitly
replaces the body with an empty value. The OpenShell service adapter derives
the wire-level `has_body` flag from that single domain representation. These
protobuf-free header types live in `request.py` and mirror the OpenShell write,
remove, and existing-header actions exactly. Header names match
case-insensitively. Operations apply in tuple order: `append` adds one value at
the end; `overwrite` removes every matching field and appends the new value;
`skip` appends only when no matching field exists; and `remove` removes every
matching field. Unrelated headers retain their relative order.

The runtime validates protected-header restrictions and all per-patch and
request-wide mutation limits before making any operation visible to the next
gate. For an allowed result, it serializes the validated operations from
proceeding gates in the same order without collapsing or synthesizing a
different sequence. Thus intermediate and final wire semantics are identical,
including for repeated headers, and operation/encoded-size limits apply both
incrementally and to the final concatenated sequence. An empty patch has
`replacement_body=None` and no header mutations.

```python
RequestPatch(
    header_mutations=(
        WriteHeaderMutation(
            name="x-openshell-middleware-policy-reviewed",
            value="true",
            on_existing=ExistingHeaderAction.OVERWRITE,
        ),
    ),
)
```

### `GateControl`

```python
class GateControl(StrEnum):
    PROCEED = "proceed"
    ALLOW = "allow"
    DENY = "deny"
```

### `GateEvaluation`

```python
class GateEvaluation(StrictDomainModel):
    control: GateControl
    patch: RequestPatch = RequestPatch()
    findings: tuple[Finding, ...] = ()
    reason_code: str | None = None

    @classmethod
    def proceed(
        cls,
        *,
        patch: RequestPatch | None = None,
        findings: tuple[Finding, ...] = (),
    ) -> Self: ...

    @classmethod
    def allow(
        cls,
        *,
        findings: tuple[Finding, ...] = (),
    ) -> Self: ...

    @classmethod
    def deny(
        cls,
        reason_code: ReasonCode,
        *,
        findings: tuple[Finding, ...] = (),
    ) -> Self: ...
```

`proceed` treats `patch=None` as an empty patch. These are the complete v0
construction helpers; callers needing no helper may instantiate the strict
model directly.

Required invariants include:

- `reason_code` is present if and only if control is `deny`; both `proceed` and
  `allow` require it to be `None`
- a deny must carry a stable reason code
- both terminal controls require an empty patch; only `proceed` can propose
  mutations
- a denied `EgressResult` contains no mutation, including mutations accumulated
  privately from earlier proceeding gates
- findings satisfy shared per-gate and final-result count and encoded-size
  limits
- strings crossing the result boundary are audit-safe and bounded
- mutations are validated before they become visible to a later gate
- a failed gate contributes no partial patch or findings

### General finding model

`Finding` is the extensible, audit-safe result vocabulary shared by built-in
and custom gates. It must be general enough to describe different classes of
observation without becoming an arbitrary payload channel.

Use one stable envelope:

```python
FindingCount = Annotated[int, Field(ge=1, le=(2**32 - 1))]

class Finding(StrictDomainModel):
    type: FindingType
    label: AuditSafeFindingLabel
    count: FindingCount = 1
    confidence: AuditSafeFindingValue | None = None
    severity: AuditSafeFindingValue | None = None
```

The fields have distinct roles:

- `type` is a stable machine-readable category owned by the gate
- `label` is a stable audit-safe identifier or concise display label within
  that category
- `count` aggregates equivalent observations
- `confidence` and `severity` are optional gate-defined scalar vocabularies

These are exactly the fields in the released OpenShell finding contract. Do
not add an internal attributes field that cannot be serialized faithfully.
When a custom gate needs a richer result, it should emit several stable finding
categories or use a separately designed offline/export surface rather than
encoding structured data into a label or result metadata.

`FindingCount` is an integer from 1 through the canonical OpenShell protobuf
maximum (`2**32 - 1` for the current `uint32` field). Aggregation uses checked
addition; overflow produces the same atomic stable limit result as count or
encoded-size exhaustion.

Example finding categories include:

| Gate | `type` | Example `label` | Optional scalar vocabulary |
| --- | --- | --- | --- |
| Regex privacy | `sensitive_entity` | `email` | `confidence=high` |
| Custom JSON validation | `body_schema_violation` | `required_field_missing` | `severity=error` |
| Custom gate | Custom stable type | Custom stable label | Gate-defined confidence/severity |

Finding values must not contain matched request text, body fragments, header or
query values, raw model output, arbitrary exception text, credentials, or
other request-derived content. A gate may use configured identifiers,
catalog-owned entity names, rule IDs, schema IDs, approved profile names, and
other stable values that satisfy shared bounds.

A gate needing to report several independent observations emits several
findings. A use case requiring structured, large, or content-bearing output
belongs in an explicitly designed opt-in export or evaluation surface, not in
middleware findings.

### Finding declarations

Each gate class declares the finding types it may emit:

```python
finding_types = (
    FindingTypeDefinition(
        type="body_schema_violation",
    ),
)
```

The declaration is part of gate discovery and contract validation. The public
gate wrapper rejects:

- undeclared finding types
- invalid identifiers
- values outside shared string bounds
- per-gate or per-request count and encoded-size limits
- finding content that violates structurally enforceable safety rules

Helper bases declare common finding definitions or provide concise helpers so a
simple custom gate does not need repetitive boilerplate. The CLI reports each
installed gate's possible finding types. Custom-gate contract tests verify
emitted findings against the declarations.

### Runtime-owned provenance and aggregation

A gate returns only its own `Finding` values. `RequestProcessor` wraps every
accepted finding with runtime-owned provenance before adding it to the final
result:

```python
class SourcedFinding(StrictDomainModel):
    source_gate: GateName
    finding: Finding
```

Gates cannot spoof another gate's source. The runtime aggregates only
findings with identical source, type, label, confidence, and severity.
Aggregation adds their counts and rechecks the request-wide bounds with checked
arithmetic.

Egress Gate is one OpenShell middleware result regardless of how many internal
gates ran. Set a fixed per-gate finding cap no larger than the canonical
OpenShell result cap, and cap the final serialized `EgressResult` at the
canonical one-result limit (currently 32 finding groups, not 32 per internal
gate). Enforce the final cap incrementally while aggregating. If several
individually valid gate evaluations exceed it together, return the stable
fail-closed limit result with no partial findings or mutations.

Findings remain observations, not hidden decision inputs. A gate's
`GateControl` decides whether processing proceeds, allows, or denies; the
presence or severity of a finding has no implicit runtime disposition.

### OpenShell finding contract

The released OpenShell `Finding` protobuf has `type`, `label`, `count`,
`confidence`, and `severity`. Those five fields are the complete v0 public
finding contract for built-in and custom gates.

`RequestProcessor` may retain `source_gate` around findings in its internal
`EgressResult` so traces, logs, and offline evaluation can attribute which gate
emitted an observation. The OpenShell adapter serializes only the nested
five-field `Finding`; per-gate provenance is not available on the wire. This is
an explicit platform limitation, not a reason to invent a compatibility
encoding.

Do not encode provenance or structured attributes into `label`, flatten them
into indexed `HttpRequestResult.metadata` keys, or serialize JSON into a string
field. `EgressResult.metadata` is runtime-owned and remains reserved for facts
about the complete pipeline evaluation. Gates cannot emit or overwrite
result-level metadata.

### `EgressResult`

The final domain result contains the terminal decision, decision source,
complete final patch relative to the original OpenShell evaluation when
allowed, accumulated internally sourced findings, stable reason code, bounded
runtime-owned metadata, policy fingerprint, and per-gate content-safe trace
data. A denied result always has an empty patch.

`DecisionSource` has a bounded kind—`gate`, `pipeline_default`, or
`runtime_limit`—plus optional `GateName` and `GateType` fields. Those fields are
required only for kind `gate` and forbidden for the other kinds. A
default-sourced decision has no terminal gate; default deny
uses the runtime-owned `egress_gate_default_deny` reason code, while default
allow has no deny reason code. A runtime-generated fail-closed limit denial has
no terminal gate, uses `runtime_limit`, and carries the appropriate stable
runtime limit code such as `egress_gate_limit_exceeded`. Logs and offline
evaluation use these fixed source values rather than misattributing either
outcome to the last proceeding gate.

Use this complete v0 deny-code ownership table:

| Denial source | Reason code |
| --- | --- |
| `regex-body` match | `egress_gate_regex_denied` |
| Custom gate | A validated gate- or policy-owned code |
| Pipeline default | `egress_gate_default_deny` |
| Runtime safety limit | `egress_gate_limit_exceeded` |

No other runtime-generated deny code is part of v0. Evaluation failures use
the service error mapping rather than inventing deny codes.

Each `GateTrace` records `GateName`, `GateType`, control result, duration,
finding count, and mutation kinds. `GateType` is the registry discriminator,
never the Python implementation class. The trace does not contain bodies,
header values, query values, matched text, model output, arbitrary exception
messages, or gate-defined free-form metadata.

## Gate extension system

The reframed product has three intentionally separate customization levels:

1. **Policy composition:** operators assemble installed gates without writing
   Python. This is how regex inspection and redaction deployments should be
   built.
2. **Gate authoring:** developers add one focused request behavior through a
   typed config and `Gate` implementation.
3. **Application assembly:** deployers register trusted gates and inject typed
   operational resources through one registry factory.

Additional transports are outside the extension model and outside this
project's scope. Gate authors target the stable protobuf-free representation
of OpenShell's supervisor-middleware request contract.

### Public gate contract

Replace `EntityProcessingEngine` as the primary extension contract with a
request-level `Gate` contract:

```python
GateConfigT = TypeVar("GateConfigT", bound=GateConfig)
GateResourcesT = TypeVar(
    "GateResourcesT",
    bound=GateResources | None,
    default=None,
)


class Gate(Generic[GateConfigT, GateResourcesT]):
    @final
    def __init__(
        self,
        config: GateConfigT,
        resources: GateResourcesT,
        *,
        timeout: Timeout | None = None,
    ) -> None: ...

    @classmethod
    def get_config_type(cls) -> type[GateConfig]: ...

    @classmethod
    def get_resources_type(cls) -> type[GateResources] | None: ...

    @property
    def config(self) -> GateConfigT: ...

    @property
    def resources(self) -> GateResourcesT: ...

    def _initialize(self, *, timeout: Timeout | None = None) -> None: ...

    @final
    def evaluate(
        self,
        request: HttpRequest,
        *,
        timeout: Timeout,
    ) -> GateEvaluation: ...

    def _evaluate(
        self,
        request: HttpRequest,
        *,
        timeout: Timeout,
    ) -> GateEvaluation: ...
```

As today, custom implementations do not define arbitrary constructors. The
base class owns construction, read-only config access, optional resource
validation, the public `evaluate` wrapper, input/result validation, and
content-safe error translation. Implementations provide a protected initialization hook for
derived reusable state and a protected `_evaluate` method. Construction
validates the exact config and resource types, stores them as read-only
properties, and calls `_initialize` exactly once with the preparation timeout.
The resulting instance must be safe for concurrent calls and is shared by
evaluations; the runtime does not claim Python-level deep immutability.
The wrapper rejects terminal evaluations with non-empty patches before they
reach the pipeline.

The generic arguments are the single source of truth for exact config and
resource types, preserving the former extension pattern. The resources type
defaults to `None`, so `Gate[KeywordDenyConfig]` is the complete declaration
for a resource-free gate. Base-owned read-only class methods infer the runtime
types; implementations do not repeat them as class attributes, and registry
validation rejects missing, unresolved, or invalid generic declarations.
Use `typing_extensions.TypeVar` for the defaulted type parameter on Python
3.11.

### Extension-author experience

The clean custom-gate path is a primary product surface. A resource-free gate
should normally require only:

1. one strict configuration model
2. one gate class
3. one compact capability declaration, or a helper base that supplies it
4. one protected `_evaluate` implementation
5. one registry call

For example, the documentation should be able to present a complete custom
gate with approximately this shape:

```python
class KeywordDenyConfig(GateConfig):
    gate: Literal["keyword-deny"]
    keyword: str
    reason_code: ReasonCode


class KeywordDenyGate(Gate[KeywordDenyConfig]):
    capabilities = GateCapabilities(reads_body=True, may_deny=True)

    def _evaluate(
        self,
        request: HttpRequest,
        *,
        timeout: Timeout,
    ) -> GateEvaluation:
        timeout.raise_if_expired()
        if self.config.keyword.encode() in request.body:
            return GateEvaluation.deny(self.config.reason_code)
        return GateEvaluation.proceed()
```

An extension author should not need to understand gRPC, protobufs, active-policy
replacement, server lifecycle, mutation serialization, or runtime concurrency.

Resource-free gates omit a resources generic parameter and resource-backed
gates add one typed `GateResources` model, matching the current extension
pattern. Export the complete supported authoring surface from
`egress_gate.gates` so examples do not import private modules.

Registration is equally direct: `registry.register(KeywordDenyGate)` for a
resource-free gate, and `registry.register(OrganizationGate,
resources=resources)` for a resource-backed gate. The registry, not policy
configuration, supplies the constructor argument; it passes `None` explicitly
for resource-free gates, while a missing or wrong resource for a
resource-backed gate is a registration error.

Document the gate contract and test the example custom gate directly. Add a
reusable external contract-test package only after a concrete second consumer
shows that the abstraction reduces duplication.

### Gate configuration

Every concrete gate declares an exact strict Pydantic config model with a
literal `gate` discriminator:

```python
class KeywordDenyConfig(GateConfig):
    gate: Literal["keyword-deny"]
    keyword: str
```

The application registry builds the discriminated union from exactly the
installed gate types. Unknown fields and unknown gates are rejected.

### Operational resources

Retain the existing distinction between policy behavior and deployment-owned
resources. Resource bundles contain concurrency-safe clients, approved
endpoints, or credential providers. They contain no per-request state and
cannot override policy configuration.

Prepared gates borrow these application-owned resources; policy replacement
never closes them. In v0, `_initialize` may create only immutable, ordinarily
garbage-collected derived state, not independently closable resources. During
shutdown the server stops admitting RPCs and waits for synchronous workers to
exit; only then may the assembling application close its resources. Egress
Gate itself never closes a borrowed resource.

A custom resource-backed gate may select an operator-approved resource profile,
but policy configuration may not provide arbitrary provider URLs, credentials,
Python imports, or client implementations.

### Capability declarations

Each gate type declares immutable capabilities used for validation and
discovery:

- reads target
- reads context
- reads headers
- reads body
- replaces body
- mutates headers
- produces findings
- may terminally allow
- may deny
- uses external resources

Read capabilities are declarative discovery and linting metadata because every
trusted gate receives the complete immutable `HttpRequest`; the wrapper cannot
prove which fields Python code inspected. Output capabilities—body replacement,
header mutation, finding production, terminal allow, and deny—are mechanically
enforced against each `GateEvaluation` at the public wrapper.

Capabilities are not a permission system for hostile Python code; registry
factories and custom gate modules remain trusted deployment code. Do not imply
that read declarations create field isolation.

Keep capability authoring compact. Helper bases should provide correct defaults
for common cases, and capabilities that can be derived unambiguously from a
base class or result type should not require repetitive declarations.

### Helper bases

Provide one narrow helper base for the built-in text-inspection pattern:

- `Utf8BodyGate`: strict UTF-8 decoding, unchanged-body checks, and bounded
  body replacement

The built-in regex implementation should use `Utf8BodyGate`. A custom privacy
gate should remain comparably compact.

## Registry and application assembly

Rename and generalize `EngineRegistry` to `GateRegistry`.

The registry must:

1. register concrete gate config, implementation, and optional resource types
2. reject duplicate discriminators and incomplete declarations
3. bind application-owned resource profiles
4. generate the exact pipeline configuration schema
5. validate gate capabilities and finding-type declarations
6. prepare reusable gate instances that satisfy the concurrent-call contract
7. expose content-safe gate and finding discovery information for the CLI
8. finalize exactly once before serving requests

Preserve `module:factory` application assembly through a renamed
`--registry-factory` option. The factory returns one finalized application
registry. Configuration cannot choose or import a factory.

## Pipeline configuration

Use one top-level schema:

```yaml
pipeline:
  gates:
    - name: stable-diagnostic-name
      config:
        gate: concrete-gate-discriminator
        # exact gate-specific fields
  default_decision: allow
```

Requirements:

- one through ten gates initially
- a required, explicit, unique `name` for every gate
- required `default_decision`
- exact strict gate configuration
- complete validation before preparation
- complete candidate preparation before active-policy publication
- no global `on_detection` concept
- no implicit action derived from findings
- no backwards-compatible acceptance of `entity_processing`

Gate configuration owns the relationship between its observations and its
control result. This permits the regex gate and installed custom gates to
detect, replace, allow, or deny as their explicit contracts define.

## Built-in gates

The default registry ships exactly one concrete gate: `regex-body`.
Organization-specific behavior may use the custom-gate registry surface.
Deterministic egress constraints belong in OpenShell policy. Semantic judgment
and other proposed built-ins are deferred; do not add another concrete gate or
example implementation in this refactor.

### Regex body gate

Port the hardened catalog validation, bounded compiled cache, timeout-capable
matching, overlap behavior, detections, and replacement implementation into a
request-level `regex-body` gate.

Proposed configuration:

```yaml
gate: regex-body
pattern_catalog: patterns.yaml
mode: replace  # detect | deny | replace
replacement:
  strategy: template
  template: "[{entity}]"
```

Behavior:

- `detect`: findings plus `proceed`, no mutation
- `deny`: deny on a match; otherwise proceed
- `replace`: replace the body, emit findings, and proceed

The gate reads the current body as strict UTF-8. Later gates see the replaced
body. Preserve the current safety bounds and atomic failure behavior, but adopt
new names and APIs without aliases.

## Reference compositions

### Regex redaction composition

Ship a runnable example and complete documentation for:

```yaml
pipeline:
  gates:
    - name: identifiers
      config:
        gate: regex-body
        pattern_catalog:
          entities:
            - name: email
              rules:
                - pattern: '(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![\w.-])'
                  confidence: high
        mode: replace
        replacement:
          strategy: template
          template: "[{entity}]"
  default_decision: allow
```

Provide equally concise detect and deny variants. This example is the
acceptance baseline for preserving regex-body usability and customization.

## Active policy preparation and replacement

One running Egress Gate service owns exactly one active prepared policy at a
time. The application registry, injected resources, executor, and server live
for the process lifetime. The active policy contains only validated
configuration and its prepared processor. The processor carries a fingerprint
for diagnostics and offline comparison; the active-policy holder uses exact
validated-config equality for reuse.

OpenShell's request configuration remains authoritative. The first evaluation
prepares and publishes its policy lazily. Later evaluations with an equal
validated configuration reuse the prepared pipeline. An evaluation carrying a
different valid configuration is a policy update, not a second cache entry.

### Policy update behavior

1. Validate the candidate policy completely.
2. Serialize active-config comparison and candidate preparation under one
   replacement lock.
3. If the validated config equals the active config, reuse the prepared
   pipeline.
4. Otherwise prepare the candidate while retaining the lock, so concurrent
   requests for the same successful update reuse the published result.
5. Fail with the defined runtime-limit result if the deadline expires while
   waiting for the lock; after acquiring it, check the deadline again.
6. Prepare every configured gate with the remaining request deadline.
7. Recheck cancellation and the deadline immediately before publication. An
   expired or cancelled evaluation cannot publish its candidate.
8. Publish the complete candidate config and processor atomically.
9. Let evaluations that already captured the previous prepared processor
   finish on it; do not mutate or prematurely close it.
10. If validation or preparation fails, leave the previous policy active and
   fail the request carrying the candidate. Never evaluate that request using
   the previous policy as a fallback.

Updating the Egress Gate policy therefore requires no service restart: validate
the candidate with `egress-gate validate --policy`, update the OpenShell policy
configuration, and let the next evaluation activate it. Do not add a file
watcher, admin RPC, reload command, or multi-entry cache initially.

Keep the single active-pipeline holder as a small private service-lifecycle
component in `service/servicer.py`, following the current `_ActivePolicy`
ownership. Do not create `policy_cache.py` or `active_policy.py` initially.
Extract it later only if concrete service complexity makes that module boundary
clearer.

Policy rollout is deliberately an operator-side serialized operation. Before
allowing requests with a new configuration to reach the service, the deployer
must stop admitting requests with the old configuration and allow already
admitted old requests to resolve which pipeline they will use. Those in-flight
evaluations may then complete on the old prepared processor while the new one
becomes active. After cutover, the old configuration must not be sent again.

The service does not add policy revisions or an administrative control plane to
enforce that rollout convention. If callers interleave unrelated distinct
configurations, whichever valid candidate wins the replacement lock becomes
active; that usage is unsupported. A deployment needing genuinely concurrent
policies must run separate Egress Gate instances. Failed candidates leave the
current policy active and may be retried by a later request; v0 does not cache
preparation failures. The active-policy holder never retains request content,
findings, patches, or results.

## Runtime execution

For each request:

1. Create one monotonic overall deadline at service entry.
2. Validate the OpenShell evaluation, request, and policy bounds against that
   deadline.
3. Convert the validated protobuf request to immutable domain models.
4. Acquire a bounded processing slot with the remaining time.
5. Resolve or prepare and, if changed, atomically publish the immutable
   pipeline with the remaining time.
6. Invoke gates in order with the current request.
7. Validate each complete gate evaluation.
8. Apply a proceeding gate's patch atomically to a new current request.
9. Accumulate bounded findings and trace records.
10. Stop on allow or deny; both terminal controls must have an empty patch.
11. Apply the required default decision if every gate proceeds.
12. For an allow, calculate one final body/header patch relative to the original
    request. For a deny, discard all privately accumulated mutations and emit
    an empty patch.
13. Serialize the egress result through the OpenShell service boundary.
14. Emit the content-safe operational log after the result is fixed.

Preserve the existing rule that cancelled RPCs do not release worker capacity
until synchronous gate execution actually exits. Gates must propagate the
remaining timeout to interruptible collaborators.

## Observability and audit

### Default operational logs

Include:

- request ID
- decision
- `decision_source_kind` (`gate`, `pipeline_default`, or `runtime_limit`)
- total duration
- finding count
- stable error code

Exclude:

- request and replacement bodies
- matched values, offsets, patterns, and surrounding text
- header values
- raw paths and queries by default
- credentials and model endpoints
- natural-language policies
- arbitrary exception messages

### Initial observability boundary

Keep v1 observability in the existing `logging.py`, `RequestProcessor`, and
service ownership points. Do not introduce a public observer interface,
`DecisionEvent`, metrics sink abstraction, or metrics-backend dependency until
a concrete second consumer requires one.

The compact structured operational log fields above are sufficient for initial
diagnostics without adding cross-layer outcome plumbing solely for telemetry.
Detailed traces, policy fingerprints, terminal gate provenance, mutation
summaries, and policy-replacement outcomes remain available in domain results
or focused diagnostics and can be promoted into service logs after a concrete
operational need. Any later metrics integration
must preserve bounded cardinality and must not derive labels from request
paths, queries, header values, bodies, sandbox IDs, request IDs, policy text,
or user-provided gate names. Persistent audit storage, dashboards, alerting,
and export integrations remain outside the initial core.

## Shadow operation and offline evaluation

### Observation-only gates

Custom gates can support a specifically documented observation-only mode that
records a proposed decision as a finding and proceeds. The final policy
decision remains determined by enforcing gates and the pipeline default.

Do not add a whole-pipeline mode that catches failures and allows traffic.

### Evaluation corpus

Define a versioned YAML or JSONL evaluation-case format containing:

- case name and tags
- bounded synthetic or deliberately captured request facts
- expected allow or deny result
- optional expected `decision_source_kind`, optional expected gate name and
  type when the kind is `gate`, and expected finding types
- explicit redaction/capture provenance metadata

Add:

```text
egress-gate evaluate --policy POLICY --cases CASES
```

The command uses the same registry, preparation, runtime, limits, and decision
logic as the service. It produces aggregate agreement and per-case differences
without sending requests upstream.

Traffic-derived policy suggestions and persistent historical replay may be
added later through an explicitly designed export integration. Generated
suggestions must always remain drafts requiring explicit human publication.

## CLI surface

Replace the current commands rather than aliasing them.

Initial CLI:

```text
egress-gate gates
egress-gate configuration-schema
egress-gate validate --policy POLICY
egress-gate evaluate --policy POLICY --cases CASES
egress-gate add-gateway-registration ...
egress-gate remove-gateway-registration ...
egress-gate serve ...
```

All inspection, validation, evaluation, and serving commands accept the same
optional trusted `--registry-factory module:factory` argument.

`gates` reports `GateType` discriminator, description, capabilities, resource
type, declared finding types, and configuration schema
reference without importing service code.

`validate` performs pure policy-model and registered-resource validation but
never constructs gates, loads preparation-time artifacts, compiles derived
state, prepares a pipeline, or changes the active policy. Preparation-only
failures remain visible through `evaluate` and the service. The CLI validates
the protobuf-free policy domain; exact encoded OpenShell `Struct` size remains
a service-boundary check.

Gateway registration uses the new service and registration names and retains
safe atomic TOML updates. It removes only the registration explicitly named by
the operator.

## Proposed project layout

```text
projects/egress-gate/
├── AGENTS.md
├── README.md
├── pyproject.toml
├── uv.lock
├── proto/
├── src/egress_gate/
│   ├── __init__.py
│   ├── base.py
│   ├── cli.py
│   ├── config.py
│   ├── constants.py
│   ├── errors.py
│   ├── gateway_config.py
│   ├── logging.py
│   ├── request.py
│   ├── request_processor.py
│   ├── result.py
│   ├── string_validators.py
│   ├── timeout.py
│   ├── bindings/
│   ├── gates/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── registry.py
│   │   └── regex_body.py
│   └── service/
│       ├── __init__.py
│       ├── server.py
│       └── servicer.py
├── tests/
├── examples/
│   └── regex-redaction/
├── docs/
└── analysis/
```

This deliberately follows the current project's flat layout. `gates/` owns a
real implementation family and `service/` owns the OpenShell gRPC/protobuf
boundary; most other concepts remain focused modules at package root. Add
another directory only when several modules form a cohesive family with a
clear import boundary.

Retain a current module when it still owns the corresponding generalized
behavior. Rename, split, or remove one only for a concrete ownership reason,
not to make the refactor look architecturally different.

### Net-new source files

Relative to the superseded implementation, only two handwritten source files
are genuinely new:

| File | Why it is separate |
| --- | --- |
| `request.py` | Owns the immutable request, header, and patch vocabulary shared by every gate and the service boundary. Keeping it out of `request_processor.py` prevents extension authors from importing orchestration internals. |
| `result.py` | Owns gate evaluations, findings, provenance, traces, and final results. These contracts are consumed by gates, the processor, CLI evaluation, and the service, so none of those existing modules is an appropriate sole owner. |

Everything else maps to an existing file or directory: `engines/` becomes
`gates/`, `engines/regex.py` becomes `gates/regex_body.py`, and the existing
configuration, processor, service, CLI, logging, validation, timeout, and
gateway-registration modules are generalized in place. Do not add observer,
metrics, policy-cache, active-policy, semantic-client, or transport modules in
v0; the plan deliberately assigns those responsibilities to existing owners
or keeps them outside the product.

## Repository-wide rename and deletion scope

Because compatibility is irrelevant, perform a complete replacement:

- replace the superseded project with `projects/egress-gate/`
- rename the Python package, distribution, CLI, logger hierarchy, service
  identity, manifest, examples, analysis assets, and test imports
- replace the superseded project workflow
- replace the documentation staging script and its tests
- replace the generated documentation route and `zensical.toml` navigation
- update `projects/README.md` and repository-level documentation references
- update Dev Notes only through the documented renderer if any relevant notes
  are intentionally changed
- delete superseded configuration examples and architecture text
- delete tests whose only purpose is asserting removed names or schemas
- add new tests for the replacement behavior; do not mechanically rename tests
  that encode obsolete concepts
- leave no superseded imports, console scripts, service registrations, or
  compatibility modules in the final tree
- leave no names or branding from external comparison projects in
  `projects/egress-gate/` implementation or documentation artifacts

The superseded product identity must not remain in current project artifacts.

## Implementation sequence

### Protocol boundary

Implement against the unmodified manifest-pinned OpenShell v0.0.97 middleware
contract. Core OpenShell protocol changes are out of scope. In particular, the
wire finding contract has only `type`, `label`, `count`, `confidence`, and `severity`;
runtime-owned gate provenance remains internal to Egress Gate and is omitted
when serializing findings.

Implement in reviewable phases. Each phase should leave the project internally
coherent; compatibility with the previous phase is not required across merged
redesign PRs if the branch is explicitly managed as a coordinated v0 rewrite.

### Phase 1: Establish the new identity and domain contract

1. Move the project and rename packaging, imports, CLI, workflow, registration,
   and documentation staging surfaces.
2. Replace project and nested agent instructions with the new boundaries.
3. Retain the copied protocol and generated bindings from manifest-pinned
   OpenShell v0.0.97; do not change the core contract.
4. Add immutable domain request, target, context, header, patch, finding,
   internally-sourced-finding, finding-definition, gate-evaluation, gate-trace, and
   egress-result models.
5. Define separate stable error and deny-reason catalogs. Give runtime-owned
   wire reason codes explicit `egress_gate_` names, define built-in gate codes
   where their behavior requires them, and validate custom or policy-owned
   codes against the OpenShell format and size constraints without rewriting or
   automatically prefixing them.
6. Add model invariant and exact-boundary tests.

Acceptance criteria:

- the distribution installs as `egress-gate`
- `import egress_gate` and the new CLI work
- no superseded Python import or executable remains
- core domain modules do not import gRPC
- the copied OpenShell protocol is generated from and records the selected
  released OpenShell contract version
- the domain and adapter expose exactly the five canonical wire finding fields
  without label or result-metadata encoding
- model tests cover empty bodies, repeated headers, body replacement with
  empty bytes, invalid deny results, oversized findings, terminal evaluations
  carrying patches, denied results carrying mutations, non-deny evaluations
  carrying reason codes, and invalid patches

### Phase 2: Build the gate registry and pipeline runtime

1. Implement `GateConfig`, `GateResources`, `Gate`, helper bases, capability
   declarations, and the finalized `GateRegistry`.
2. Build the exact discriminated-union pipeline schema from registered gates.
3. Add gate finding-type declarations to registry validation and discovery.
4. Implement gate preparation and reusable concurrent-call-safe instances.
5. Port enough of the current regex catalog, compilation, matching,
   replacement, and safety behavior to register a working `regex-body` gate.
6. Replace the superseded top-level config with the strict pipeline config and rewrite
   `RequestProcessor` around `HttpRequest`, configured gates, and
   `EgressResult`. Keep the processor module and public-wrapper style because
   they still own the same generalized orchestration behavior.
7. Implement sequential execution, the overall shared deadline,
   gate-evaluation validation, patch application, terminal decisions, default
   decision provenance, aggregation, and content-safe traces.
8. Add custom-gate contract fixtures demonstrating resource-free and
   resource-backed extensions.
9. Remove the entity-only public engine API only after `regex-body` and the
   gate authoring surface replace it; do not wrap or alias it.

Acceptance criteria:

- a third-party gate can be registered without changing core source
- the documented resource-free custom gate, config, and registration remain a
  compact example with no service or runtime-internal imports
- a resource-backed gate adds only its typed resource model and factory-owned
  resource value to the same pattern
- generated schema contains only installed gates and exact config types
- gate discovery reports declared finding types
- undeclared finding types are rejected by the gate wrapper
- `regex-body` is runnable through the new pipeline before the old engine API
  is removed
- later gates see earlier validated mutations
- terminal decisions skip later gates
- default decision is required and tested
- default-sourced decisions are attributed to `pipeline_default`
- runtime-generated fail-closed limit denials are attributed to `runtime_limit`
- preparation and processing-slot wait consume the same overall deadline as
  gate execution
- several individually valid gate outputs cannot exceed the final OpenShell
  finding limit without a stable atomic limit result
- gate failure returns no partial patch or findings
- output-capability violations fail at the gate wrapper boundary; read
  capabilities remain explicitly declarative

### Phase 3: Complete the regex redaction composition and coverage

1. Complete detect, deny, and replace behavior and the remaining regex safety
   coverage on the Phase 2 `regex-body` gate.
2. Rewrite the gate-authoring documentation snippet and reusable contract-test
   fixture against `Gate` and `Utf8BodyGate`. Do not add a runnable custom-gate
   implementation without a concrete consumer.
3. Add the regex redaction example.
4. Re-run and adapt relevant performance analysis against the new runtime.

Acceptance criteria:

- the regex redaction example remains concise
- all current regex safety properties remain covered by new behavior-focused
  tests
- detect leaves bytes unchanged, deny returns no partial mutation, and replace
  is visible to later gates
- logs and findings remain content-safe by default

### Phase 4: Harden active policy reuse and replacement

1. Implement deterministic policy fingerprinting for diagnostics and results.
2. Implement one active prepared pipeline and a serialized candidate-replacement
   path.
3. Reuse the active pipeline for equal validated configs and coordinate
   concurrent requests carrying the same successful candidate update.
4. Publish successful candidates atomically while preserving in-flight
   references to the prior prepared processor.
5. Leave the active pipeline unchanged after validation or preparation failure.
6. Make replacement-lock waits and final publication checks consume the
   request's overall deadline.
7. Document the serialized single-policy rollout precondition and the point at
   which admitted old requests must have resolved their pipeline.

Acceptance criteria:

- repeated requests with the active policy reuse the same prepared gates
- a valid changed policy becomes active without restarting the service
- in-flight evaluations may finish on the prior prepared processor during an
  update
- failed candidate preparation is never published and never falls back to the
  previous policy for the candidate request
- concurrent requests for the same successfully prepared candidate reuse the
  published pipeline; failed candidates may be retried without a failure cache
- an expired or cancelled request cannot publish a candidate
- tests cover the documented old-request resolution boundary before new-policy
  admission
- unrelated concurrent policies are documented as requiring separate service
  instances
- no request content is retained between evaluations

### Phase 5: Complete the OpenShell middleware service

1. Convert every bounded OpenShell request field into the new domain model.
2. Serialize final body and header mutations relative to the original request.
3. Serialize the five canonical finding fields through the OpenShell protocol;
   keep runtime-owned gate provenance internal.
4. Preserve strict phase, message, configuration, target, header, and body
   bounds.
5. Map validation, internal, limit, allow, and deny outcomes explicitly.
6. Update server concurrency and shutdown behavior around the new runtime.
7. Update gateway registration management for the new identity.

Acceptance criteria:

- no handwritten runtime or gate module imports protobuf or gRPC
- provider credentials remain absent from gate input
- mutation output obeys OpenShell's protected-header rules
- each serialized finding preserves type, label, count, confidence, and
  severity; tests confirm internal source is intentionally omitted
- RPC cancellation and worker-slot behavior are tested
- integration tests cover active-policy reuse, successful and failed policy
  replacement, in-flight replacement, body mutation, header mutation, terminal
  allow, deny, default decision, failure, aggregate multi-gate finding overflow,
  exact size limits, ordered repeated-header mutations, and every row of the
  normative outcome mapping

### Phase 6: Add offline evaluation tooling

1. Implement the versioned evaluation corpus and `evaluate` CLI.
2. Exercise the regex-redaction example through the production registry and
   processor.
3. Keep semantic judgment and additional runnable custom gate examples out of
   this phase.

Acceptance criteria:

- the installed built-in set remains exactly `regex-body`
- no semantic-specific client, config, implementation, policy, corpus, or test
  exists in the project
- no vendor SDK is required by the package or default installation
- evaluation uses the production runtime rather than a parallel reimplementation

### Phase 7: Observability, documentation, and cleanup

1. Complete content-safe structured operational logging through the existing
   logging, processor, and service ownership points.
2. Rewrite canonical project documentation around Egress Gate.
3. Document gate authoring, resource injection, policy composition, regex
   inspection and redaction, active-policy replacement, operations, limits,
   and failures. State explicitly that deterministic egress constraints belong
   in OpenShell policy.
4. Replace examples and documentation mirrors through the documented staging
   workflow.
5. Remove obsolete predecessor assets, analyses, scripts, tests, navigation,
   and workflow references.
6. Search the complete repository for stale code identifiers and user-facing
   claims. In `projects/egress-gate/`, audit case-insensitive `stage`
   occurrences and allowlist only genuinely distinct external OpenShell/build
   terminology or historical quotations; no code, configuration, log, metric,
   test, or current documentation may use it as a synonym for gate.

Acceptance criteria:

- the public documentation leads with modular policy composition
- regex redaction is the first-party quickstart and custom gates remain a
  first-class extension surface
- no default log or finding contains request content
- repository search finds no obsolete compatibility surface
- the scoped `stage` audit finds no remaining gate synonyms
- project checks and repository documentation checks pass

## Test strategy

### Unit tests

- immutable domain-model invariants
- gate wrapper, declarative read capabilities, and output-capability enforcement
- registry finalization and exact schema generation
- request patch application
- the complete control/patch matrix, denied-result mutation suppression,
  gate, pipeline-default, and runtime-limit decision provenance, and default
  behavior
- timeout across preparation, slot acquisition, execution, and atomic failure
  behavior
- finding declarations, internal provenance, aggregation, and limits
- exact-maximum finding counts and checked multi-gate count overflow
- trace, result metadata, and mutation limits
- regex-body behavior and adversarial patterns
- request-rule normalization and matching
- policy fingerprinting, active-policy reuse, and atomic replacement

### Contract tests for extensions

Test representative custom gate classes inside the core gate and registry test
suites for concurrent-call safety, content-safe errors, deadline handling,
declared capabilities and finding types, and invalid output rejection. Do not
add a separate runnable custom integration or public testing framework without
a concrete consumer.

### Service integration tests

Exercise real gRPC requests through the OpenShell middleware service with:

- empty and binary bodies
- repeated headers
- every target and process field
- active-policy reuse and atomic replacement while an old evaluation is in
  flight
- sequential mutations
- terminal allow and deny
- default allow and deny
- invalid config and preparation failure
- gate timeout and resource limit with `runtime_limit` provenance
- maximum and over-maximum protobuf message and result values
- several individually valid gate findings exceeding the final result cap
- `runtime_limit` provenance for timeout, encoded-size exhaustion, finding-group
  overflow, and checked finding-count overflow
- canonical five-field finding serialization and intentional omission of
  internal gate provenance
- fail-closed OpenShell policy configuration in runnable examples

### Security-focused tests

- mutation of protected headers
- findings or errors attempting to contain request content
- undeclared finding types and invalid finding scalar values
- attempts to spoof runtime-owned finding provenance
- failed or concurrent policy replacement without cross-policy state leakage

### Performance tests

Measure separately:

- fixed runtime overhead with one minimal benchmark-only no-op gate, preserving
  the production requirement that every pipeline contains at least one gate
- regex-body latency by body size and rule count
- pipeline overhead by gate count
- active-policy reuse and replacement-preparation latency
- concurrency saturation

Set budgets only after measuring the new architecture. Do not preserve current
numbers as compatibility requirements, but prevent regressions within the new
design once baselines are established.

## Validation commands

During implementation, use focused tests for the owning layer. Before each
phase handoff, run from `projects/egress-gate/`:

```bash
make check
make check-py311
```

For repository documentation changes, follow `docs/development/index.md` and
run from the repository root:

```bash
python3 tests/test_render_dev_notes.py
scripts/build-docs.sh
```

Serve and inspect the generated site as documented whenever navigation,
staging, diagrams, or public documentation changes.

## Documentation deliverables

The final documentation set should include:

- product overview and explicit boundary
- OpenShell request-path quickstart
- regex redaction quickstart
- full pipeline configuration reference
- built-in regex-body gate reference
- custom gate and resource authoring guide
- custom finding types, internal provenance, and safety guide
- runtime architecture and request lifecycle
- active-policy preparation and replacement lifecycle
- limits, failures, cancellation, and concurrency
- content-safe structured logging behavior
- offline evaluation guide
- OpenShell ownership boundaries and explicit proxy/non-OpenShell non-goals

Architecture diagrams should show the OpenShell gRPC service boundary around
the request processor and make the current-request mutation flow between gates
explicit.

## Risks and mitigations

### A generic gate API becomes an unbounded middleware hook

Mitigation: keep immutable typed input/output, exact config models, declared
capabilities, base-owned construction, validated patches, bounded results, and
trusted registry factories. Do not expose sockets, gRPC objects, or mutable
request instances.

### Early allow rules bypass important later gates

Mitigation: make allow terminal and visibly named, document it at every request
rules example, emit the terminal gate in traces, and test that later gates are
skipped. Do not add a separate policy linter in v0.

### General request handling weakens regex-body guarantees

Mitigation: keep the regex-body gate's full accepted-body processing, strict
UTF-8 contract, atomic replacement, deadlines, and resource limits. Binary
support in the request model must not imply partial inspection by a text gate.

### Policy replacement races activate the wrong configuration

Mitigation: support one active policy, serialize candidate preparation, recheck
validated-config equality under the replacement lock, publish only complete
prepared processors, let existing references finish, and never fall back to the
old policy for a request carrying a failed candidate. Document that unrelated
concurrent policies require separate service instances.

### Python throughput is insufficient for an all-request gate

Mitigation: benchmark empty and deterministic paths early, avoid unnecessary
body copies, keep rule matching local and bounded, isolate synchronous work in
the existing executor model, and reconsider implementation strategy only from
measured evidence. Do not introduce proxy implementation concerns into the
middleware domain model.

### The reframed name implies response or network-firewall guarantees

Mitigation: consistently say “pre-credentials sandbox HTTP egress middleware,”
document that OpenShell owns routing and network enforcement, and state that
responses and non-HTTP traffic are outside the v1 boundary.

## Definition of done

The refactor is complete when:

1. The repository contains one `egress-gate` project and no predecessor
   compatibility surface.
2. Custom request gates and resources are first-class, typed, discoverable,
   schema-generating, bounded, protobuf-free, and explicitly aligned with the
   OpenShell supervisor-middleware request contract.
3. Policy composition, gate authoring, and application assembly are documented
   as separate clean extension levels with runnable built-in compositions and
   core contract-test coverage.
4. Built-in and custom gates can emit declared bounded findings using the five
   canonical OpenShell fields; runtime-owned gate provenance is retained for
   internal traces and offline evaluation and intentionally omitted on the
   wire.
5. The default registry contains exactly the `regex-body` built-in.
6. The regex redaction composition detects, denies, and replaces with
   the current hardened regex behavior.
7. Deterministic egress constraints are documented as OpenShell policy's
   responsibility rather than duplicated in Egress Gate.
8. One active policy is reused across requests and can be replaced atomically
   without restarting the service or disrupting in-flight evaluations.
9. Allow, deny, proceed, mutation, failure, and default behavior are explicit
   and exhaustively tested.
10. Default logs, findings, and traces remain content-safe.
11. Offline evaluation uses the production registry and runtime.
12. The OpenShell middleware service passes all exact-boundary and concurrency
    tests.
13. Project and repository documentation validation passes.
14. Public documentation presents regex redaction as the built-in composition,
    the generic custom-gate surface as the extension path, and does not imply
    that deterministic request rules or semantic judgment are implemented.
15. No standalone proxy or non-OpenShell transport exists or is implied to be
    part of this project.
