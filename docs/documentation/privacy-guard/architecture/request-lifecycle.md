---
title: Request lifecycle
description: How Privacy Guard normalizes, scans, filters, and transforms one request.
agent_markdown: true
---

# Request lifecycle

`RequestProcessor` owns the complete protobuf-free request flow. It composes
format handlers and scanners, applies policy, and returns a `ProcessingResult`.

## Inputs

The processor receives an `InterceptedRequest` containing:

- the exact request body bytes
- a parsed `PolicyConfig`
- the request ID
- the original content type, retained as context but not used for format
  selection

Policy selects the format through `body_format`; the processor does not infer it
from request content or metadata.

After validating the configured format and entity filter, the processor allows
an empty body without normalization or scanning.

## Processing stages

### 1. Select the format handler

The processor looks up `PolicyConfig.body_format` in its registered format
handlers. The handler's own `format_name` must match its registry key.

It also verifies that every entity named by policy appears in at least one
active scanner's `ScannerConfig.entity_types`. An unknown format or entity is
invalid configuration.

### 2. Normalize the body

The selected handler parses the original bytes and returns a `RequestBody`.
The processor then verifies:

- `RequestBody.original_bytes` exactly matches the input
- every `TextBlock.path` is unique
- the number of text blocks is within the request limit
- the total text length is within the scan limit

The processor does not parse text-block paths. Paths belong to the format
handler.

### 3. Scan and filter findings

The processor creates one request-wide `ScanBudget`. It passes every text block
to every configured scanner in registration order. It validates each result,
then selects findings by:

- `entity_types`
- `minimum_confidence`

Policy never changes scanner behavior. `entity_types: null` selects all entity
types. An empty list selects none.
`minimum_confidence: null` accepts every confidence level.

`Scanner.scan` validates the returned tuple and finding models.
`RequestProcessor` validates scanner identity, declared entities, offsets, and
selected finding totals. See
[Validation ownership](safety-and-limits.md#validation-ownership).

### 4. Apply the action

| Action | No selected findings | Selected findings |
| --- | --- | --- |
| `observe` | Allow unchanged | Allow unchanged and report findings |
| `block` | Allow unchanged | Deny and report findings |
| `redact` | Allow unchanged | Replace selected spans, or deny if safe replacement is impossible |

The processor scans all text blocks for every action. The action controls how
selected findings affect the result.

### 5. Reconstruct the body

For redaction, the processor creates replacement text for each affected,
replaceable block. It passes a path-to-text mapping back to the same format
handler that normalized the body.

A selected block action, non-replaceable redaction, or limit result returns
before reconstruction. Every other non-empty request is reconstructed once.
Observe and block-without-findings pass an empty replacement map.

With an empty replacement map, a handler returns the original bytes exactly.
After a rewrite, untouched values must remain semantically equivalent, but
byte formatting may change.

The processor returns a replacement body only when reconstructed bytes differ
from the original.

## Redaction behavior

Findings may overlap. Every action retains all selected findings for reporting.
Redaction resolves overlaps only when choosing spans to replace; losing overlaps
remain in the result.

Redaction chooses non-overlapping replacement spans in this order:

1. higher confidence
2. longer span
3. earlier start offset
4. earlier end offset
5. scanner name
6. entity name
7. pattern name

The stable ordering makes redaction independent of incidental scanner return
order.

The redaction template accepts static text and `{entity}`. Formatting options,
conversions, and other fields are invalid.

Before allocating replacement text, the processor projects its UTF-8 size
against the body limit. It checks the serialized body again after
reconstruction.

## Non-replaceable text

A format handler may expose text that can be observed but not safely rewritten.
It marks that `TextBlock` with `replaceable=False`.

The JSON handler uses this for object keys. Observe reports findings in keys,
and block denies normally. Redact denies the request when a selected finding is
in a key because changing a key could create a collision.

## Output

The processor returns a `ProcessingResult`:

- `ALLOW` with no replacement for an unchanged request
- `ALLOW` with replacement bytes for a successful redaction
- `DENY` with `privacy_guard_blocked` for a policy block
- `DENY` with `privacy_guard_limit_exceeded` when safe bounded output cannot be
  produced

Findings remain path-aware in the domain result. The service later aggregates
them for the protobuf response.

[Back to the architecture overview](index.md)
