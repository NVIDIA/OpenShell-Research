---
title: Add a custom engine
description: Implement, register, run, and test a typed Egress Gate entity-processing engine.
agent_markdown: true
---

# Add a custom engine

A custom engine integrates another detector or replacement tool with Privacy
Guard. It receives one text string, an invocation strategy, a shared timeout,
and validated engine-specific configuration. It returns processed text and
bounded detections.

Custom engine code runs inside the Egress Gate process and can access request
text. Install only reviewed, trusted implementations.

## Engine contract

A custom engine defines:

1. a concrete `EngineConfig`
2. optional typed `EngineResources`
3. supported invocation strategies
4. optional immutable initialization in `_initialize()`
5. request processing in `_run()`

Do not override `__init__()` or the public `run()` method. The framework-owned
wrapper validates strategy support, timeouts, detection spans, detection
cardinality, output size, and mutation behavior.

## Minimal detection engine

```python
import re
from typing import Literal

from pydantic import Field

from egress_gate.engines import (
    EngineConfig,
    EntityDetection,
    EntityProcessingEngine,
    EntityProcessingStrategy,
    TextProcessingResult,
)
from egress_gate.timeout import Timeout


class KeywordEngineConfig(EngineConfig):
    engine: Literal["keyword"] = "keyword"
    keyword: str = Field(min_length=1, max_length=256)


class KeywordEngine(EntityProcessingEngine[KeywordEngineConfig]):
    supported_strategies = frozenset(
        {EntityProcessingStrategy.DETECT}
    )

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

`TextProcessingResult.from_detections()` stops consuming a lazy detection
stream when the per-stage limit is exceeded. The public engine wrapper remains
the enforcement boundary.

## Configuration

Each config class must:

- subclass `EngineConfig`
- declare one literal `engine` discriminator
- use strict typed fields for all policy-owned behavior
- reject unknown fields through the shared base model
- keep sensitive values out of normal representations when applicable

```python
class AcmeEngineConfig(EngineConfig):
    engine: Literal["acme-pii"] = "acme-pii"
    model_profile: str
    replacement: AcmeReplacement | None = None
```

Egress Gate adds the exact config type to the registry-built Pydantic
discriminated union. The policy object is passed unchanged to the engine.

OpenShell transports numbers through protobuf `Struct`. Integer settings must
fit the safe range `-(2^53 - 1)` through `2^53 - 1`.

## Operational resources

Use `EngineResources` for deployment-owned clients, adapters, endpoints,
credential providers, or preloaded models:

```python
from dataclasses import dataclass

from egress_gate.engines import EngineResources


@dataclass(frozen=True)
class AcmeResources(EngineResources):
    client: AcmeClient


class AcmeEngine(
    EntityProcessingEngine[AcmeEngineConfig, AcmeResources]
):
    ...
```

Resources must:

- contain operational dependencies, not policy behavior
- retain no request text or per-request state
- be safe for concurrent use
- be created before request processing

A resource-free engine omits the second generic argument.

## Supported strategies

Declare the exact operations exposed by the engine:

```python
supported_strategies = frozenset(
    {
        EntityProcessingStrategy.DETECT,
        EntityProcessingStrategy.REPLACE,
    }
)
```

`block` is not an engine strategy. The processor invokes `DETECT` and applies
the block decision after successful engine execution.

Override `_validate_run_config()` when a strategy requires additional
configuration. For example, a replacement engine can require a replacement
recipe only when invoked with `REPLACE`.

## Result requirements

Return `TextProcessingResult` with:

| Field | Requirement |
| --- | --- |
| `text` | Complete authoritative stage output |
| `detections` | Every bounded occurrence produced by the stage |

Each `EntityDetection` provides:

| Field | Requirement |
| --- | --- |
| `entity` | Stable declared identifier, never a value derived from request text |
| `start` | Inclusive Unicode code-point offset in stage input |
| `end` | Exclusive non-empty offset in stage input |
| `confidence` | Optional `low`, `medium`, or `high` |
| `metadata` | Optional bounded internal attribution |

For `DETECT`, returned text must exactly equal input text. For `REPLACE`, text
may change only when the result contains at least one detection. Do not return
partial text or detections after a collaborator failure.

## Timeouts

One `Timeout` is shared across the complete stage pipeline. Pass its remaining
duration to APIs that accept a timeout:

```python
result = client.process(
    text,
    timeout=timeout.remaining_seconds(),
)
```

Translate Python `TimeoutError` with the shared context manager:

```python
with timeout.enforce():
    result = client.process(
        text,
        timeout=timeout.remaining_seconds(),
    )
```

Long-running local loops may call `timeout.raise_if_expired()`. Document and
bound operations that cannot be interrupted.

## Concurrency

One configured engine instance may process requests concurrently. Keep request
text, detections, counters, and temporary objects local to `_run()`. Treat
configuration and derived initialization state as immutable. Ensure injected
clients and resources support concurrent calls.

## Errors and logging

Translate expected collaborator failures into Egress Gate's content-safe
engine exceptions. Do not include:

- input or replacement text
- matched values or surrounding text
- credentials or endpoints
- raw exception messages
- model or SDK response bodies

Stable engine and entity identifiers may appear in findings and diagnostic
logs when they satisfy shared validation.

Use a static, content-safe message when translating an operational failure:

```python
from egress_gate.engines import EngineExecutionError

try:
    result = self.resources.client.process(
        text,
        timeout=timeout.remaining_seconds(),
    )
except AcmeClientError:
    raise EngineExecutionError("Acme processing failed") from None
```

| Exception | Use in a custom engine |
| --- | --- |
| `EngineConfigurationError` | Strategy-specific configuration is unusable |
| `EngineExecutionError` | A collaborator or runtime operation failed |
| `EngineLimitExceededError` | Engine-owned bounded work or output exceeded its limit |

The framework raises `EngineContractError` when returned text or detections
violate the engine contract; custom engines should not use it for collaborator
failures.

## Register the engine

Create one application-scoped registry factory:

```python
from egress_gate.engines.registry import EngineRegistry


def create_registry() -> EngineRegistry:
    registry = EngineRegistry(include_builtin_engines=True)
    registry.register(KeywordEngine)
    return registry.finalize()
```

Pass resources during registration when required:

```python
registry.register(
    AcmeEngine,
    resources=AcmeResources(client=client),
)
```

Use `include_builtin_engines=True` to add the built-in `RegexEngine`. Omit it
when the registry should contain only explicitly registered custom engines.

## Inspect and run the registry

```bash
uv run egress-gate \
  --registry-factory my_engines:create_registry \
  engines

uv run egress-gate \
  --registry-factory my_engines:create_registry \
  configuration-schema

uv run egress-gate \
  --registry-factory my_engines:create_registry \
  serve \
  --listen 0.0.0.0:50051
```

The module must be installed or available on `PYTHONPATH`. The factory is
trusted deployment code and executes for each CLI invocation.

The complete runnable example is in
[`projects/egress-gate/examples/custom-engine`](https://github.com/NVIDIA/OpenShell-Research/tree/main/projects/egress-gate/examples/custom-engine).

## Verify the integration

Before deploying a custom engine:

1. Run its unit tests directly against `run()` for every supported strategy.
   Assert the exact returned text, entity identifiers, spans, and confidence.
   Include Unicode input when offsets come from another library.
2. Run `engines` and `configuration-schema` with the registry factory. Confirm
   that the engine and its policy fields appear.
3. Send a representative request through a running Egress Gate service with
   the deployment policy. Confirm the OpenShell decision, replacement body, and
   findings.
4. Force collaborator timeouts and failures. Confirm that the request fails
   without partial output and that responses and logs contain no request text,
   credentials, or raw collaborator errors.

Test engine-specific behavior and integrations. Egress Gate's own suite
covers the shared wrapper contract, processor ordering, request-wide limits,
and gRPC result mapping.

## Related pages

- [Configure policies](../configuration.md)
- [Run and operate Egress Gate](../operations.md)
- [System architecture](../architecture/index.md)
- [Limits and failure behavior](../reference/limits-and-failures.md)
