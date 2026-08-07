---
title: Regex gate
description: Scan one configured part of a request with bounded regular expressions.
agent_markdown: true
---

# Regex gate

The `regex` gate matches one configured part of the current request. It can
inspect the complete body, selected JSON string fields, normalized JSON message
blocks, path, query, or selected header values. It returns audit-safe findings
with type `regex_match`.

Choose what to scan with `scan.kind`, then choose what to do with
`scan.action.kind`. This example replaces matches in the request body:

```yaml title="Inline regex catalog"
name: customer-identifiers
kind: regex
scan:
  kind: body
  action:
    kind: replace
    template: '[{entity}]'
pattern_catalog:
  entities:
    - name: customer-id
      rules:
        - name: customer-id-rule
          pattern: '\bCUST-[0-9]{8}\b'
          confidence: high
```

The body is decoded as strict UTF-8. Path and query scans use the exact text in
the request model. A header scan matches each selected header value on its own;
a match cannot span two values. Header names are case-insensitive:

```yaml title="Selected request headers"
name: labeled-headers
kind: regex
scan:
  kind: header
  names: [x-customer-note, x-request-label]
  action:
    kind: deny
pattern_catalog: patterns.yaml
```

The header scan sees the current request snapshot, including validated header
mutations from earlier gates. The regex gate does not return header mutations
itself.
OpenShell permits writes only in the `x-openshell-middleware-` namespace, so a
general regex replacement cannot rewrite arbitrary selected headers. A custom
gate can return supported header writes or removals when it declares the
`GateCapability.MUTATE_HEADERS` capability.

## Structured JSON fields

`json-fields` parses the current body as strict UTF-8 JSON and scans only string
values selected by typed paths. A selector starts at the document root. `key`
selects one exact object member, `index` selects one zero-based array item, and
`each` selects every immediate array item or object member value:

```yaml title="Selected JSON message content"
name: message-identifiers
kind: regex
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
pattern_catalog: patterns.yaml
```

Missing paths and non-string terminal values produce no scan text. Overlapping
selectors select the same JSON string once. Matches cannot span two selected
string values. Structured replacement re-encodes each selected string token
and preserves every source byte outside selected tokens, including whitespace,
number spellings, key order, and escaping in unrelated strings.

The JSON parser rejects invalid UTF-8, malformed JSON, duplicate object keys,
non-standard constants, invalid Unicode scalar values, and configured parsing
limits. Invalid UTF-8 produces `body_encoding_invalid`; invalid strict JSON
produces `body_format_invalid`.

## Normalized message blocks

`message-blocks` builds on the same JSON document. A `json-message-map` selects
one or more message arrays, reads each message role, and applies relative
`text_selectors`. Optional `tool_input_selectors` and `tool_output_selectors`
classify provider- or harness-specific fields explicitly. The mapping requires
at least one selector across those three groups. The scan can then filter
normalized roles and block kinds:

```yaml title="Selected user and tool messages"
name: model-visible-identifiers
kind: regex
scan:
  kind: message-blocks
  parser:
    kind: json-message-map
    messages:
      segments:
        - kind: key
          value: request
        - kind: key
          value: messages
    role_key: role
    text_selectors:
      - segments:
          - kind: key
            value: content
      - segments:
          - kind: key
            value: content
          - kind: each
          - kind: key
            value: text
  roles: [system, developer, user, tool]
  block_kinds: [text, tool_output]
  action:
    kind: deny
pattern_catalog: patterns.yaml
```

Known roles are `system`, `developer`, `user`, `assistant`, and `tool`; other or
missing roles normalize to `unknown`. Text selected from a `tool` message is a
`tool_output`; other selected text is `text`. Message mappings are policy
configuration, so harness-specific envelopes do not require changes to regex
matching. A custom gate can reuse the public `RequestContentParser`,
`MessageBlocksParser`, and `MessageBodyParser` surfaces when it needs the same
text-target and replacement contracts with a different semantic adapter.

A catalog can be inline or in a relative `.yaml` or `.yml` file. Relative paths
resolve from the Egress Gate process working directory, not from the policy
file. Use an inline catalog when the process does not have a stable working
directory. The gate rejects absolute paths, path traversal, symlinks, YAML
aliases, duplicate keys, invalid body UTF-8, unsafe patterns, and oversized
catalogs.

Each entity has a stable, bounded name and one or more rules. Rule confidence
is `low`, `medium`, or `high`. Optional flags are `ignore_case`, `multiline`,
`dot_all`, and `ascii`. Do not use named capture groups or inline flags.
Patterns must produce non-empty matches. Findings include overlapping
detections. Replacement uses deterministic, non-overlapping matches.

## Actions

| `scan.action.kind` | Match result |
| --- | --- |
| `detect` | `proceed`, findings, no request mutation |
| `deny` | terminal `deny`, findings, `egress_gate_regex_denied` |
| `replace` | `proceed`, findings, explicit body replacement |

`detect` and `deny` work with every scan kind. `replace` exists in the complete
body, `json-fields`, and `message-blocks` schemas. It cannot be configured for
a path, query, or header scan.
This structure keeps unsupported combinations out of generated schemas and
editor suggestions. OpenShell middleware results cannot rewrite a request path
or query. Header replacement is not part of the built-in gate.

The replace action owns its template. It returns a body replacement even when
there is no match. This preserves the operator's explicit intent to replace the
current body. Invalid body UTF-8 is a stable `body_encoding_invalid` service
failure. A structured scan whose UTF-8 body is not strict JSON produces the
stable `body_format_invalid` failure.

The regex gate does not edit the body in place. It returns the replacement in
`RequestMutations`. The pipeline processor uses it to build the next immutable
`HttpRequest` snapshot. If the pipeline allows the request, Egress Gate includes
the replacement in the final mutations returned to the OpenShell supervisor.

Replacement templates contain literal text and the `{entity}` field only.
Output size is projected before rendering and is bounded by the advertised
OpenShell body limit.

## Scan reference

| `scan.kind` | Additional fields | Supported `action.kind` values |
| --- | --- | --- |
| `body` | none | `detect`, `deny`, `replace` |
| `json-fields` | non-empty typed `selectors` | `detect`, `deny`, `replace` |
| `message-blocks` | `parser`; optional `roles` and `block_kinds` | `detect`, `deny`, `replace` |
| `path` | none | `detect`, `deny` |
| `query` | none | `detect`, `deny` |
| `header` | non-empty `names` list | `detect`, `deny` |

Configure another regex gate when different request parts need different
catalogs or actions. Keeping one scan per gate makes matches, findings, and
replacement offsets unambiguous.
