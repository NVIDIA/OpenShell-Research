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
`@override` is not required.

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
| `detections` | Tuple of all `EntityDetection` occurrences |

Each `EntityDetection` contains:

| Field | Meaning |
| --- | --- |
| `entity` | Bounded entity label |
| `start` | Inclusive Unicode code-point offset in the stage input |
| `end` | Exclusive Unicode code-point offset in the stage input |
| `confidence` | Optional `low`, `medium`, `high`, or strict value from 0 through 1 |
| `metadata` | Optional bounded engine-specific attribution retained inside the processing boundary |

A detection span is non-empty and must fall within the stage input. The public
wrapper also enforces stage detection and output-size limits.

For `DETECT`, output text must exactly equal input text. For `REPLACE`, a
successful return is the engine's authoritative completed result. Text may not
change without at least one detection. Engines must raise on native partial
failure instead of returning partial output.

Confidence remains in the representation supplied by the engine. Privacy Guard
does not invent numeric values for categorical confidence or compare confidence
across engines.

## Custom engine example

```python
from typing import Literal

from pydantic import Field

from privacy_guard.engines import (
    EngineConfig,
    EngineConfigurationError,
    EntityDetection,
    EntityProcessingEngine,
    EntityProcessingStrategy,
    TextProcessingResult,
)
from privacy_guard.timeout import Timeout
from privacy_guard.base import StrictDomainModel


class KeywordReplacement(StrictDomainModel):
    strategy: Literal["token"] = "token"
    token: str = "[keyword]"


class KeywordEngineConfig(EngineConfig):
    engine: Literal["keyword"] = "keyword"
    keyword: str = Field(min_length=1)
    replacement: KeywordReplacement | None = None


class KeywordEngine(EntityProcessingEngine[KeywordEngineConfig]):
    supported_strategies = frozenset(
        {
            EntityProcessingStrategy.DETECT,
            EntityProcessingStrategy.REPLACE,
        }
    )

    @classmethod
    def _validate_run_config(
        cls,
        config: KeywordEngineConfig,
        resources: None,
        *,
        strategy: EntityProcessingStrategy,
    ) -> None:
        if (
            strategy is EntityProcessingStrategy.REPLACE
            and config.replacement is None
        ):
            raise EngineConfigurationError(
                "keyword replacement configuration is required"
            )

    def _run(
        self,
        text: str,
        *,
        strategy: EntityProcessingStrategy,
        timeout: Timeout,
    ) -> TextProcessingResult:
        timeout.raise_if_expired()
        start = text.find(self.config.keyword)
        if start < 0:
            return TextProcessingResult(text=text, detections=())

        end = start + len(self.config.keyword)
        detection = EntityDetection(
            entity="keyword",
            start=start,
            end=end,
        )
        if strategy is EntityProcessingStrategy.DETECT:
            output = text
        else:
            replacement = self.config.replacement
            if replacement is None:
                raise EngineConfigurationError(
                    "keyword replacement configuration is required"
                )
            output = text[:start] + replacement.token + text[end:]
        return TextProcessingResult(text=output, detections=(detection,))
```

Register the implementation and, when required, its resources before finalizing
the registry:

```python
registry = EngineRegistry()
registry.register(KeywordEngine)
registry = registry.finalize()
```

Finalization freezes registration and constructs the exact policy config type,
JSON Schema, and engine discovery metadata.

The complete example at
`projects/privacy-guard/examples/custom-engine/README.md` adapts an
operator-provided analysis tool, injects it as a typed resource, and runs its
finalized registry through discovery, schema generation, serving, and an
OpenShell policy.

## Timeout and concurrency

One `Timeout` is created for the processor run and passed through every stage.
An engine must not create a fresh per-stage duration. It should call:

```python
timeout.raise_if_expired()
remaining = timeout.remaining_seconds()
```

When a delegated API accepts a timeout, pass the remaining duration. Operations
that cannot be interrupted must be documented and bounded independently.

Engine configuration, derived state, and injected resources are shared across
concurrent requests. Keep request text, detections, and counters local to
`_run()`. Resources and engines must be concurrency-safe.

## RegexEngine

`RegexEngine` owns both regular-expression detection and deterministic template
replacement. Its `RegexPatternCatalog` contains structured entities and ordered
patterns; Privacy Guard maintains the schema and limits but no authoritative
pattern set.

Important properties:

- patterns compile once during configuration validation and initialization
- a non-capturing wrapper plus a private trailing named marker preserves
  numeric backreferences and proves the configured match completed
- user-defined named groups and inline flags are rejected
- `ignore_case`, `multiline`, `dot_all`, and `ascii` are explicit fields
- detection retains overlapping matches within and across patterns
- each backend search receives the shared remaining timeout
- pattern names are optional; unnamed patterns receive deterministic
  diagnostic identities without changing serialized configuration
- replacement resolves overlaps by categorical confidence, span length,
  offsets, entity, and pattern identity
- templates allow literal text and `{entity}` only
- replacement size is projected before output allocation

The third-party `regex` backend is used because it can interrupt an individual
search when the timeout expires.

## Tool-specific custom engines

Tool integrations belong in custom engines until they have a complete,
production-backed implementation. Privacy Guard does not ship placeholder
engine types or runtime protocols that merely resemble a third-party tool.

The first NeMo Anonymizer integration will be a custom engine. Its configuration
and replacement types should preserve Anonymizer's native concepts, while the
engine itself owns all translation to and from the actual Anonymizer SDK.

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
- categorical and numeric confidence
- output and detection limits
- timeout propagation and expiration
- deterministic ordering where relevant
- concurrent calls over immutable initialized state
- content-safe errors that omit input and engine secrets

Processor tests should cover only ordered multi-stage behavior, shared timeout,
policy disposition, aggregate limits, and stage-qualified findings.

[Back to the architecture overview](index.md)
