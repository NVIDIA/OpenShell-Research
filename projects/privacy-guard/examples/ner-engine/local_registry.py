"""Registry factory for an already-downloaded local GLiNER model."""

from __future__ import annotations

import os
from importlib import import_module

from privacy_guard.engines import LocalNERModel, NERResources
from privacy_guard.engines.registry import EngineRegistry


def create_registry() -> EngineRegistry:
    """Load one local model at startup and create the built-in NER registry."""
    model_path = _required_environment("PRIVACY_GUARD_NER_MODEL_PATH")
    gliner_type = getattr(import_module("gliner"), "GLiNER")
    loaded_model = gliner_type.from_pretrained(model_path, local_files_only=True)
    resources = NERResources(
        model=LocalNERModel(
            model=loaded_model,
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
