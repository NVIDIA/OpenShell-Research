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
              patterns:
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
Serialization and canonical fingerprints contain the validated structured
catalog rather than its source path. File metadata keys a bounded parser cache;
the normalized immutable catalog keys a bounded compiled-rule cache. A content
change produces a newly validated configuration, compiled rule set, and
processor fingerprint.

Privacy Guard maintains the catalog schema and safety limits but does not ship
an authoritative pattern set. Repository catalogs are examples to copy and
adapt, not presets or runtime defaults.

## Current transport constraint

The copied OpenShell protocol carries policy configuration in a
per-evaluation `google.protobuf.Struct` limited to 64 KiB. An inline catalog is
bounded by that transport. A file-backed configuration carries only its bounded
relative path through the protocol and loads the deployment-mounted catalog in
the middleware process.

The service validates the complete configuration, computes its canonical
fingerprint, and uses a bounded internal `RequestProcessor` cache. Caching
avoids repeated engine initialization but does not increase the transport
limit.

A future self-contained transport for larger expanded catalogs requires an
upstream OpenShell contract for preparing configuration and referring to it
during evaluation. Privacy Guard must not create a private protocol fork.

## Configuration identity

Canonical serialization includes every concrete engine field and nested
replacement variant. Mapping keys are sorted, compact JSON encoding is used,
and the SHA-256 fingerprint is computed over the resulting UTF-8 bytes.

Equivalent structured configurations therefore share a processor cache entry.
Cache state is only an optimization; eviction or restart reconstructs the
processor from configuration supplied by a later evaluation.

[Back to the architecture overview](index.md)
