---
title: Format handlers
description: Request-body normalization, opaque paths, and reconstruction contracts.
agent_markdown: true
---

# Format handlers

A format handler translates raw body bytes into text blocks and reconstructs the
body after redaction. It owns all format-specific parsing and path syntax.

The processor uses the same interface for every format and never parses a path.

## Format handler interface

A format handler implements two protected methods:

| Method | Responsibility |
| --- | --- |
| `_normalize(raw_body, policy_config)` | Parse bytes and return a `RequestBody` |
| `_reconstruct(request_body, replacements_by_path)` | Apply replacements and return bytes |

Applications call the public `normalize` and `reconstruct` methods. These
wrappers validate extension output.

The handler constructor declares a stable `format_name`. The processor registry
key must match it.

## Normalized body

`RequestBody` contains:

| Field | Purpose |
| --- | --- |
| `text_blocks` | `TextBlock` values selected for scanning |
| `parsed_value` | Opaque handler-owned reconstruction state |
| `original_bytes` | Exact input bytes |

`TextBlock` contains:

| Field | Purpose |
| --- | --- |
| `path` | Opaque handler-defined address |
| `text` | String passed to scanners |
| `replaceable` | Whether redaction may rewrite this block |

The processor requires unique paths. It may compare paths and use them as
mapping keys, but it must not parse them.

The handler must not mutate `parsed_value` during reconstruction. It may copy
the value before applying replacements.

## Unchanged and rewritten bodies

When `replacements_by_path` is empty, return `original_bytes` exactly.

When replacements exist:

- replace only the addressed text blocks
- reject invalid paths or replacement types
- preserve untouched values semantically
- return bytes

A rewritten body does not need to preserve whitespace, object formatting, or
other serialization details.

## JSON handler

`JsonHandler` is the only built-in format handler. It accepts strict UTF-8 JSON
and rejects:

- invalid UTF-8
- duplicate object keys
- non-finite numbers
- invalid Unicode scalar values
- excessive nesting or text shape

It scans:

- every JSON string value
- every object key

It does not scan numbers, booleans, or null.

### Paths

String values use JSON Pointer paths:

```text
{"items": [{"email": "a@example.com"}]}
```

```text
/items/0/email
```

The root string uses the empty path. Pointer tokens escape `~` as `~0` and `/`
as `~1`.

Object keys use an internal `#key:` prefix:

```text
#key:/items/0/email
```

Key paths are not reconstruction paths. Their text blocks set
`replaceable=False`.

### Reconstruction

`JsonHandler` copies its parsed tree, resolves each JSON Pointer, verifies the
target is a string, applies the replacement, and serializes compact UTF-8 JSON.

Object keys are never rewritten. If redact policy selects a finding in a key,
the processor denies the request.

## Minimal plain-text handler

```python
from collections.abc import Mapping

from privacy_guard.config import PolicyConfig
from privacy_guard.errors import ErrorCode, PrivacyGuardError
from privacy_guard.request_body import FormatHandler, RequestBody, TextBlock


class PlainTextHandler(FormatHandler):
    def __init__(self) -> None:
        super().__init__(format_name="text")

    def _normalize(
        self,
        raw_body: bytes,
        policy_config: PolicyConfig,
    ) -> RequestBody:
        try:
            text = raw_body.decode("utf-8")
        except UnicodeDecodeError:
            raise PrivacyGuardError(ErrorCode.BODY_ENCODING_INVALID) from None
        return RequestBody(
            text_blocks=(TextBlock(path="", text=text),),
            parsed_value=None,
            original_bytes=raw_body,
        )

    def _reconstruct(
        self,
        request_body: RequestBody,
        replacements_by_path: Mapping[str, str],
    ) -> bytes:
        if not replacements_by_path:
            return request_body.original_bytes
        if set(replacements_by_path) != {""}:
            raise PrivacyGuardError(ErrorCode.BODY_RECONSTRUCTION_INVALID)
        return replacements_by_path[""].encode("utf-8")
```

Register it when constructing the processor:

```python
processor = RequestProcessor(
    scanners=[scanner],
    format_handlers={"text": PlainTextHandler()},
)
```

## Concurrency

One handler instance serves concurrent requests. Keep all parsed request state
inside the returned `RequestBody`.

Do not store request bytes, parsed bodies, text blocks, paths, or replacements
on the handler instance.

## Testing a handler

Test:

- valid and invalid input encoding
- parser edge cases specific to the format
- stable and unique text-block paths
- text selection and `replaceable` flags
- exact no-op byte preservation
- replacement of every supported path shape
- invalid and stale replacement paths
- reconstruction does not mutate parsed state
- shape limits
- concurrent calls

Processor tests should cover cross-handler rules such as path uniqueness,
original-byte equality, and aggregate text limits.

[Back to the architecture overview](index.md)
