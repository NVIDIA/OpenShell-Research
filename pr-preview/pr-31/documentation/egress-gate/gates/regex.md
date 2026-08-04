---
title: Regex gate
description: Scan one configured part of a request with bounded regular expressions.
agent_markdown: true
---

# Regex gate

The `regex` gate matches one configured part of the current request. It can
inspect the body, path, query, or selected header values. It returns audit-safe
findings with type `regex_match`.

Choose what to scan with `scan.kind`, then choose what to do with
`scan.action.kind`. This example replaces matches in the request body:

```yaml title="Inline regex catalog"
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
kind: regex
scan:
  kind: header
  names: [x-customer-note, x-request-label]
  action:
    kind: deny
pattern_catalog: patterns.yaml
```

The header scan sees the current request snapshot, including validated header
patches from earlier gates. The regex gate does not return header mutations.
OpenShell permits writes only in the `x-openshell-middleware-` namespace, so a
general regex replacement cannot rewrite arbitrary selected headers. A custom
gate can return supported header writes or removals when it declares the
`mutates_headers` capability.

A catalog can be inline or in a relative `.yaml` or `.yml` file. The gate
rejects absolute paths, path traversal, symlinks, YAML aliases, duplicate keys,
invalid body UTF-8, unsafe patterns, and oversized catalogs.

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

`detect` and `deny` work with every scan kind. `replace` exists only in the
body scan schema. It cannot be configured for a path, query, or header scan.
This structure keeps unsupported combinations out of generated schemas and
editor suggestions. OpenShell middleware results cannot rewrite a request path
or query. Header replacement is not part of the built-in gate.

The replace action owns its template. It returns a body replacement even when
there is no match. This preserves the operator's explicit intent to replace the
current body. Invalid body UTF-8 is a stable `body_encoding_invalid` service
failure.

Replacement templates contain literal text and the `{entity}` field only.
Output size is projected before rendering and is bounded by the advertised
OpenShell body limit.

## Scan reference

| `scan.kind` | Additional fields | Supported `action.kind` values |
| --- | --- | --- |
| `body` | none | `detect`, `deny`, `replace` |
| `path` | none | `detect`, `deny` |
| `query` | none | `detect`, `deny` |
| `header` | non-empty `names` list | `detect`, `deny` |

Configure another regex gate when different request parts need different
catalogs or actions. Keeping one scan per gate makes matches, findings, and
replacement offsets unambiguous.
