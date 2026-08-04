---
title: Regex-body gate
description: Configure bounded regex matching, denial, and replacement.
agent_markdown: true
---

# Regex-body gate

`regex-body` strictly decodes the current request body as UTF-8. It matches a
bounded catalog and returns audit-safe findings with type `sensitive_entity`.
A catalog can be inline or in a relative `.yaml` or `.yml` file. The gate
rejects absolute paths, path traversal, symlinks, YAML aliases, duplicate keys,
invalid UTF-8, unsafe patterns, and oversized catalogs.

```yaml title="Inline regex catalog"
gate: regex-body
pattern_catalog:
  entities:
    - name: customer-id
      rules:
        - name: customer-id-rule
          pattern: '\bCUST-[0-9]{8}\b'
          confidence: high
mode: replace
replacement:
  strategy: template
  template: '[{entity}]'
```

Each entity has a stable, bounded name and one or more rules. Rule confidence
is `low`, `medium`, or `high`. Optional flags are `ignore_case`, `multiline`,
`dot_all`, and `ascii`. Do not use named capture groups or inline flags.
Patterns must produce non-empty matches. Findings include overlapping
detections. Replacement uses deterministic, non-overlapping matches.

## Modes

| Mode | Match result |
| --- | --- |
| `detect` | `proceed`, findings, no body replacement |
| `deny` | terminal `deny`, findings, `egress_gate_regex_denied` |
| `replace` | `proceed`, findings, explicit replacement bytes |

The replacement recipe is required exactly for `replace`. In replacement mode
the gate returns a replacement even when there is no match, preserving the
operator's intent to replace the current body. Invalid input UTF-8 is a stable
`body_encoding_invalid` service failure.

Replacement templates contain literal text and the `{entity}` field only.
Output size is projected before rendering and is bounded by the advertised
OpenShell body limit.
