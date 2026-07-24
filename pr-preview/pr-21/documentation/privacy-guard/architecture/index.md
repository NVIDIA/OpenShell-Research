---
title: Privacy Guard architecture
description: System structure, component boundaries, and request flow for Privacy Guard.
agent_markdown: true
---

# Privacy Guard architecture

Privacy Guard is OpenShell middleware that detects and optionally replaces
sensitive entities in provider-bound HTTP request text before OpenShell adds
provider credentials.

This architecture is a clean break from the earlier implementation. A processor
run receives one UTF-8 text value and invokes an ordered pipeline of
entity-processing engines. Structured-body parsing and compatibility with the
previous extension and policy APIs are intentionally out of scope.

Privacy Guard can allow the original body, allow a replacement body, or deny
the request. It never sends the provider request itself.

## Where Privacy Guard runs

```text
Sandbox process
      |
      | provider-bound HTTP request
      v
OpenShell supervisor
      |
      | gRPC: SupervisorMiddleware
      v
Privacy Guard
      |
      | allow original body, allow replacement body, or deny
      v
OpenShell supervisor
      |
      | credentials added only after middleware allows
      v
Provider
```

Privacy Guard implements the OpenShell-owned `SupervisorMiddleware` gRPC
service and advertises one HTTP-request binding in the pre-credentials phase.
The checked-in protocol and generated bindings are canonical copies from
OpenShell and must not be edited locally.

## Component boundaries

Source paths on these pages are relative to
`projects/privacy-guard/src/privacy_guard/`.

- `service/` owns gRPC, protobuf conversion, UTF-8 decoding and encoding,
  bounded worker scheduling, processor caching, and finding serialization.
  Outside generated `bindings/`, no other package imports gRPC or generated
  bindings.
- `request_processor.py` runs configured stages over one text value, shares one
  timeout across them, aggregates detections, and applies the user-facing
  policy action. It does not import gRPC or implement an engine's algorithms.
- `engines/` defines the custom-engine contract and the built-in Regex
  implementation. Each engine owns its detection and replacement algorithms.
- `engine_registry.py` registers engine implementations and operator-owned
  resources, builds the exact Pydantic discriminated union, validates
  configurations, and constructs configured engines.
- `config.py` defines ordered stages, the required policy action, canonical
  configuration serialization, and fingerprints.
- top-level `base.py` defines the package-wide strict immutable domain-model
  base.
- `string_validators.py` defines shared string validators and field types.

The OpenShell policy is the single source of privacy behavior: stage order,
each stage's exact engine configuration, entity definitions, replacement
recipes, and the final `detect`, `block`, or `replace` action. Deployment
configuration registers implementations and injects operational resources such
as model profiles, endpoints, clients, and credentials.

## Request flow

```text
HttpRequestEvaluation protobuf
        |
        v
PrivacyGuardMiddleware
  validates phase and body size
  validates expanded policy configuration
  resolves or builds a cached RequestProcessor
  decodes a non-empty body as strict UTF-8
        |
        v
RequestProcessor.process(text)
  derives DETECT or REPLACE engine strategy
  creates one shared Timeout
        |
        v
stage 1 engine.run(current text)
        |
        v
stage 2 engine.run(stage 1 text)
        |
       ...
        |
        v
RequestProcessor
  aggregates stage-qualified detections
  applies detect, block, or replace
        |
        v
RequestProcessingResult
        |
        v
PrivacyGuardMiddleware
  serializes bounded findings
  encodes replacement text only for replace
        |
        v
HttpRequestResult protobuf
```

The processor passes only `EntityProcessingStrategy.DETECT` or
`EntityProcessingStrategy.REPLACE` to engines. Blocking is a request
disposition and never crosses the engine boundary.

The processor is synchronous. The service runs it in a dedicated thread pool
so engine work does not block the gRPC event loop.

## Core data types

| Type | Meaning |
| --- | --- |
| `PrivacyGuardConfig` | Ordered entity-processing stages and the required action on detection |
| `EntityProcessingStage` | One configured engine invocation with an optional diagnostic name |
| `EngineConfig` | Nominal strict base for an engine's exact policy configuration |
| `EntityProcessingStrategy` | Per-run engine selection: detect or replace |
| `EntityDetection` | One occurrence with stage-input offsets and optional confidence |
| `TextProcessingResult` | One engine's authoritative output text and detections |
| `Timeout` | One monotonic deadline shared across all stages |
| `EntityDetectionSummary` | Bounded stage/entity/confidence aggregate for audit output |
| `RequestProcessingResult` | Allow or deny decision, detection summaries, and replacement text when requested |

Pydantic domain models are strict, frozen, reject unknown fields, hide rejected
input from validation errors, and suppress sensitive fields from normal
representations.

## Deliberate omissions

- Cross-request entity memory is not part of v0.
- Engines do not receive transport metadata or the user-facing policy action.
- There is no parallel execution-plan model; preparation constructs a
  `RequestProcessor` directly.
- There is no generic replacement field or replacement-strategy enum. Each
  engine owns any replacement settings appropriate to its underlying
  algorithm.
- Runtime policy models do not accept catalog paths. Transparent file expansion
  requires an upstream OpenShell policy-authoring feature.

## Read next

- [Request lifecycle](request-lifecycle.md) explains configuration resolution,
  ordered execution, actions, and output behavior.
- [Entity-processing engines](entity-processing-engines.md) defines the
  extension contract and built-in engines.
- [Configuration and text boundary](configuration.md) covers the one-text
  contract, configuration ownership, and current catalog limits.
- [Service boundary](service-boundary.md) covers gRPC adaptation, caching, and
  concurrency.
- [Safety and limits](safety-and-limits.md) records failure behavior and
  resource bounds.
