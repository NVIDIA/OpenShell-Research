# Privacy Guard

Privacy Guard is OpenShell middleware that runs an ordered pipeline of
entity-processing engines over one UTF-8 request body and applies a user-facing
detect, block, or replace action.

## Development commands

Run commands from `projects/privacy-guard/`.

- List targets: `make help`
- Run all checks: `make check`
- Check Python 3.11: `make check-py311`
- Run focused tests: `make test PYTEST_ARGS=tests/test_request_processor.py`

Run focused tests while working and `make check` before handoff.

## Engineering approach

- Backwards compatibility is explicitly not a concern for the v0 redesign. Do
  not restore legacy behavior, schemas, imports, names, tests, or examples.
- Add defensive handling only for a concrete failure mode at the layer that
  owns it. Avoid speculative guards, duplicate validation, broad catches,
  retries, and fallbacks.
- Prefer explicit, domain-specific names. Avoid generic intermediate
  abstractions that do not own behavior.
- Keep public declarations before private helper types, functions, methods, and
  constants when dependency ordering permits. Put private implementation
  details at the bottom of their module or class.

## Project map

- `src/privacy_guard/engines/`: engine contract and built-in implementations
- `src/privacy_guard/config.py`: policy action and ordered stage configuration
- `src/privacy_guard/engine_registry.py`: registration and finalized config union
- `src/privacy_guard/request_processor.py`: stage execution and policy disposition
- `src/privacy_guard/base.py`: package-wide strict immutable domain-model base
- `src/privacy_guard/string_validators.py`: shared string validators and field types
- `src/privacy_guard/service/`: gRPC lifecycle and protobuf adapter
- `src/privacy_guard/bindings/`: generated protobuf files; never hand-edit
- [`docs/architecture/`](docs/architecture/index.md): symlink to the canonical
  site sources under `../../docs/documentation/privacy-guard/architecture/`
- `tests/`: tests that mirror source boundaries
- `examples/`: copyable policy-authoring examples

Before changing `request_processor.py`, `engines/`, or `service/`, read the
architecture overview and matching topic page. Architecture changes follow
[`docs/development/index.md`](../../docs/development/index.md) and require its
checks.

## Design boundaries

- One processor call receives one text string. Do not reintroduce request-body
  codecs, format handlers, document regions, or JSON traversal.
- An `EntityProcessingEngine` receives engine configuration, a processing
  strategy, and a shared `Timeout`. It never receives or infers the policy
  action.
- `RequestProcessor` runs configured stages in order and owns detect, block, or
  replace disposition.
- Engine configuration lives inside the OpenShell policy as the exact Pydantic
  discriminated-union member registered for that engine.
- Deployment startup owns only installed engine implementations and operational
  resources such as clients, endpoints, models, and credentials.
- Engine instances and injected resources serve concurrent requests. Do not
  retain request content or mutable per-request state.
- Outside generated `bindings/`, only `service/` may import gRPC or generated
  bindings.
- The copied OpenShell `.proto` and generated bindings must be updated only
  through `openshell-middleware-kit`; never edit them manually.

## Extension pattern

Define a concrete `EngineConfig` and implement `_run`. Custom engines do not
define `__init__`; use optional `_initialize` for derived immutable state.
`@override` is not required.

```python
from typing import Literal

from privacy_guard.engines import (
    EngineConfig,
    EntityProcessingEngine,
    EntityProcessingStrategy,
    TextProcessingResult,
)
from privacy_guard.base import StrictDomainModel
from privacy_guard.timeout import Timeout


class KeywordReplacement(StrictDomainModel):
    strategy: Literal["token"] = "token"


class KeywordConfig(EngineConfig[KeywordReplacement]):
    engine: Literal["keyword"] = "keyword"
    keyword: str


class KeywordEngine(EntityProcessingEngine[KeywordConfig, None]):
    supported_strategies = frozenset({EntityProcessingStrategy.DETECT})

    def _run(
        self,
        text: str,
        *,
        strategy: EntityProcessingStrategy,
        timeout: Timeout,
    ) -> TextProcessingResult:
        timeout.raise_if_expired()
        return TextProcessingResult(text=text, detections=())
```

The public `run` method validates extension output. Register every engine before
finalizing the registry so policy serialization retains its exact config type.

## Change limits

- Add or update tests at the layer that owns the behavior.
- Ask before adding dependencies or changing the OpenShell protobuf contract,
  stable error codes, protocol limits, or fail-closed defaults.
- Do not remove or weaken relevant tests merely to pass checks.
- Do not add casts, explicit `Any`, blanket ignores, or broad type suppressions
  to handwritten code. `tests/test_typing_policy.py` enforces this.
