# Contributing to Privacy Guard

Run `scripts/validate.sh` from this directory before review. Run
`scripts/validate.sh --python 3.11` when changing interpreter-sensitive code.
The gate owns full tests, Ruff formatting and lint, curated `ty` diagnostics,
the import smoke test, and the package build.

Trust-boundary validation has one owner:

| Boundary | Owner |
| --- | --- |
| Scalar fields and record shape | strict frozen Pydantic models |
| Action selection and action-specific fields | `PolicyConfig.on_finding` discriminated union keyed by `action` |
| Scanner metadata and scanner output shape | `Scanner` public wrapper |
| Format metadata and normalized/reconstructed output shape | `FormatHandler` public wrappers |
| Scanner/block identity, offsets, aggregate limits, and original bytes | `RequestProcessor` |
| Recursive JSON values | cached Pydantic adapter in `JsonHandler` |
| Protobuf conversion | service adapter |

Custom scanners and format handlers subclass the nominal ABCs, call the base
constructor, and decorate protected hook implementations with `@override`.
`RequestProcessor` accepts a scanner sequence. Scanners return block-relative
`Finding` models; the processor attaches the opaque text-block path by composing
them into `RequestBodyFinding` models.

Scanner configuration must not depend on policy. Every `ScannerConfig` declares
the scanner's complete `entity_types` catalog, and concrete subtypes own any
additional detection behavior. Observe, block, and redact action configs own
the finding criteria applied after scanning.

Within modules and classes, place the public API before private implementation
details whenever dependency ordering allows it. Keep private constants, helper
records, functions, and methods toward the bottom of their containing scope.

The handwritten typing policy is AST-enforced by
`tests/test_typing_policy.py`. Generated bindings under
`src/privacy_guard/bindings` are excluded. Keep generics parameterized and
suppressions narrow and rule-specific. Production and test overrides are
explicit; example scripts omit `@override` to keep their public API surface
minimal and copyable.

When changing performance-sensitive processing code, run the optional
diagnostic benchmark with:

```bash
uv run --frozen python scripts/benchmark_privacy_guard.py --suite full
```

Use the default quick suite during iteration. Both suites verify findings and
reconstruction outcomes before reporting median wall time and peak traced
allocation. The benchmark is not part of the contributor gate and has no
pass/fail thresholds; compare results only on a controlled environment. It uses
a synthetic scanner and the synchronous processor path, so it does not measure
real scanner cost, service concurrency, queuing, gRPC adaptation, or process
RSS.
