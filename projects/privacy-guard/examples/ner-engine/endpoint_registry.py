"""Registry factory for the explicit ``POST /v1/extract`` NER endpoint."""

from __future__ import annotations

import os

from privacy_guard.engines import NERExtractEndpointModel, NERResources
from privacy_guard.engines.registry import EngineRegistry


def create_registry() -> EngineRegistry:
    """Create a built-in registry backed by the configured NER endpoint."""
    endpoint = _required_environment("PRIVACY_GUARD_NER_ENDPOINT")
    model = _required_environment("PRIVACY_GUARD_NER_MODEL")
    resources = NERResources(
        model=NERExtractEndpointModel(
            endpoint=endpoint,
            model=model,
            chunk_length=_environment_integer(
                "PRIVACY_GUARD_NER_CHUNK_LENGTH",
                default=384,
            ),
            overlap=_environment_integer(
                "PRIVACY_GUARD_NER_CHUNK_OVERLAP",
                default=128,
            ),
        )
    )
    return EngineRegistry(
        include_builtin_engines=True,
        ner_resources=resources,
    ).finalize()


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value:
        raise RuntimeError(f"Required environment variable {name} is not set")
    return value


def _environment_integer(name: str, *, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        raise RuntimeError(f"Environment variable {name} must be an integer") from None
