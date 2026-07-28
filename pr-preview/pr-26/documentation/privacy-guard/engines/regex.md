---
title: Use RegexEngine
description: Configure RegexEngine catalogs, matching flags, findings, and deterministic replacement.
agent_markdown: true
---

# Use RegexEngine

`RegexEngine` is the built-in Privacy Guard engine. It detects every configured
regular-expression match and can replace a deterministic non-overlapping subset
with a constrained template.

Privacy Guard provides the catalog schema and execution bounds. It does not
provide an authoritative pattern catalog. Define and test patterns for the data
your deployment handles.

## Engine configuration

```yaml
engine: regex
pattern_catalog: patterns.yaml
replacement:
  strategy: template
  template: "[{entity}]"
```

| Field | Required | Purpose |
| --- | --- | --- |
| `engine` | Yes | Must be `regex` |
| `pattern_catalog` | Yes | Inline catalog or relative YAML path |
| `replacement` | For `replace` actions | Template replacement configuration |

## Catalog structure

```yaml
entities:
  - name: email
    rules:
      - name: conventional-email
        pattern: '(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![\w.-])'
        confidence: high
  - name: customer-id
    rules:
      - name: prefixed-eight-digit-id
        pattern: '\bCUST-[0-9]{8}\b'
        confidence: high
        ignore_case: false
```

### Entity fields

| Field | Required | Rules |
| --- | --- | --- |
| `name` | Yes | Unique catalog identifier matching `[A-Za-z_][A-Za-z0-9_-]*` |
| `rules` | Yes | Non-empty ordered list |

### Rule fields

| Field | Required | Default | Purpose |
| --- | --- | --- | --- |
| `pattern` | Yes | — | Regular expression compiled by the `regex` package |
| `confidence` | Yes | — | `low`, `medium`, or `high` |
| `name` | No | Derived identity | Stable diagnostic identity within the entity |
| `ignore_case` | No | `false` | Case-insensitive matching |
| `multiline` | No | `false` | Make `^` and `$` operate per line |
| `dot_all` | No | `false` | Make `.` include newlines |
| `ascii` | No | `false` | Use ASCII character-class behavior |

Supplied rule names must be unique within an entity. Derived rule identities
are deterministic and do not alter serialized configuration.

Use the explicit flag fields. Inline regex flags and user-defined named groups
are rejected.

## Inline and file-backed catalogs

Use an inline catalog when it fits comfortably within OpenShell's 64 KiB
configuration transport:

```yaml
config:
  engine: regex
  pattern_catalog:
    entities:
      - name: api-key
        rules:
          - pattern: '\bAK_[A-Za-z0-9]{32}\b'
            confidence: high
```

Use a file-backed catalog for larger or separately maintained rule sets:

```yaml
config:
  engine: regex
  pattern_catalog: catalogs/identifiers.yaml
```

The path:

- is relative to Privacy Guard's working directory
- must end in `.yaml` or `.yml`
- cannot be absolute
- cannot contain path traversal
- cannot resolve through a symlink

Catalog YAML must be UTF-8 and cannot contain aliases, duplicate keys, or unsafe
tags. File-backed and inline catalogs normalize to the same immutable model.

## Detection behavior

`RegexEngine` evaluates each rule independently. It retains overlapping matches
within one rule and across different rules. Each detection records:

- entity name
- start and end offsets in the stage input
- configured confidence
- rule identity as internal metadata

The service aggregates findings by stage, entity, and confidence. Matched text,
offsets, patterns, and rule metadata are not serialized to OpenShell findings.

Patterns must consume at least one character. A pattern that matches empty input
during validation is rejected. A context-dependent zero-width match discovered
during evaluation rejects the configuration for that request.

## Replacement behavior

Configure replacement for policies using `on_detection.action: replace`:

```yaml
replacement:
  strategy: template
  template: "[{entity}]"
```

The template may contain:

- literal text
- `{entity}`, replaced with the catalog entity name

Formatting conversions, format specifications, and other fields are rejected.
The default template is `[{entity}]`.

For overlapping detections, replacement selects non-overlapping winners in this
order:

1. higher confidence
2. longer span
3. earlier start offset
4. earlier end offset
5. entity name
6. rule identity

Selected replacements are applied from left to right. The engine calculates
the final UTF-8 size before allocating replacement output.

## Pattern design

Prefer patterns with explicit boundaries that do not consume neighboring text.
For example:

```yaml
pattern: '(?<![A-Za-z0-9])CUST-[0-9]{8}(?![A-Za-z0-9])'
```

For each rule, test:

- representative matches
- values that must not match
- adjacent punctuation and Unicode text
- multiline input when applicable
- overlap with other rules
- long non-matching input
- input designed to trigger expensive backtracking
- replacement output for every entity name

Do not treat a passing example as proof that a regex is safe for arbitrary
input. Privacy Guard enforces a shared timeout, but a slow pattern can consume
the complete request budget.

## Validate the installed schema

```bash
uv run privacy-guard engines
uv run privacy-guard configuration-schema
```

The built-in registry reports `regex` with `detect,replace`. The schema includes
`RegexEngineConfig`, `RegexPatternCatalog`, `RegexEntity`, `RegexRule`, and
`RegexReplacement`.

The complete runnable example is in
[`projects/privacy-guard/examples/regex-engine`](https://github.com/NVIDIA/OpenShell-Research/tree/main/projects/privacy-guard/examples/regex-engine).

## Limits

| Item | Limit |
| --- | ---: |
| Entities per catalog | 2,000 |
| Rules per catalog | 10,000 |
| Entity or supplied rule name | 128 ASCII bytes |
| Pattern string | 16 KiB |
| Catalog file | 16 MiB |
| Detections per stage | 256 |
| Compiled catalog cache | 128 entries and 32 MiB |

See [Limits and failure behavior](../reference/limits-and-failures.md) for
request-wide, transport, timeout, and result limits.

## Related pages

- [Configure policies](../configuration.md)
- [Request lifecycle](../architecture/request-lifecycle.md)
- [Add a custom engine](custom.md)
