# Full-path benchmark evidence

The benchmark invokes `RequestProcessor.process` and therefore covers JSON
normalization, scanner calls, scanner-output validation, contextual finding
validation, policy application, and body reconstruction. Input bodies are exact
1 KiB, 1 MiB, and 4 MiB JSON payloads. The maximum load is the request limit of
4,096 findings, distributed across 16 blocks; multiple-scanner cases use four
scanners. Replacement cases redact matched characters to an empty string so the
4 MiB input remains within the output limit. Each scanner constructs normal
strict `Finding` models during every measured scan; no finding output is reused
between requests. The full suite also contains a separate maximum-width numeric
array: exactly 4,194,303 bytes and 2,097,151 numeric elements, with zero text
blocks and findings. It exposes the complete duplicate-aware stdlib parse and
single cached strict Pydantic adapter path without large-string scanning costs.

JSON normalization stores the adapter's `JsonValue` output directly in private
handler state. It does not build an immutable mirror tree. Incremental text-block
traversal retains state proportional to nesting depth, no-op reconstruction
returns the original bytes without walking the tree, and replacement
reconstruction performs one `deepcopy`.

Run the recorded suite with:

```bash
uv run --frozen python scripts/benchmark_privacy_guard.py --suite full
```

Wall-clock samples are collected without tracing. Peak allocations are measured
in separate runs with `tracemalloc`, preventing allocation tracing from changing
the wall-time result. Each reported value is the median of seven runs after one
verified warm-up. `--profile profile.out` adds one verified `cProfile` pass per
scenario without contaminating either measurement.

Reference environment: CPython 3.11.9 on Apple arm64, macOS 26.5.2. Recorded
2026-07-22 with the command above.

| Scenario | Input bytes | Findings | Scanners | Reconstruction | Median wall (ms) | Median peak traced allocation (bytes) |
| --- | ---: | ---: | ---: | --- | ---: | ---: |
| 1KiB-zero-one-noop | 1,024 | 0 | 1 | no-op | 0.075 | 7,043 |
| 1KiB-typical-multiple-replacement | 1,024 | 8 | 4 | replacement | 0.192 | 19,563 |
| 1MiB-typical-one-noop | 1,048,576 | 8 | 1 | no-op | 55.380 | 2,101,945 |
| 1MiB-max-one-replacement | 1,048,576 | 4,096 | 1 | replacement | 116.764 | 8,252,749 |
| 4MiB-zero-multiple-noop | 4,194,304 | 0 | 4 | no-op | 434.537 | 8,393,513 |
| 4MiB-max-multiple-replacement | 4,194,304 | 4,096 | 4 | replacement | 542.377 | 20,834,860 |
| 1KiB-typical-one-noop | 1,024 | 8 | 1 | no-op | 0.093 | 14,696 |
| 1MiB-zero-one-noop | 1,048,576 | 0 | 1 | no-op | 56.848 | 2,101,890 |
| 1MiB-typical-multiple-replacement | 1,048,576 | 8 | 4 | replacement | 129.166 | 4,209,498 |
| 1MiB-max-multiple-noop | 1,048,576 | 4,096 | 4 | no-op | 126.820 | 5,117,784 |
| 4MiB-typical-one-replacement | 4,194,304 | 8 | 1 | replacement | 303.447 | 16,792,456 |
| 4MiB-typical-multiple-noop | 4,194,304 | 8 | 4 | no-op | 437.978 | 8,393,401 |
| 4MiB-max-one-noop | 4,194,304 | 4,096 | 1 | no-op | 229.557 | 8,402,617 |
| 4194303B-2097151-elements-wide-numeric-zero-one-noop | 4,194,303 | 0 | 1 | no-op | 726.707 | 38,102,178 |

The focused 100,000-element numeric-array regression uses a 200,001-byte body.
On the same interpreter, complete normalization peaked at 1,802,636 traced
bytes (below its committed 8 MiB ceiling), while walking the already validated
tree peaked at 1,067 traced bytes (below 64 KiB), demonstrating that walker
state does not grow with container width.

`tracemalloc` reports traced Python allocations, not process RSS, allocator
arena retention, native allocations, or transient memory outside its tracing
domain. The maximum-width peak includes the unavoidable overlap while the
cached Pydantic adapter constructs its validated output from the stdlib parse
tree; the raw parse tree becomes unreachable when the parse helper returns,
before text-block materialization begins.
