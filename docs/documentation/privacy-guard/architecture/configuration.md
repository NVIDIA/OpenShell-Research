---
title: Configuration and text boundary
description: Runtime configuration ownership and the single-text processing contract.
agent_markdown: true
---

# Configuration and text boundary

Privacy Guard processes the complete request body as one text string. Its
runtime configuration is supplied by OpenShell for each evaluation and
normalized into strict structured models before processing.

## Bytes and text

The service owns the transport boundary:

- it validates the advertised 4 MiB request-body limit
- it allows an empty body without invoking an engine
- it decodes each non-empty body as strict UTF-8
- it passes exactly one `str` to `RequestProcessor`
- detect and block return no body mutation
- replace UTF-8 encodes the final processed text

Because detect and block return no mutation, OpenShell retains the exact
original bytes. Replace returns the final text even when it happens to equal
the input.

Headers and media types do not change this behavior. Privacy Guard does not
parse JSON, select nested values, create document regions, or reconstruct
structured payloads.

## Policy configuration

The OpenShell policy owns:

- ordered entity-processing stages
- each stage's exact engine configuration
- entity definitions and detection settings
- engine-specific replacement recipes
- the final detect, block, or replace action

For example:

```yaml
entity_processing:
  stages:
    - name: identifiers
      config:
        engine: regex
        pattern_catalog:
          entities:
            - name: customer-id
              rules:
                - name: prefixed-eight-digit-id
                  pattern: '\bCUST-[0-9]{8}\b'
                  confidence: high
        replacement:
          strategy: template
          template: "[{entity}]"
on_detection:
  action: replace
```

`config.engine` is the Pydantic discriminator. `EngineRegistry.finalize()`
builds the complete policy model from the exact config type registered for each
engine. Engine-specific fields therefore validate and serialize without a
generic mapping layer.

Deployment startup owns operational resources rather than privacy behavior:
installed engine implementations, approved model profiles, clients, endpoints,
credentials, and data-egress constraints.

## Regex catalogs

`RegexEngineConfig.pattern_catalog` accepts either a structured
`RegexPatternCatalog` or a relative `.yaml` or `.yml` path:

```yaml
pattern_catalog: patterns.yaml
```

Paths resolve beneath Privacy Guard's working directory. Absolute paths,
traversal, and symlinks are rejected. Catalog files must be bounded UTF-8 YAML
without aliases, duplicate keys, or unsafe tags.

File-backed and inline inputs normalize to the same `RegexPatternCatalog`.
The complete validated configuration contains the structured catalog rather
than its source path. File metadata keys a bounded parser cache; the normalized
immutable catalog keys a bounded compiled-rule cache. A content change produces
a different validated configuration and causes the next evaluation to prepare
an active-policy replacement.

Privacy Guard maintains the catalog schema and safety limits but does not ship
an authoritative pattern set. Repository catalogs are examples to copy and
adapt, not presets or runtime defaults.

`ValidateConfig` proves that each Regex pattern has valid syntax and rejects
patterns that immediately match empty input. It cannot prove that every
context-dependent branch consumes text. If a boundary, lookaround, or
alternation produces a zero-width match only when triggering request text is
observed, evaluation rejects the policy as `config_invalid` rather than treating
the result as an internal engine failure. Lookarounds and boundaries remain
valid when the complete match consumes text.

## Current transport constraint

The copied OpenShell protocol carries policy configuration in a
per-evaluation `google.protobuf.Struct` limited to 64 KiB. An inline catalog is
bounded by that transport. A file-backed configuration carries only its bounded
relative path through the protocol and loads the deployment-mounted catalog in
the middleware process.

The service rejects an encoded configuration above that limit before protobuf
conversion or policy validation. A policy may contain at most ten ordered
entity-processing stages. The service validates the complete bounded
configuration and compares it with the single active policy. Equal validated
configurations reuse the active processor; a different valid configuration pays
full engine and processor preparation before atomic replacement. Reuse avoids
repeated engine initialization but does not increase either limit.

`Struct` carries every number as a double. At this transport boundary only,
Privacy Guard converts finite integral values in the safe integer range
`-(2^53 - 1)` through `2^53 - 1` to Python integers before strict policy
validation. Non-integral and out-of-range values remain floats, so integer
fields reject them. Direct Python registry validation remains strict and does
not perform this transport normalization. Custom-engine integer settings must
therefore remain within the safe range when supplied through OpenShell policy.

A future self-contained transport for larger expanded catalogs requires an
upstream OpenShell contract for preparing configuration and referring to it
during evaluation. Privacy Guard must not create a private protocol fork.

## Policy identity

Privacy Guard compares the complete immutable validated configuration,
including every concrete engine field, nested replacement variant, and expanded
Regex catalog. Equal configurations reuse the active processor. A different
configuration is an update candidate and replaces the active policy only after
complete preparation succeeds.

The current protocol carries neither an explicit update operation nor a policy
version. Privacy Guard therefore cannot distinguish an intentional user update
from any other changed per-evaluation configuration. OpenShell must send one
consistent policy stream to a process; interleaved old and new configurations
can repeatedly replace one another. A future protocol can make preparation and
activation explicit and let evaluations refer to a versioned policy.

[Back to the architecture overview](index.md)
