---
title: Entity-processing engines
description: Engine configuration, lifecycle, output contract, and extension rules.
agent_markdown: true
---

# Entity-processing engines

An `EntityProcessingEngine` detects sensitive entities in one text string and
may replace them when requested. It has no access to the user-facing policy
action, request metadata, gRPC, or protobuf messages.

Each configured stage owns one engine instance. The same instance may serve
concurrent requests, while separate stages may use the same implementation
with different configurations.

## Engine interface

An engine:

1. declares a concrete configuration type and, when needed, a resource type
   through generics
2. declares its non-empty set of `supported_strategies`
3. receives validated configuration and operator-owned resources through the
   base constructor
4. optionally derives immutable reusable state in `_initialize()`
5. implements `_run(text, strategy, timeout)`
6. returns one `TextProcessingResult`

The public `run()` method validates input, strategy support, the timeout, and
the complete output contract. Custom engines implement `_run()` and do not
override `run()` or define `__init__`. `_initialize()` is optional, and
`@override` is not required. The base constructor and public wrapper are final
lifecycle methods. Registration rejects direct or inherited overrides and
directs custom engines to `_initialize()` and `_run()` instead.

## Configuration

Every concrete config subclasses `EngineConfig` and declares exactly one
literal `engine` discriminator:

```python
class AcmeEngineConfig(EngineConfig):
    engine: Literal["acme-pii"] = "acme-pii"
    replacement: AcmeReplacement | None = None
```

The object under `EntityProcessingStage.config` is this exact concrete model.
It is validated and serialized as a member of the registry-built Pydantic
discriminated union, then passed unchanged to the engine constructor.

Custom integer fields remain strict for direct Python callers. OpenShell policy
uses protobuf `Struct`, whose numeric representation is a double, so the service
normalizes finite integral values only within the safe range `-(2^53 - 1)`
through `2^53 - 1`. Values outside that range and non-integral values remain
floats and fail strict integer fields. Nested models and lists follow the same
transport rule.

`EngineConfig` is a nominal, strict base model. It does not prescribe a
`replacement` field or any other algorithm-specific setting. An engine that
needs a replacement recipe declares that field on its concrete config. Engines
with multiple replacement algorithms may define a nested discriminated union,
using fields such as `replacement.strategy`. An engine whose underlying tool
has intrinsic replacement behavior may support `REPLACE` without declaring a
replacement field at all.

Configuration contains privacy behavior. An optional `EngineResources` object
contains operator-owned runtime dependencies such as initialized model clients,
SDK adapters, endpoints, credential providers, or approved model profiles.
Resources are registered by the operator and never serialized into policy.

`EngineResources` is a nominal contract. A concrete resource bundle subclasses
it, is typed as the engine's second generic argument, and must:

- contain operational dependencies rather than policy behavior
- retain no request text or mutable per-request state
- expose only dependencies that are safe for concurrent engine calls
- avoid relying on construction or mutation during request processing

For example:

```python
from dataclasses import dataclass

from privacy_guard.engines import EngineResources


@dataclass(frozen=True)
class AcmeResources(EngineResources):
    client: AcmeClient


class AcmeEngine(EntityProcessingEngine[AcmeEngineConfig, AcmeResources]):
    ...
```

Resources are optional. A resource-free engine omits the second generic
argument and receives `None` from the base class:

```python
class KeywordEngine(EntityProcessingEngine[KeywordEngineConfig]):
    ...
```

## Invocation strategy

```python
class EntityProcessingStrategy(StrEnum):
    DETECT = "detect"
    REPLACE = "replace"
```

An engine declares exactly the invocation strategies it supports. A
detection-only engine declares:

```python
supported_strategies = frozenset({EntityProcessingStrategy.DETECT})
```

A replacement-only engine declares:

```python
supported_strategies = frozenset({EntityProcessingStrategy.REPLACE})
```

An engine that exposes both operations includes both enum values. Supporting
`REPLACE` does not imply that the engine exposes `DETECT`, even when replacement
requires internal detection. Blocking does not appear here: the processor runs
engines with `DETECT` and applies the block disposition afterward.

The registry calls `validate_run_config()` with the strategy derived from the
policy action. The base implementation verifies strategy support. An engine
may add technique-specific requirements through `_validate_run_config()`, such
as requiring a template only when `REPLACE` is requested. The engine receives
the strategy, never the user-facing `PolicyAction`.

## Result contract

`TextProcessingResult` contains:

| Field | Meaning |
| --- | --- |
| `text` | Authoritative text returned by this stage |
| `detections` | Bounded tuple of all `EntityDetection` occurrences |

Each `EntityDetection` contains:

| Field | Meaning |
| --- | --- |
| `entity` | Bounded entity label |
| `start` | Inclusive Unicode code-point offset in the stage input |
| `end` | Exclusive Unicode code-point offset in the stage input |
| `confidence` | Optional `low`, `medium`, or `high` certainty |
| `metadata` | Optional bounded engine-specific attribution retained inside the processing boundary |

A detection span is non-empty and must fall within the stage input. The public
wrapper independently enforces stage detection and output-size limits for every
returned result. Engines that produce detections lazily should use
`TextProcessingResult.from_detections()`; this optional convenience stops
consuming the iterable as soon as the stage limit is exceeded, but it is not
the enforcement boundary.

For `DETECT`, output text must exactly equal input text. For `REPLACE`, a
successful return is the engine's authoritative completed result. Text may not
change without at least one detection. Engines must raise on native partial
failure instead of returning partial output.

Privacy Guard preserves categorical confidence as supplied by the engine and
does not compare confidence across engines.

## Custom engine example

```python
import re
from typing import Literal

from pydantic import Field

from privacy_guard.engines import (
    EngineConfig,
    EntityDetection,
    EntityProcessingEngine,
    EntityProcessingStrategy,
    TextProcessingResult,
)
from privacy_guard.timeout import Timeout


class KeywordEngineConfig(EngineConfig):
    engine: Literal["keyword"] = "keyword"
    keyword: str = Field(min_length=1)


class KeywordEngine(EntityProcessingEngine[KeywordEngineConfig]):
    supported_strategies = frozenset({EntityProcessingStrategy.DETECT})

    def _run(
        self,
        text: str,
        *,
        strategy: EntityProcessingStrategy,
        timeout: Timeout,
    ) -> TextProcessingResult:
        matches = re.finditer(re.escape(self.config.keyword), text)
        return TextProcessingResult.from_detections(
            text=text,
            detections=(
                EntityDetection(
                    entity="keyword",
                    start=match.start(),
                    end=match.end(),
                )
                for match in matches
            ),
        )
```

Register the implementation and, when required, its resources before finalizing
the registry:

```python
registry = EngineRegistry(include_builtin_engines=True)
registry.register(KeywordEngine)
registry = registry.finalize()
```

The opt-in constructor argument registers the engines shipped by Privacy Guard
before application-specific engines. Omit it when the registry should contain
only explicitly registered implementations.

Finalization freezes registration and constructs the exact policy config type,
JSON Schema, and engine discovery metadata.

The complete example at
`projects/privacy-guard/examples/custom-engine/README.md` keeps the engine,
configuration, and registry factory in one Python file and runs that registry
through discovery, schema generation, serving, and an OpenShell policy.

## Timeout and concurrency

One `Timeout` is created for the processor run and passed through every stage.
The public engine wrapper checks it immediately before and after `_run()`, so
ordinary in-memory implementations do not repeat those checks. An engine must
not create a fresh per-stage duration.

When a delegated API accepts a timeout, pass the remaining duration:

```python
remaining = timeout.remaining_seconds()
```

When that API raises Python's `TimeoutError` on expiration, use the shared
context manager to translate it into Privacy Guard's domain error:

```python
with timeout.enforce():
    result = client.process(text, timeout=timeout.remaining_seconds())
```

Unique long-running loops may also call `timeout.raise_if_expired()`.
Operations that cannot be interrupted must be documented and bounded
independently.

Engine configuration, derived state, and injected resources are shared across
concurrent requests. Keep request text, detections, and counters local to
`_run()`. Resources and engines must be concurrency-safe.

## RegexEngine

`RegexEngine` owns both regular-expression detection and deterministic template
replacement. Its `RegexPatternCatalog` contains structured entities and ordered
rules. Each rule combines one regex pattern with its optional diagnostic name,
confidence, and flags. Privacy Guard maintains the schema and limits but no
authoritative pattern set.

Important properties:

- rules compile once during configuration validation and initialization
- a non-capturing wrapper plus a private trailing named marker preserves
  numeric backreferences and proves the configured match completed
- user-defined named groups and inline flags are rejected
- `ignore_case`, `multiline`, `dot_all`, and `ascii` are explicit fields
- detection retains overlapping matches within and across rules
- each backend search receives the shared remaining timeout
- rule names are optional; unnamed rules receive deterministic
  diagnostic identities without changing serialized configuration
- catalogs may be supplied inline or through a bounded relative YAML path and
  normalize to the same structured configuration
- replacement resolves overlaps by categorical confidence, span length,
  offsets, entity, and rule identity
- templates allow literal text and `{entity}` only
- replacement size is projected before output allocation

The third-party `regex` backend is used because it can interrupt an individual
search when the timeout expires.

## NEREngine

`NEREngine` is a resource-backed built-in for general named-entity recognition.
Its policy configuration owns:

- an ordered non-empty list of printable labels
- a required finite threshold from zero through one
- `nested` or `flat` overlap behavior
- an optional template replacement allowing only literal text and `{entity}`

The deployment registers one immutable `NERResources` bundle containing a
provider-neutral `NERModel`. The facade has one synchronous
`predict_entities` operation. Endpoint URLs, model identifiers, chunk
configuration, device placement, and loaded model objects never enter policy.
The default built-in registry remains Regex-only; it registers NER only when
the operator explicitly supplies `ner_resources`.

The package provides two focused facades:

- `NERExtractEndpointModel` sends the exact `POST /v1/extract` JSON contract,
  bounds response bytes before decoding, performs no retries, and translates
  expected transport or schema failures without exposing content, endpoint
  details, or raw collaborator errors.
- `LocalNERModel` receives an already-loaded object without importing GLiNER,
  serializes access, processes long text in overlapping code-point windows,
  rebases offsets, and removes exact chunk-overlap duplicates.

The local facade's lock acquisition is bounded by the shared timeout. Once a
loaded model call starts, Python's worker-thread architecture cannot preempt
it; expiration is observed after that call returns.

The engine accepts only returned labels declared by policy, canonicalizes
case-only differences, rejects invalid spans and scores, retains the
highest-scoring exact duplicate, and sorts results deterministically. Raw
scores remain private to duplicate and replacement selection and do not become
finding confidence or metadata.

Nested detection may return overlapping findings. Replacement keeps every
finding but selects non-overlapping winners by score, span length, earlier
start, and configured label order. It projects UTF-8 output size before
allocating the replacement.

The extract endpoint contract is not the self-hosted Anonymizer GLiNER
`/v1/chat/completions` contract. Supporting that deployment requires a future
explicit facade with its own response validation and must not rely on protocol
autodetection. A future full Anonymizer engine remains separately configured
because it may own validation, augmentation, or rewriting beyond general NER.

## Tool-specific custom engines

Tool integrations belong in custom engines until they have a complete,
production-backed implementation. Privacy Guard does not ship placeholder
engine types or runtime protocols that merely resemble a third-party tool.

A future full NeMo Anonymizer integration will be a custom engine. Its
configuration and replacement types should preserve Anonymizer's native
concepts, while the engine itself owns translation to and from the actual
Anonymizer SDK.

## State

Cross-request entity memory is intentionally out of scope. The engine API has
no state argument, session identifier, persistent replacement map, or
placeholder storage interface.

Future memory support requires separate decisions about tenant isolation,
retention, deletion, consistency, and policy ownership.

## Testing an engine

Test engines without gRPC:

- valid and invalid exact configuration and resource types
- detection-only immutability
- replacement behavior and native partial failure
- Unicode offsets and invalid spans
- categorical confidence
- output and detection limits
- timeout propagation and expiration
- deterministic ordering where relevant
- concurrent calls over immutable initialized state
- content-safe errors that omit input and engine secrets

Processor tests should cover only ordered multi-stage behavior, shared timeout,
policy disposition, aggregate limits, and stage-qualified findings.

[Back to the architecture overview](index.md)
