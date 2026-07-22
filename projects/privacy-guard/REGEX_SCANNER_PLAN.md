# Regex scanner implementation plan

## Outcome

`RegexScanner` will become Privacy Guard's default scanner implementation. It
will compile a strict YAML configuration at startup and emit findings for every
configured entity and pattern match. Operators will be able to maintain focused
configuration files for different environments, such as customer-specific data
or HIPAA-regulated identifiers, without changing Python code.

The scanner type will be the default; the rule set will remain explicit. The
service must never silently start with an empty or missing rule set.

“Supports” a catalog size means that the complete configuration can be parsed,
validated, compiled, and used to scan representative requests within the
default request budget. Merely accepting a large file that predictably times
out does not satisfy the scalability goal. Catalog capacity is independent of
match cardinality: a profile may define thousands of entities even though one
unusually dense request can still hit the existing per-block, per-request, or
protocol-result limits and be denied safely.

## Proposed YAML contract

A single-profile configuration is its complete entity catalog. Every listed
entity is active, so an additional top-level `entities` wrapper would add no
information:

```yaml
- name: email
  patterns:
    - name: common-email
      regex: '(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![\w.-])'
      confidence: high
- name: medical-record-number
  patterns:
    - name: prefixed-mrn
      regex: '\bMRN[ :]?[0-9]{6,10}\b'
      confidence: high
      ignore_case: true
```

A file such as `entities.yaml`, `customer.yaml`, or `hipaa.yaml` can use this
shape. The filename is descriptive only and does not affect loading.

A multi-profile configuration groups complete entity catalogs under explicit
profile names. Each profile value has the same entity-list shape as the entire
single-profile file:

```yaml
profiles:
  customer-support:
    - name: email
      patterns:
        - name: common-email
          regex: '(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![\w.-])'
          confidence: high
    - name: customer-id
      patterns:
        - name: prefixed-customer-id
          regex: '\bCUST-[0-9]{8}\b'
          confidence: high
  hipaa:
    - name: medical-record-number
      patterns:
        - name: prefixed-mrn
          regex: '\bMRN[ :]?[0-9]{6,10}\b'
          confidence: high
          ignore_case: true
```

The command will require `--profile NAME` when the file contains `profiles`. It
will reject `--profile` for a single-profile file, avoiding an ignored or
ambiguous option. Exactly one selected entity catalog is active; profiles are
not merged implicitly.

Scanner identity is runtime configuration rather than rule-set content. The
default command will use the stable name `regex`, with an explicit
`--scanner-name` override for deployments that compose multiple scanners. This
keeps scanner lifecycle metadata out of profile configuration and avoids the
ambiguous `scanner` top-level wrapper.

Profile, entity, and pattern names will match
`[A-Za-z_][A-Za-z0-9_-]*` and have explicit ASCII byte-length limits. This
excludes `/`, making the reported `entity/pattern` label unambiguous. Regexes,
confidence, and optional flags will also be strictly validated. The document
must be either a non-empty entity list or a mapping containing only a non-empty
`profiles` mapping. Unknown fields, duplicate profile or entity names, empty
profiles, empty entity catalogs, empty pattern lists, invalid Unicode, invalid
regular expressions, duplicate pattern names within an entity, and inputs
exceeding configured byte or count limits will fail startup. Entity names from
the active catalog will populate `ScannerConfig.entity_types`; policy
validation will therefore continue to reject entity names absent from the
selected profile. Policy filtering remains entity-level in this iteration;
pattern names add attribution, not a second policy-selection language.

The loader will parse and validate every profile, not only the selected one, so
an inactive malformed profile cannot remain latent. Document-wide limits bound
the complete file, while active-profile limits bound the catalog compiled into
one scanner. Regex syntax and flags will therefore be compiled and checked for
every profile during loading, but only the selected profile's compiled rules
will be retained by the scanner.

Pattern names may use letters, digits, underscores, and hyphens, but must begin
with a letter or underscore. Privacy Guard will replace `-` with `_` only when
creating the internal regular-expression group identifier. The original
configured name remains unchanged in domain findings and service reporting.
Because patterns are compiled separately, names such as `common-email` and
`common_email` may safely normalize to the same internal group identifier while
remaining distinct in their per-pattern metadata. Original pattern names must
still be unique within an entity. Configured regexes may use unnamed or
non-capturing groups, including numeric backreferences, but may not declare
named groups or inline flags. Named groups are reserved for Privacy Guard's
automatic pattern markers, while supported flags (`ignore_case`, `multiline`,
`dot_all`, and `ascii`) are explicit YAML booleans. Validate reserved regex
constructs with engine-aware parsing or compilation metadata, not substring
searches that can confuse escaped text with syntax.

Patterns must produce non-empty spans. The loader will reject expressions that
match the empty input; because zero-width behavior can also depend on surrounding
text, the scanner will treat any runtime zero-length match as a content-safe
scanner-configuration failure and return no partial findings. Startup loader
failures and runtime-discovered configuration failures will use the same
dedicated error code, but runtime failures remain middleware-internal errors;
they are not blamed on the request that happened to expose the pattern defect.

## Implementation

1. Add strict frozen models for the single-profile and multi-profile file
   shapes, named profiles, entities, and patterns. Add one selection step that
   resolves the active entity catalog before constructing the scanner
   configuration. Treat YAML as an untrusted boundary: check the byte limit
   before decoding, require strict UTF-8, use safe standard tags only, reject
   anchors and aliases, reject duplicate mapping keys, and bound nesting, node
   count, and scalar bytes before constructing Python values. Translate syntax,
   structural, and model failures into one content-safe scanner-configuration
   error that never includes paths, patterns, or matched text. Add a dedicated
   `SCANNER_CONFIG_INVALID` catalog code rather than reusing policy
   `CONFIG_INVALID`. Add the selected safe-YAML parser and timeout-capable regex
   engine as bounded project dependencies in `pyproject.toml` and `uv.lock`;
   do not build either boundary on undocumented engine behavior.
2. Add `RegexScannerConfig`, derived from `ScannerConfig`, and `RegexScanner`.
   Compile each configured expression as
   `(?:expression)(?P<normalized_group_name>)`. The trailing empty named marker
   makes the pattern identity observable without inserting a capture before the
   user's expression, so unnamed group numbering and numeric backreferences are
   preserved. Retain the original pattern name alongside the compiled rule.
   Compile patterns separately and use overlapping iteration so matches can
   overlap both within one pattern and across different patterns, while
   normalized group names remain rule-local. Read the automatic marker to
   identify the matched rule, then report the original, non-normalized pattern
   name from immutable rule metadata. Scanner calls will remain stateless and
   thread-safe.
3. Add an immutable request-scoped `ScanBudget` containing a monotonic deadline
   and export it from `privacy_guard.scanners` for advanced scanner authors.
   Begin with a conservative provisional package default, then document and
   finalize it using the benchmark gate below. `RequestProcessor` will accept an
   optional finite, positive, upper-bounded `scan_timeout_seconds` construction
   argument with that safe default; callers do not need to configure it. Reject
   booleans as well as non-finite, non-positive, and over-maximum numeric values.
   `RequestProcessor.process` will create one budget immediately before scanning
   and pass it through every `Scanner.scan` call for every block and scanner.
   `Scanner.scan` will accept an optional keyword-only budget and create a fresh
   default budget for standalone calls when none is supplied. The protected
   scanner hook receives the effective non-optional budget; simple scanners may
   ignore it, while scanners performing potentially unbounded work must
   cooperate with it. The budget is therefore an automatic hard bound for
   `RegexScanner`, but only a cooperative contract for custom scanners. Before
   each pattern evaluation,
   `RegexScanner` will pass the smaller of the remaining request budget and the
   per-pattern ceiling to a timeout-capable regex engine. Check the budget while
   consuming the engine's iterator, not only when constructing it. Budget
   exhaustion or an engine timeout will raise one exported, content-safe
   `ScanBudgetExceeded` exception from `Scanner.scan`, with no partial findings.
   `RequestProcessor` will catch only that typed condition, discard findings
   accumulated from earlier scanners or blocks, and produce the existing stable
   limit deny. Standalone scanner callers receive the typed exception instead of
   a service-specific decision. This deliberately evolves the protected scanner
   extension signature; all built-in examples, tests, and extension
   documentation must migrate in the same change.
4. Define independent limits for configuration bytes, profiles, entities,
   patterns, pattern bytes, matches per pattern, and request evaluation time.
   Configuration capacity must not be derived from response aggregation limits:
   profiles should support thousands of entities and patterns. Establish the
   final hard ceilings with startup-time, startup-memory, and scan-time
   benchmarks. The scalability gate is 1,000 active entities and 10,000 active
   patterns on documented representative inputs and reference hardware. Record
   input size and match density with each result so the claim is reproducible.
   The request-scoped budget remains the runtime CPU bound regardless of
   catalog size. If separately evaluating 10,000 compiled patterns cannot meet
   the default budget, introduce bounded batching, indexing, or a multi-pattern
   engine before claiming that scale; preserve overlapping results and original
   pattern identity in any optimization. If batching combines patterns, assign
   collision-free internal group identifiers even when configured names
   normalize to the same value. Implement conservative provisional file and
   count ceilings first, then finalize and document them only after this gate
   passes.
5. Add optional bounded string metadata to the domain `Finding` so scanners can
   attach scanner-specific attribution without adding fields to the shared model.
   `RegexScanner` stores its configured pattern name under `pattern_name`.
   Include that metadata value, or `""` when absent, after entity in
   scanner-result, block-result, overlap-winner, and final-winner ordering keys
   so otherwise identical findings remain deterministic without changing higher-priority
   span or confidence semantics.
6. Include the pattern in audit-safe service reporting. Findings without a
   pattern retain the existing `type=scanner_name` and `label=entity` shape.
   Findings with a pattern retain `type=scanner_name` and use the unambiguous
   `label=entity/pattern_name` shape. Group by scanner, entity, optional pattern,
   and confidence; validate the combined UTF-8 label and encoded protobuf size
   before returning it. Keep the current limit of 32 aggregated findings per
   middleware stage: the checked-in OpenShell protocol explicitly documents
   that receiver limit. It limits distinct groups matched in one response, not
   entities or patterns configured in a profile. The existing 4,096 domain
   findings per request and 4 KiB per-group encoded limits also remain. If more
   than 32 distinct groups must be reported in one stage, coordinate that as a
   separate OpenShell protocol change before increasing Privacy Guard's limit;
   the scanner implementation must not emit a response its consumer rejects.
7. Add a loader such as `RegexScanner.from_yaml(path, profile=None)`. Update the
   packaged command to accept one required `--scanner-config PATH`, a
   conditionally required `--profile NAME`, and an optional `--scanner-name`.
   Load and compile the configuration before binding a listening socket. Restore
   the `privacy-guard` script only in the same change, so the command cannot run
   with an implicit allow-all scanner between implementations.
8. Add example rule sets under `examples/regex-configs/`: single-profile
   `customer.yaml` and `hipaa.yaml` files plus a `profiles.yaml` file showing
   both catalogs in the multi-profile shape. The HIPAA example must be
   described as a starting rule set, not a claim of compliance; operational
   controls and validation remain the deployer's responsibility. Migrate the
   self-contained `examples/email-scanner/` example from its handwritten scanner
   to `RegexScanner` and a colocated YAML file, so the primary manual example
   exercises the new default without depending on another example directory.

## Verification

- Unit-test both YAML shapes, profile selection, missing and unexpected
  selections, ordering, limits, duplicate keys, aliases, unsafe tags, excessive
  nesting and scalar sizes, unsupported flags, malformed regexes, and
  content-safe failures. Verify that every profile is validated even when it is
  not selected.
- Test Unicode offsets, multiple entities and patterns, overlaps, confidence,
  case handling, automatic hyphen normalization, normalized-name coexistence,
  trailing named markers, numeric backreferences, reserved named-group and
  inline-flag rejection, load-time and contextual zero-width rejection,
  deterministic ordering, and finding limits.
- Test per-pattern and request-wide timeout behavior with adversarial
  expressions, many patterns, many text blocks, and long inputs. Verify timeout
  paths raise `ScanBudgetExceeded` for standalone calls and return a stable deny
  through `RequestProcessor`, with no partial findings in either case.
- Test that `RequestProcessor` and standalone `Scanner.scan` calls use safe
  default budgets when callers provide no timeout or budget, and that an
  explicit processor timeout applies one shared deadline across the request.
- Test concurrent calls against one scanner instance.
- Exercise observe, redact, and block through `RequestProcessor` and the gRPC
  boundary with both customer and HIPAA example configurations. Verify that
  domain and aggregated service findings report the configured pattern name,
  legacy findings without a pattern retain their current representation, and
  profiles containing more than 32 entities load and scan sparse inputs
  normally. Verify the exact 32-group protocol boundary, the 4,096
  domain-finding boundary, and the per-group encoded-size boundary; the
  thirty-third distinct response group must produce the stable limit deny with
  no partial findings.
- Extend the diagnostic benchmark with representative rule counts and matching
  densities, including 100, 1,000, and 10,000 active patterns. Record parse and
  compilation time, retained memory, scan latency, timeout rate, input size,
  and match density before establishing the final configuration ceilings,
  default request budget, or performance thresholds.

## Delivery sequence

Each delivery step includes its focused unit tests; the final step adds
cross-component and adversarial coverage rather than deferring all testing.

1. Configuration models, bounded YAML loader, dependencies, and conservative
   provisional safety limits.
2. Request-scoped scan budget, regex compilation, and scanning engine.
3. Scalability benchmark, engine optimization if required, and final limits and
   default budget.
4. Backward-compatible finding metadata and pattern-aware service reporting.
5. Service CLI wiring and restoration of the package script.
6. Customer and HIPAA example configurations and user documentation.
7. Security-focused tests and full integration tests.
