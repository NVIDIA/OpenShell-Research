"""Tests for entity-processing engine registration and schema finalization."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

import pytest
from pydantic import field_validator

from privacy_guard.base import StrictDomainModel
from privacy_guard.engine_registry import EngineRegistry, EngineRegistryError
from privacy_guard.engines import (
    EngineConfig,
    EntityProcessingEngine,
    EntityProcessingStrategy,
    RegexEngine,
    TextProcessingResult,
)
from privacy_guard.errors import PrivacyGuardError
from privacy_guard.timeout import Timeout


class AcmeReplacement(StrictDomainModel):
    strategy: Literal["token"] = "token"


class AcmeConfig(EngineConfig[AcmeReplacement]):
    engine: Literal["acme-pii"] = "acme-pii"
    entities: tuple[str, ...]

    @field_validator("entities", mode="before")
    @classmethod
    def _entities_are_a_tuple(cls, value: object) -> object:
        if not isinstance(value, list | tuple):
            raise ValueError("entities must be a list")
        return tuple(value)


@dataclass(frozen=True)
class AcmeResources:
    prefix: str


class AcmeEngine(EntityProcessingEngine[AcmeConfig, AcmeResources]):
    supported_strategies = frozenset(
        {
            EntityProcessingStrategy.DETECT,
            EntityProcessingStrategy.REPLACE,
        }
    )

    def _run(
        self,
        text: str,
        *,
        strategy: EntityProcessingStrategy,
        timeout: Timeout,
    ) -> TextProcessingResult:
        del strategy, timeout
        return TextProcessingResult(text=text, detections=())


class DetectConfig(EngineConfig[AcmeReplacement]):
    engine: Literal["detect-only"] = "detect-only"


class DetectEngine(EntityProcessingEngine[DetectConfig, None]):
    supported_strategies = frozenset({EntityProcessingStrategy.DETECT})

    def _run(
        self,
        text: str,
        *,
        strategy: EntityProcessingStrategy,
        timeout: Timeout,
    ) -> TextProcessingResult:
        del strategy, timeout
        return TextProcessingResult(text=text, detections=())


def _acme_values(*, action: str = "detect") -> dict[str, object]:
    return {
        "entity_processing": {
            "stages": [
                {
                    "config": {
                        "engine": "acme-pii",
                        "entities": ["account"],
                        "replacement": {"strategy": "token"},
                    }
                }
            ]
        },
        "on_detection": {"action": action},
    }


def test_custom_engine_config_joins_the_exact_discriminated_union() -> None:
    resources = AcmeResources(prefix="token")
    registry = EngineRegistry()
    registry.register(RegexEngine)
    registry.register(AcmeEngine, resources=resources)
    registry.finalize()

    config = registry.validate_config(_acme_values(action="replace"))
    engine = registry.create_engine(config.entity_processing.stages[0].config)

    assert type(config.entity_processing.stages[0].config) is AcmeConfig
    assert type(engine) is AcmeEngine
    assert engine.config is config.entity_processing.stages[0].config
    assert engine.resources is resources
    assert registry.engine_names == ("regex", "acme-pii")


def test_detection_only_engine_is_rejected_for_replace_action() -> None:
    registry = EngineRegistry()
    registry.register(DetectEngine)
    registry.finalize()
    values = {
        "entity_processing": {"stages": [{"config": {"engine": "detect-only"}}]},
        "on_detection": {"action": "replace"},
    }

    with pytest.raises(PrivacyGuardError):
        registry.validate_config(values)


class ReplaceOnlyConfig(EngineConfig[AcmeReplacement]):
    engine: Literal["replace-only"] = "replace-only"


class ReplaceOnlyEngine(EntityProcessingEngine[ReplaceOnlyConfig, None]):
    supported_strategies = frozenset({EntityProcessingStrategy.REPLACE})

    def _run(
        self,
        text: str,
        *,
        strategy: EntityProcessingStrategy,
        timeout: Timeout,
    ) -> TextProcessingResult:
        del strategy, timeout
        return TextProcessingResult(text=text, detections=())


def test_replacement_only_engine_is_rejected_for_detect_action() -> None:
    registry = EngineRegistry()
    registry.register(ReplaceOnlyEngine)
    registry.finalize()
    values = {
        "entity_processing": {
            "stages": [
                {
                    "config": {
                        "engine": "replace-only",
                        "replacement": {"strategy": "token"},
                    }
                }
            ]
        },
        "on_detection": {"action": "detect"},
    }

    with pytest.raises(PrivacyGuardError):
        registry.validate_config(values)

    values["on_detection"] = {"action": "replace"}
    registry.validate_config(values)


def test_registry_is_frozen_after_finalize_and_finalize_is_idempotent() -> None:
    registry = EngineRegistry()
    registry.register(RegexEngine)
    first_type = registry.finalize()

    assert registry.finalize() is first_type
    with pytest.raises(EngineRegistryError):
        registry.register(DetectEngine)


def test_registry_rejects_duplicate_discriminators_and_resource_mismatch() -> None:
    registry = EngineRegistry()
    registry.register(AcmeEngine, resources=AcmeResources(prefix="token"))

    with pytest.raises(EngineRegistryError):
        registry.register(AcmeEngine, resources=AcmeResources(prefix="other"))
    with pytest.raises(EngineRegistryError):
        EngineRegistry().register(AcmeEngine)
    with pytest.raises(EngineRegistryError):
        EngineRegistry().register(DetectEngine, resources=object())


def test_describe_does_not_construct_an_engine() -> None:
    class CountingEngine(EntityProcessingEngine[DetectConfig, None]):
        supported_strategies = frozenset({EntityProcessingStrategy.DETECT})
        initialized = 0

        def _initialize(self) -> None:
            type(self).initialized += 1

        def _run(
            self,
            text: str,
            *,
            strategy: EntityProcessingStrategy,
            timeout: Timeout,
        ) -> TextProcessingResult:
            del strategy, timeout
            return TextProcessingResult(text=text, detections=())

    registry = EngineRegistry()
    registry.register(CountingEngine)
    registry.finalize()

    descriptions = registry.describe_engines()

    assert CountingEngine.initialized == 0
    assert descriptions[0].engine == "detect-only"
    assert descriptions[0].supported_strategies == frozenset(
        {EntityProcessingStrategy.DETECT}
    )
    properties_value = descriptions[0].configuration_schema["properties"]
    assert isinstance(properties_value, Mapping)
    properties = {
        key: value for key, value in properties_value.items() if isinstance(key, str)
    }
    engine_value = properties["engine"]
    assert isinstance(engine_value, Mapping)
    engine = {key: value for key, value in engine_value.items() if isinstance(key, str)}
    assert engine["const"] == "detect-only"


def test_registry_requires_at_least_one_engine() -> None:
    with pytest.raises(EngineRegistryError):
        EngineRegistry().finalize()
