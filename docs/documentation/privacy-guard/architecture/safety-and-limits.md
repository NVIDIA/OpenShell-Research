---
title: Safety and limits
description: Validation ownership, bounded entity processing, failure behavior, and logging rules.
agent_markdown: true
---

# Safety and limits

Privacy Guard bounds request text, engine output, detections, regex execution,
transport output, and concurrency. Limits are part of the architecture, not
optional tuning defaults.

Package-wide values live in `constants.py`.

## Validation ownership

Each layer validates facts it can establish:

| Layer | Validates |
| --- | --- |
| Policy and engine config models | Strict types, exact fields, discriminators, local field rules, catalog bounds, and regex validity |
| `EngineRegistry` | Registration contracts, exact config and resource types, pure resource-backed config checks, and action/replacement compatibility |
| `EntityProcessingEngine.run()` | Text and invocation types, supported strategy, timeout, result model, spans, detection-only immutability, mutation attribution, and stage output bounds |
| Concrete engine | Algorithm-specific execution, replacement completeness, backend timeout propagation, and native failure normalization |
| `RequestProcessor` | Request text bounds, shared timeout, intermediate output, request-wide detection count, aggregation, and policy disposition |
| Service | Phase, transport body size, strict UTF-8, protobuf conversion, replacement encoding, finding representation, and response limits |

Keep request-wide policy decisions in the processor and algorithm-specific
behavior in the concrete engine. The service adapts transport but does not
reimplement either.

## Text, processing, and transport limits

| Limit | Constant | Value | Owner |
| --- | --- | ---: | --- |
| Incoming request body | `MAX_BODY_BYTES` | 4 MiB | Service |
| Input or intermediate text characters | `MAX_SCANNED_CHARACTERS` | 4,194,304 | Processor |
| Input or intermediate UTF-8 bytes | `MAX_BODY_BYTES` | 4 MiB | Processor |
| Engine output UTF-8 bytes | `MAX_BODY_BYTES` | 4 MiB | Engine wrapper |
| Encoded replacement body | `MAX_BODY_BYTES` | 4 MiB | Service |
| gRPC receive message | `MAX_RECEIVE_MESSAGE_BYTES` | 5 MiB | gRPC server |
| Detections returned by one stage | `MAX_DETECTIONS_PER_STAGE` | 256 | Engine wrapper |
| Detections across one request | `MAX_DETECTIONS_PER_REQUEST` | 4,096 | Processor |
| Default shared timeout | `DEFAULT_TIMEOUT_SECONDS` | 1 second | Processor |
| Maximum configured timeout | `MAX_TIMEOUT_SECONDS` | 30 seconds | Processor and `Timeout` |
| Active processor workers | `MAX_CONCURRENT_PROCESSING` | 4 | Service |
| Concurrent gRPC calls | `MAX_CONCURRENT_RPCS` | 16 | gRPC server |

The gRPC receive allowance is 1 MiB larger than the request body limit for the
protobuf envelope. The service still enforces the advertised 4 MiB body bound.

One monotonic `Timeout` is shared by all stages and final result validation.
`RegexEngine` passes the remaining duration into every backend search.
Third-party calls that accept a timeout should receive the same remaining
duration; calls that cannot be interrupted continue to occupy a service worker
until they exit.

## Diagnostic and result limits

| Limit | Constant | Value |
| --- | --- | ---: |
| Bounded diagnostic string | `MAX_DIAGNOSTIC_TEXT_BYTES` | 1,024 UTF-8 bytes |
| Metadata entries per detection | `MAX_FINDING_METADATA_ENTRIES` | 32 |
| Aggregated protobuf finding groups | `MAX_PROTO_FINDING_GROUPS` | 32 |
| Encoded size per protobuf finding | `MAX_PROTO_FINDING_BYTES` | 4 KiB |

The diagnostic-string bound applies to stage names, entity names, metadata
keys and values, model-profile names, and other audit-safe identifiers built
from the shared domain field type. Regex entity and supplied pattern names have a
stricter ASCII grammar and limit described below.

The processor aggregates occurrences before the service applies protobuf
limits. If a safe result cannot be represented, the service returns a limit
deny with no partial findings.

## Regex and catalog limits

| Limit | Constant | Value |
| --- | --- | ---: |
| Regex entity or supplied pattern name | `MAX_REGEX_NAME_BYTES` | 128 ASCII bytes |
| Entities per catalog | `MAX_REGEX_ENTITIES_PER_CATALOG` | 2,000 |
| Patterns per catalog | `MAX_REGEX_PATTERNS_PER_CATALOG` | 10,000 |
| Pattern string | `MAX_REGEX_PATTERN_BYTES` | 16 KiB |
| Matches per pattern | `MAX_MATCHES_PER_PATTERN` | 256 |

Entity and pattern names use
`[A-Za-z_][A-Za-z0-9_-]*`. Pattern names are optional; their deterministic
derived diagnostic identities are not serialized back into configuration.

The per-evaluation config `Struct` is limited to 64 KiB. A catalog may satisfy
Privacy Guard's engine limits yet remain too large for the current upstream
protocol. Internal caching does not raise that transport ceiling.

## Regex execution safety

`RegexEngine` validates and compiles the complete catalog before accepting
configuration. It:

- rejects invalid and empty pattern strings
- rejects patterns that match empty input
- rejects user-defined named groups and inline flags
- verifies its private trailing marker on every match
- treats a context-dependent zero-width match as an atomic configuration
  failure
- evaluates patterns independently to retain overlaps
- uses the timeout-capable third-party `regex` backend
- caps matches per pattern and detections per stage
- projects exact UTF-8 replacement size before rendering

No timeout, limit, or pattern failure returns partial stage detections or
mutated text.

## When a limit is exceeded

The processor returns a successful deny with
`privacy_guard_limit_exceeded` and no partial findings or replacement when:

- the shared timeout expires
- an engine exceeds its detection or output limit
- intermediate text exceeds the character or byte limit
- aggregate request detections exceed the limit

The service returns the same bounded deny when:

- findings exceed the protobuf group or encoded-size limit
- replacement text cannot be encoded within the body limit
- a deny reason code is not safely representable

Malformed input and engine contract or execution failures instead abort the RPC
with a cataloged error. OpenShell then applies the middleware registration's
failure behavior.

The distinction is deliberate:

- an exhausted declared processing bound is a closed, successful deny
- invalid user input is an `INVALID_ARGUMENT` RPC failure
- an engine or middleware defect is an `INTERNAL` RPC failure

## Error catalog

Production failures use `PrivacyGuardError` and a stable `ErrorCode`. Each
error spec defines:

- invalid-input or internal classification
- responsible component
- failed operation
- content-safe summary
- remediation hint

Caught extension and third-party exceptions are translated at their trust
boundary. Raw exception messages and chains are not exposed. Configuration
errors do not include rejected pattern strings, paths, credentials, provider
endpoints, or request text.

Use a new catalog entry for a genuinely new externally observable failure. Do
not return raw Pydantic, regex-backend, engine, or protobuf exceptions.

## Logging

Default operational logs include:

- request ID
- evaluation duration
- allow or deny action
- aggregate finding count
- stable error code
- stage source and invocation strategy at debug level

They do not include:

- request or replacement text
- matched or surrounding text
- offsets
- patterns or catalog contents
- headers, targets, or credentials
- model endpoints
- arbitrary exception text

Sensitive domain fields use `repr=False` to reduce accidental exposure. This
does not replace safe exception handling.

`--debug` enables content-safe processing diagnostics.
`--debug-log-content` deliberately logs complete input and processed text and
emits a warning; use it only in a controlled development environment.

## Extension requirements

Custom engines should:

- use the base constructor and public `run()` wrapper
- return the exact `TextProcessingResult` contract
- keep request data local to `_run()`
- keep initialized state immutable and make resources concurrency-safe
- pass the shared remaining timeout to delegated APIs
- fail the stage on native partial failure
- check applicable bounds before allocating output proportional to input or
  detections
- translate expected collaborator failures to the content-safe engine
  exception hierarchy
- avoid logging input or caught exception text

Do not add retries, fallback providers, persistent request artifacts, or extra
validation without a concrete failure mode and a clear owning layer.

## State and retention

Cross-request entity memory is not implemented. Privacy Guard retains validated
configuration and immutable engine state in its processor cache, but never
retains request text, detections, or replacement mappings there.

## Changing a limit

A limit change may affect policy schema, processor behavior, engine contracts,
protobuf serialization, tests, examples, and the OpenShell middleware
registration.

Before changing a limit:

1. identify every layer that enforces or advertises it
2. update exact-boundary tests
3. check the failure result immediately above the boundary
4. run the complete project validation
5. benchmark changes that affect regex compilation or request processing
6. coordinate upstream when the copied OpenShell protocol is involved

[Back to the architecture overview](index.md)
