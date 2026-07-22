# Privacy Guard

An OpenShell supervisor middleware. The supervisor calls it over gRPC (the
`SupervisorMiddleware` service in `bindings/`) to inspect provider-bound HTTP
requests *before* credentials are attached. Privacy Guard parses the request
body, scans its text for sensitive values, and returns an allow/deny decision
plus an optionally rewritten body that the supervisor forwards instead of the
original.

> Status: **hardened research project with a configurable regex scanner.** Strict
> policy and scanner configuration, request-level processing, bounded scanning,
> the generic JSON handler, safe gRPC adaptation, and a loopback server are
> implemented.

The self-contained [email scanner example](examples/email-scanner/README.md)
provides a deterministic scanner, middleware entry point, gateway registration,
sandbox policy, and manual Claude Code workflow.

## Request flow

`RequestProcessor` orchestrates one complete request:

```
proto HttpRequestEvaluation
  -> payloads.InterceptedRequest         proto-free capture of the request
  -> request_body.FormatHandler.normalize()
                                       -> request_body.RequestBody (TextBlocks)
  -> scanners.Scanner.scan(text_block)   -> scanners.Finding[]
  -> policy via config.PolicyConfig      -> per-block replacements
  -> request_body.FormatHandler.reconstruct()
                                       -> rewritten body (bytes)
  -> payloads.ProcessingResult           decision + replacement + findings
  -> proto HttpRequestResult
```

The servicer is the only seam that touches both the proto messages and the
domain types: it translates proto -> domain on the way in and domain -> proto on
the way out. Everything below the service layer is free of `bindings/` and
gRPC. OpenShell applies an allowed mutation and forwards the provider request;
Privacy Guard does not make the provider call itself.

Scanners continue to operate on exactly one text block. Scanner calls run on a
dedicated four-thread executor rather than the gRPC event loop; scanners must be
thread-safe and must not retain request content. Format handlers decide
which text blocks are relevant and own their addressing scheme. The processor
scans every emitted text block but treats each block path as opaque, so adding a
new request format does not require provider-specific processor logic.

## Scanner and JSON policy

Findings carry scanner-owned `low`, `medium`, or `high` confidence. Each action
selects the findings it applies to by `entity_types` and `minimum_confidence`:

```json
{
  "body_format": "json",
  "on_finding": {
    "action": "redact",
    "entity_types": ["email"],
    "minimum_confidence": "high",
    "template": "[{entity}]"
  }
}
```

`PolicyConfig.on_finding` is a Pydantic discriminated union keyed by `action`.
Observe, block, and redact each carry their own finding criteria; only the
redact variant has a `template`. `entity_types: null` selects every emitted
entity type, while an empty list selects none. `minimum_confidence: null`
accepts every confidence level.

Scanner configuration is independent of policy. A concrete `ScannerConfig`
subtype controls what and how its scanner detects; scanners can run without a
`PolicyConfig`. Action criteria filter the resulting `Finding` values for one
policy evaluation and never reconfigure the scanner.

Every scanner config declares its complete `entity_types` catalog. When an
action selects entity types, every configured name must occur in the union of
the active scanner catalogs. Unknown names fail config validation instead of
silently producing no findings.

Every JSON string value and every object key is scanned; JSON numbers, booleans,
and nulls are not. Keys are observable in `observe`, cause normal denial in
`block`, and cause a stable deny in `redact` because collision-safe key mutation
is not supported. Value findings may overlap for observe/block. Redaction picks
winners deterministically by confidence, span length, offsets, scanner identity,
and entity. A scanner sequence is passed to `RequestProcessor`; scanner names
must be unique and remain visible in aggregated findings.

`RegexScanner` is the packaged command's default implementation. Every scanner
requires `--scanner-config` on the standard command surface; the active scanner
owns that file's schema and interpretation. For `RegexScanner`, the path selects
its YAML catalog. Single-profile files contain a non-empty entity list;
multi-profile files contain only a non-empty `profiles` mapping and require the
scanner-specific `--profile` option after `--`. The separator is unnecessary
when no scanner-specific options are supplied. Use `privacy-guard --help` for
standard options and `privacy-guard --scanner-config PATH -- --help` for regex
scanner options. See [examples/regex-configs](examples/regex-configs) for both
configuration forms.

```bash
uv run privacy-guard \
  --scanner-config examples/regex-configs/customer.yaml \
  --listen 127.0.0.1:50051
```

Each entity has a unique name and a non-empty `patterns` list. Patterns declare
`name`, `regex`, `confidence`, and optional `ignore_case`, `multiline`,
`dot_all`, and `ascii` booleans. Every match carries its configured pattern name
in general finding metadata and is reported as `entity/pattern-name` by the
service. Policy filtering remains at entity level.

A scanner is a nominal extension: declare its strict configuration type and
implement `_scan`. Scanners that need derived, reusable state may also override
`_initialize`; the base constructor calls it after validating and retaining the
configuration. The public `scan` wrapper validates the returned tuple and each
`Finding`.

```python
from privacy_guard.scanners import Finding, ScanBudget, Scanner, ScannerConfig


class ExampleScanner(Scanner[ScannerConfig]):
    def _scan(self, text_block: str, budget: ScanBudget) -> tuple[Finding, ...]:
        return ()
```

Applications construct the processor with a sequence, for example
`RequestProcessor([ExampleScanner(ScannerConfig(name="example", entity_types=frozenset()))])`.
The base
constructor infers and validates the scanner's declared config type. A scanner
returns block-relative
`Finding` values; the processor composes each one into a `RequestBodyFinding`
with the owning `TextBlock.path`.

Applications serving a custom scanner can use the high-level server API, which
owns processor, middleware, gRPC, and shutdown wiring:

```python
from privacy_guard.service import MiddlewareServer

scanner = ExampleScanner(ScannerConfig(name="example", entity_types=frozenset()))
server = MiddlewareServer(scanner=scanner)
server.serve()  # Defaults to 127.0.0.1:50051
```

## Resource and failure behavior

The service enforces the protocol's 4 MiB input and replacement-body maximum,
32 finding groups, and exact 4 KiB encoded limit for each aggregate finding.
The operator may configure a lower effective maximum; this project cannot observe
that value, so deployments must keep the registration aligned with the 4 MiB
manifest or add the lower limit to service configuration.

JSON parsing is additionally bounded to 64 nesting levels, 4,096 text blocks,
and 4 MiB of scanned characters. Scanning is capped at 256 findings per block,
4,096 per request, four active scanner workers, and 16 concurrent gRPC calls.
One default one-second monotonic scan budget is shared across every block and
scanner in a request. `RegexScanner` checks it before and after each expression
evaluation and while consuming overlapping matches. Because the standard-library
regex engine cannot interrupt an active evaluation, one backtracking-heavy
expression may outlast the deadline. Test catalogs against representative
worst-case inputs before deployment and avoid expressions with pathological
backtracking behavior.
Shape excess is invalid input. Finding or outbound representation excess returns
a stable `privacy_guard_limit_exceeded` deny with no body or partial findings,
avoiding a failure-mode-dependent fail open.
Redacted text is projected against the body budget before it is rendered, which
bounds template and finding amplification. The authoritative serialized-body
limit is checked immediately after format reconstruction and again at the gRPC
boundary.

Operational logs contain request ID, duration, action, finding count, and safe
error code only—never bodies, text blocks, or matches. A cancelled RPC does not
release its scanner slot until its synchronous worker really exits.

## Module map

| Module | Responsibility |
| --- | --- |
| `config` | Strict Pydantic `PolicyConfig` parsing at the untrusted config boundary |
| `constants` | Package-wide limits, service metadata, and stable protocol values |
| `errors` | Closed, content-safe error catalog shared by all components |
| `payloads` | Frozen `InterceptedRequest` and `ProcessingResult` domain records |
| `request_body` | Nominal `FormatHandler` ABC + `JsonHandler`; strict `RequestBody`, `TextBlock` models |
| `scanners` | `Scanner` ABC + strict `ScannerConfig`, `Finding`, and `RequestBodyFinding` models |
| `processor` | Proto-free request orchestration and policy application |
| `service` | High-level `MiddlewareServer`, gRPC lifecycle, and servicer adapter |
| `bindings` | generated protobuf stubs — do not edit by hand |

## Notes for implementers

- **Scanner metadata.** Applications pass an explicit `ScannerConfig` subtype to
  the scanner constructor; `Scanner` infers and validates the declared generic
  config type. The read-only `config` property preserves that concrete type, and
  `supported_entity_types` returns its required `entity_types` catalog.
- **Finding types.** A scanner returns strict block-relative `scanners.Finding`
  values. The processor creates `RequestBodyFinding` values with the owning text-block
  path, and the servicer aggregates those into the protocol's count-based `Finding`.
  A finding may include an immutable, bounded string metadata mapping for
  scanner-specific attribution. Regex findings use its `pattern_name` key.
- **Scanner initialization.** Override `_initialize` only when validated config
  must be compiled or transformed into reusable immutable scanner state. The
  default hook does nothing.
- **Scan budgets.** The protected scanner hook receives a request-scoped
  `ScanBudget`. Standalone `scan` calls create a safe default when no budget is
  supplied. Potentially unbounded scanners must cooperate with the deadline.
- **Text-block paths.** `Finding` has no path state. A `Scanner` sees only one text
  block; the processor attaches `TextBlock.path` by creating `RequestBodyFinding`.
- **Format selection.** `PolicyConfig.body_format` (default `json`) picks a handler
  from the `format_handlers` mapping supplied to `RequestProcessor`. JSON is the
  only built-in format; applications can supply mappings containing other formats.
  Do not infer a provider from the request body or headers.
- **Format handlers.** A custom handler subclasses `FormatHandler`, calls
  `super().__init__(format_name="...")`, and implements the protected
  `_normalize` and `_reconstruct` hooks with `@override`. Return normally
  constructed strict `RequestBody` and `TextBlock` models; handler instances are
  reused concurrently and must retain no request content or mutable request state.

  ```python
  from collections.abc import Mapping

  from typing_extensions import override

  from privacy_guard.request_body import FormatHandler, RequestBody
  from privacy_guard.config import PolicyConfig


  class CustomHandler(FormatHandler):
      def __init__(self) -> None:
          super().__init__(format_name="custom")

      @override
      def _normalize(self, raw_body: bytes, policy_config: PolicyConfig) -> RequestBody:
          return RequestBody(
              text_blocks=(), parsed_value=None, original_bytes=raw_body
          )

      @override
      def _reconstruct(
          self,
          request_body: RequestBody,
          replacements_by_path: Mapping[str, str],
      ) -> bytes:
          return request_body.original_bytes
  ```
- **Log safety.** Raw bodies, parsed values, and text-block content use `repr=False` to
  keep content out of routine domain representations; this does not sanitize
  arbitrary tracebacks. Cataloged errors and gRPC status details are
  content-safe, and caught collaborator exception chains must never be logged.

## Development validation

Run the complete local gate from this directory:

```bash
scripts/validate.sh
```

To exercise the package with the minimum supported interpreter, run
`scripts/validate.sh --python 3.11`. This project has no dedicated CI workflow,
so this committed entry point is the authoritative contributor gate. It runs the
full tests, formatting, lint, curated `ty` rules, import smoke, and package build.
The AST policy test rejects cast operations and explicit dynamic typing in
handwritten `src`, `tests`, and `examples`; only generated protobuf/gRPC bindings
are excluded.

The optional diagnostic benchmark uses three samples per median and covers a
focused set of body sizes, finding loads, scanner shapes, and reconstruction
modes:

```bash
uv run --frozen python scripts/benchmark_privacy_guard.py
```

Use it manually when changing performance-sensitive processing code; it is not
part of `scripts/validate.sh` and does not enforce regression thresholds. For
the broader scenario set and seven samples per median, pass `--suite full`.
Pass `--profile profile.out` to either suite to record a `cProfile` artifact.
The harness reports median wall time and median peak traced allocation for the
synchronous normalize, synthetic scan, output validation, policy, and
reconstruction path. It does not measure a real PII scanner, gRPC adaptation,
executor queuing, concurrent throughput, or process RSS. Methodology and one
platform-specific development snapshot are in [BENCHMARKS.md](BENCHMARKS.md).
