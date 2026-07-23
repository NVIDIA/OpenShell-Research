---
title: Scanners
description: Scanner configuration, lifecycle, output contract, and extension rules.
agent_markdown: true
---

# Scanners

A scanner detects sensitive data in one text block. It has no access to the
request format, text-block path, active policy, gRPC request, or protobuf
messages.

## Scanner interface

A scanner:

1. declares a concrete `ScannerConfig` type
2. receives validated configuration at construction
3. receives one text block and a `ScanBudget` in `_scan`
4. returns a tuple of block-relative `Finding` values

The public `scan` method owns output validation. Extensions implement `_scan`
and do not override `scan`.

## Configuration

`ScannerConfig` contains:

| Field | Purpose |
| --- | --- |
| `name` | Stable scanner identity copied into findings |
| `entity_types` | Complete set of entity names the scanner can emit |

Custom scanners subclass `ScannerConfig` to add detection-specific fields.
Configuration fields are strict and frozen. Use immutable types for custom
fields because nested values are not deep-frozen. Scanner configuration remains
independent of policy.

The base constructor infers the concrete config type from
`Scanner[ConcreteConfig]`, validates the supplied model, stores scanner metadata,
and then calls `_initialize`.

Use `_initialize` for reusable derived state such as compiled expressions. Do
not define a scanner `__init__` for custom initialization.

## Minimal scanner

```python
from pydantic import Field

from privacy_guard.scanners import Finding, ScanBudget, Scanner, ScannerConfig


class KeywordScannerConfig(ScannerConfig):
    keyword: str = Field(min_length=1)


class KeywordScanner(Scanner[KeywordScannerConfig]):
    def _scan(self, text_block: str, budget: ScanBudget) -> tuple[Finding, ...]:
        start = text_block.find(self.config.keyword)
        if start < 0:
            return ()
        return (
            Finding(
                entity="keyword",
                scanner_name=self.scanner_name,
                start_offset=start,
                end_offset=start + len(self.config.keyword),
            ),
        )
```

Construct it with an explicit entity catalog:

```python
scanner = KeywordScanner(
    KeywordScannerConfig(
        name="keywords",
        entity_types=frozenset({"keyword"}),
        keyword="secret",
    )
)
```

## Finding model

A `Finding` contains:

| Field | Meaning |
| --- | --- |
| `entity` | Entity name declared in `ScannerConfig.entity_types` |
| `scanner_name` | Name of the scanner that produced the finding |
| `start_offset` | Inclusive character offset within the text block |
| `end_offset` | Exclusive character offset within the text block |
| `confidence` | `low`, `medium`, or `high` |
| `metadata` | Optional bounded, immutable scanner attribution |

Offsets count Python string characters, not UTF-8 bytes. A finding must cover a
non-empty span.

The processor adds the text-block path later by creating a
`RequestBodyFinding`. Scanners never create or interpret paths.

## Output validation

`Scanner.scan` validates the returned tuple and findings. `RequestProcessor`
validates scanner identity, declared entities, offsets, and aggregate limits.
The processor validates every finding, then enforces block and request totals on
findings selected by policy.

See [Validation ownership](safety-and-limits.md#validation-ownership).

## Budgets and blocking work

After normalization, the processor creates one monotonic budget for the scan
phase and shares it across every scanner and text block. The budget is
cooperative.

Iterative scanners should call `budget.remaining_seconds()` before and after
each bounded unit of work. The method returns the remaining time or raises
`ScanBudgetExceeded`.

A budget cannot interrupt a blocking operation already in progress. For
example, Python's standard regex engine cannot stop an expression while it is
evaluating. Avoid operations without a tested worst-case bound.

## Concurrency

One scanner instance serves concurrent requests through the service worker
pool. Scanner state must be immutable after initialization.

Do not store:

- text blocks
- matches from a request
- request IDs
- mutable per-request counters

Keep per-request data local to `_scan`; do not mutate shared objects from it.

## Built-in regex scanner

`RegexScanner` loads a bounded YAML catalog. It compiles configured patterns in
`_initialize` and stores immutable compiled rules.

Each regex finding includes `pattern_name` metadata. The processor uses entity
names for policy filtering. The service renders the audit label as
`entity/pattern-name`.

The regex scanner:

- rejects empty matches
- rejects named groups because it reserves an internal marker group
- rejects inline flags in favor of explicit config fields
- supports overlapping matches by advancing from the previous match start
- limits matches per pattern and findings per block
- checks the shared budget around each regex evaluation

## Testing a scanner

Test scanners without gRPC:

- valid and invalid configuration
- no-match and match cases
- Unicode offsets
- declared entity and scanner identity
- output ordering when relevant
- finding limits
- budget exhaustion for iterative work
- concurrent calls when the scanner keeps derived state

Processor tests should cover only behavior that requires multiple scanners,
text-block paths, policy, or request-wide limits.

[Back to the architecture overview](index.md)
