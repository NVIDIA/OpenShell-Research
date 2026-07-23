---
title: Safety and limits
description: Validation ownership, bounded processing, failure behavior, and logging rules.
agent_markdown: true
---

# Safety and limits

Privacy Guard bounds input shape, scanning work, findings, replacement size, and
transport output. Limits are part of the architecture, not optional tuning
defaults.

Package-wide values live in `constants.py`.

## Validation ownership

Each layer validates facts it can establish:

| Layer | Validates |
| --- | --- |
| Policy and domain models | Strict field types, shape, and local field rules |
| Scanner public wrapper | Scanner return container and finding models |
| Format-handler public wrappers | Normalized body and reconstructed byte types |
| JSON handler | Early JSON nesting, text-block, and character limits |
| Processor | Registry consistency, original bytes, paths, finding identity, offsets, and authoritative request-wide limits |
| Service | Phase, transport body size, protobuf conversion, response size, and finding aggregation |

Keep validation at these owners. Early checks may bound allocation, while the
next trust boundary repeats authoritative checks for extension output. Do not
move request-wide checks into a scanner or scanner semantics into the service.

## Request limits

| Limit | Constant | Value | Owner |
| --- | --- | ---: | --- |
| Incoming request body | `MAX_BODY_BYTES` | 4 MiB | Service |
| Projected or reconstructed replacement body | `MAX_BODY_BYTES` | 4 MiB | Processor |
| Serialized replacement body | `MAX_BODY_BYTES` | 4 MiB | Service |
| gRPC receive message | `MAX_RECEIVE_MESSAGE_BYTES` | 5 MiB | gRPC server |
| JSON nesting | `MAX_JSON_NESTING` | 64 levels | JSON handler |
| Text blocks per request | `MAX_TEXT_BLOCKS` | 4,096 | JSON handler + processor |
| Characters scanned per request | `MAX_SCANNED_CHARACTERS` | 4,194,304 | JSON handler + processor |
| Findings returned by one scanner call | `MAX_FINDINGS_PER_BLOCK` | 256 | Scanner wrapper |
| Selected findings per text block | `MAX_FINDINGS_PER_BLOCK` | 256 | Processor |
| Selected findings per request | `MAX_FINDINGS_PER_REQUEST` | 4,096 | Processor |
| Default request scan budget | `DEFAULT_SCAN_TIMEOUT_SECONDS` | 1 second | Processor |
| Maximum configured scan budget | `MAX_SCAN_TIMEOUT_SECONDS` | 30 seconds | Processor |
| Active scanner workers | `MAX_CONCURRENT_SCANS` | 4 | Service |
| Concurrent gRPC calls | `MAX_CONCURRENT_RPCS` | 16 | gRPC server |

The gRPC receive limit includes 1 MiB beyond the body limit for the protobuf
envelope. The service still enforces the advertised 4 MiB body limit.

The JSON handler checks text-block and character limits while parsing. The
processor enforces them authoritatively for every handler.

The scan budget is shared across all scanners and text blocks in a request. It
is cooperative: a scanner that calls `remaining_seconds()` after the deadline
causes a limit deny. The processor cannot detect or interrupt an opaque scanner
operation that does not check the budget.

## Name, finding, and response limits

| Limit | Constant | Value |
| --- | --- | ---: |
| Entity, scanner, and metadata strings | `MAX_SCANNER_METADATA_BYTES` | 1,024 UTF-8 bytes |
| Metadata entries per finding | `MAX_FINDING_METADATA_ENTRIES` | 32 |
| Aggregated protobuf finding groups | `MAX_PROTO_FINDING_GROUPS` | 32 |
| Encoded size per protobuf finding | `MAX_PROTO_FINDING_BYTES` | 4 KiB |

The string limit covers policy entity filters, finding entities, finding scanner
names, and finding metadata keys and values.

The service aggregates findings before applying protobuf limits. If a safe
result cannot be represented, it returns a limit deny with no partial findings.

## Regex configuration limits

| Limit | Constant | Value |
| --- | --- | ---: |
| YAML file size | `MAX_SCANNER_CONFIG_BYTES` | 16 MiB |
| YAML nesting | `MAX_SCANNER_CONFIG_NESTING` | 16 |
| YAML nodes | `MAX_SCANNER_CONFIG_NODES` | 250,000 |
| YAML scalar | `MAX_SCANNER_CONFIG_SCALAR_BYTES` | 16 KiB |
| Name length | `MAX_REGEX_NAME_BYTES` | 128 ASCII bytes |
| Profiles | `MAX_REGEX_PROFILES` | 32 |
| Entities per profile | `MAX_REGEX_ENTITIES_PER_PROFILE` | 2,000 |
| Patterns per profile | `MAX_REGEX_PATTERNS_PER_PROFILE` | 10,000 |
| Entities across the file | `MAX_REGEX_ENTITIES_TOTAL` | 10,000 |
| Patterns across the file | `MAX_REGEX_PATTERNS_TOTAL` | 50,000 |
| Regex pattern | `MAX_REGEX_PATTERN_BYTES` | 16 KiB |
| Matches per pattern and text block | `MAX_MATCHES_PER_PATTERN` | 256 |

These catalog limits are separate from request finding limits. A large catalog
may still produce only a few findings for a request.

## When a limit is exceeded

Privacy Guard returns a successful deny result with
`privacy_guard_limit_exceeded` when:

- a scanner observes an expired scan budget
- findings exceed block or request limits
- projected redaction exceeds the body limit
- a reconstructed body exceeds the body limit
- aggregated findings exceed protobuf limits
- a deny reason code cannot be represented safely

The limit result contains no replacement body and no partial findings.

During evaluation, malformed input and internal failures instead abort the RPC
with a cataloged gRPC error. OpenShell then applies the middleware
registration's failure mode.

## Error catalog

Production failures use `PrivacyGuardError` and a stable `ErrorCode`. Each error
spec defines:

- whether the failure is invalid input or internal
- the responsible component
- the failed operation
- a content-safe summary
- a remediation hint

Caught extension exceptions are translated at their boundary. Their messages
and exception chains are not exposed.

Use a new catalog entry for a new externally observable failure. Do not return
raw parser, scanner, Pydantic, or protobuf exceptions.

## Logging

Default operational logs include:

- request ID
- duration
- action
- finding count
- stable error code
- phase timing and aggregate sizes at debug level

They do not include:

- request or replacement bodies
- text blocks
- matches
- paths
- headers or credentials
- scanner configuration
- arbitrary exception text

Sensitive model fields use `repr=False` to reduce accidental exposure. This is
not a substitute for safe exception handling.

`--debug` enables content-safe phase diagnostics. `--debug-log-content`
deliberately logs complete bodies and emits a warning; use it only in a
controlled development environment.

## Extension requirements

Scanner and format-handler extensions should:

- return the types declared by the extension hook
- keep request data in local variables or returned request models
- translate expected parse and configuration failures to `PrivacyGuardError`
- leave extension-output validation to the public wrapper
- check applicable limits before allocating output proportional to input or
  findings
- avoid logging input or caught exception text

Do not add retries, fallbacks, or extra validation without a concrete failure
mode and a clear owning layer.

## Changing a limit

A limit change may affect the middleware manifest, processor behavior, protobuf
serialization, tests, examples, and deployment configuration.

Before changing a limit:

1. identify every layer that enforces or advertises it
2. update exact-boundary tests
3. check failure behavior above the boundary
4. run `make check`
5. benchmark changes that affect request processing

[Back to the architecture overview](index.md)
