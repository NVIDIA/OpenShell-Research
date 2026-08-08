---
title: Parse request content
description: Select and replace bounded text in complete bodies, JSON fields, and normalized message blocks.
agent_markdown: true
---

# Parse request content

The public `egress_gate.request_content` package lets built-in and custom gates
interpret the current request body as independently inspectable text targets.
It supports a complete UTF-8 body, selected strings in a strict JSON document,
and normalized message blocks derived from harness-specific JSON envelopes.

`Utf8TextParser`, `JsonFieldsParser`, and `MessageBlocksParser` implement the
same `RequestContentParser` contract. Each parser owns both text extraction and
rendering immutable `TextReplacement` values back into a complete replacement
body. The regex gate composes these parsers, but they are not regex-specific;
trusted custom gates can use the same public API.

Here, *bounded* means that parsing and replacement enforce explicit limits on
body size, JSON depth, node and selector counts, selected text, and message
blocks. All work also uses the request's shared deadline. See
[Limits and failures](reference/limits-and-failures.md) for the failure
contract.

## Use a parser from a custom gate

Prepare a parser once with the gate, then parse the current body inside each
evaluation. The returned view contains request-local targets and owns rendering
their replacements back into complete body bytes:

```python title="Parse and replace selected JSON text"
from egress_gate.request_content import (
    JsonFieldsParser,
    JsonKeySegment,
    JsonSelector,
    TextReplacement,
)

parser = JsonFieldsParser(
    selectors=(
        JsonSelector(
            segments=(JsonKeySegment(kind="key", value="prompt"),),
        ),
    ),
)

parsed = parser.parse(request.body, timeout=timeout)
replacements = tuple(
    TextReplacement(target_id=target.id, text=transform(target.text))
    for target in parsed.targets
)
replacement_body = parsed.replace_text(replacements, timeout=timeout)
```

The custom gate can return `replacement_body` through `RequestMutations` after
declaring `GateCapability.REPLACE_BODY`. Keeping parsing and rendering on the
same request-local view prevents a replacement from targeting text that the
parser did not expose.

## Select JSON values with typed paths

A `JsonSelector` is an ordered sequence of typed path segments. It is not a
JSONPath string. Egress Gate starts with one JSON value and applies each segment
to the values selected by the preceding segment.

| Segment | Required input | Selection |
| --- | --- | --- |
| `key` | JSON object | The value of the member whose key exactly equals `value` |
| `index` | JSON array | The item at position `value`; `0` is the first item, `1` is the second, and so on |
| `each` | JSON object or array | Every immediate object-member value or array item |

For example, this selector chooses the `content` value from every item in the
top-level `messages` array:

```yaml title="Select every message's content"
segments:
  - kind: key
    value: messages
  - kind: each
  - kind: key
    value: content
```

Given this document:

```json
{
  "messages": [
    {"content": "first"},
    {"content": "second"}
  ]
}
```

the selector produces the two string values `first` and `second`.

Use `index` when a policy needs one specific array position. The numeric
`value` is the position in the array:

```yaml title="Select the first message's content"
segments:
  - kind: key
    value: messages
  - kind: index
    value: 0
  - kind: key
    value: content
```

This selector produces only `first`. An index equal to or greater than the
array length selects nothing; it is not an input error.

A segment applied to the wrong JSON kind also selects nothing. For example,
`key` does not select from an array, and `index` does not select from an object.
Missing paths therefore produce no selected values. When a parser requests
text, selected non-string terminal values also produce no text targets.

Multiple selectors are evaluated in configuration order. When selectors reach
the same JSON node, Egress Gate returns that node once. Results produced by one
selector follow their order in the document.

## Select JSON string fields

`JsonFieldsParser` evaluates selectors from the JSON document root and exposes
each selected string as an independent `TextTarget`. A match or other custom
gate operation on one target cannot span another selected string.

The regex gate exposes this parser through `scan.kind: json-fields`:

```yaml title="Scan selected JSON strings"
scan:
  kind: json-fields
  selectors:
    - segments:
        - kind: key
          value: messages
        - kind: each
        - kind: key
          value: content
  action:
    kind: replace
    template: '[{entity}]'
```

## Map normalized message blocks

`MessageBlocksParser` builds on the same strict JSON document. Its
`MessageBodyParser` first selects message containers and normalizes their roles
and text-bearing blocks. `JsonMessageMapParser` is the configurable mapping for
ordinary JSON harness envelopes.

The mapping's `messages` selector starts at the document root. Its
`text_selectors`, `tool_input_selectors`, and `tool_output_selectors` start at
each selected message object. This distinction is important: relative text
selectors do not repeat the path to the message array.

Message roles normalize to `system`, `developer`, `user`, `assistant`, `tool`,
or `unknown`. Blocks normalize to `text`, `tool_input`, or `tool_output`.
Provider- or harness-specific parsers can implement the public
`MessageBodyParser` protocol without changing consumers such as the regex gate.

See the [regex gate](gates/regex.md#normalized-message-blocks) for a complete
policy example and [custom gates](gates/custom.md) for the trusted extension
contract.

## Replacement and parsing behavior

JSON parsing is strict and bounded. It rejects invalid UTF-8, malformed JSON,
duplicate object keys, non-standard constants, invalid Unicode scalar values,
and configured parsing limits.

Structured replacement re-encodes only selected JSON string tokens. It
preserves every source byte outside those tokens, including whitespace, number
spellings, key order, and escaping in unrelated strings. A parsed-content view
rejects replacement of targets it did not expose.

Request-content parsers are prepared stateless objects. Each parsed result and
its document-local target identities belong to one request evaluation and must
not be reused with another request body.
