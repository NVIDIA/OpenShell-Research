"""Tests for entity-processing engine registration and schema finalization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pytest
from pydantic import field_validator

from privacy_guard.base import StrictDomainModel
from privacy_guard.engines import (
    EngineConfig,
    EngineConfigurationError,
    EngineResources,
    EntityProcessingEngine,
    EntityProcessingStrategy,
    RegexEngine,
    TextProcessingResult,
)
from privacy_guard.engines.registry import EngineRegistry, create_builtin_registry
from privacy_guard.errors import EngineRegistryError, PrivacyGuardError
from privacy_guard.timeout import Timeout


class AcmeReplacement(StrictDomainModel):
    strategy: Literal["token"] = "token"


class AcmeConfig(EngineConfig):
    engine: Literal["acme-pii"] = "acme-pii"
    entities: tuple[str, ...]
    replacement: AcmeReplacement | None = None

    @field_validator("entities", mode="before")
    @classmethod
    def _entities_are_a_tuple(cls, value: object) -> object:
        if not isinstance(value, list | tuple):
            raise ValueError("entities must be a list")
        return tuple(value)


@dataclass(frozen=True)
class AcmeResources(EngineResources):
    prefix: str


class AcmeEngine(EntityProcessingEngine[AcmeConfig, AcmeResources]):
    supported_strategies = frozenset(
        {
            EntityProcessingStrategy.DETECT,
            EntityProcessingStrategy.REPLACE,
        }
    )

    @classmethod
    def _validate_run_config(
        cls,
        config: AcmeConfig,
        resources: AcmeResources,
        *,
        strategy: EntityProcessingStrategy,
    ) -> None:
        del cls, resources
        if strategy is EntityProcessingStrategy.REPLACE and config.replacement is None:
            raise EngineConfigurationError("acme replacement configuration is required")

    def _run(
        self,
        text: str,
        *,
        strategy: EntityProcessingStrategy,
        timeout: Timeout,
    ) -> TextProcessingResult:
        del strategy, timeout
        return TextProcessingResult(text=text, detections=())


class DetectConfig(EngineConfig):
    engine: Literal["detect-only"] = "detect-only"


class DetectEngine(EntityProcessingEngine[DetectConfig]):
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


def test_builtin_registry_contains_the_builtin_regex_engine() -> None:
    registry = create_builtin_registry()

    assert registry.is_finalized is True
    descriptions = registry.describe_engines()
    assert tuple(item.engine_name for item in descriptions) == ("regex",)
    description = descriptions[0]
    assert description.engine_name == "regex"
    assert description.supported_strategies == frozenset(
        {
            EntityProcessingStrategy.DETECT,
            EntityProcessingStrategy.REPLACE,
        }
    )


def test_registry_can_include_builtin_engines_before_custom_registration() -> None:
    registry = EngineRegistry(include_builtin_engines=True)
    registry.register(AcmeEngine, resources=AcmeResources(prefix="token"))
    registry.finalize()

    assert tuple(item.engine_name for item in registry.describe_engines()) == (
        "regex",
        "acme-pii",
    )


def test_custom_engine_config_joins_the_exact_discriminated_union() -> None:
    resources = AcmeResources(prefix="token")
    registry = EngineRegistry(include_builtin_engines=True)
    registry.register(AcmeEngine, resources=resources)
    registry.finalize()

    config = registry.validate_config(_acme_values(action="replace"))
    engine = registry.create_engine(config.entity_processing.stages[0].config)

    assert type(config.entity_processing.stages[0].config) is AcmeConfig
    assert type(engine) is AcmeEngine
    assert engine.config is config.entity_processing.stages[0].config
    assert engine.resources is resources
    assert tuple(item.engine_name for item in registry.describe_engines()) == (
        "regex",
        "acme-pii",
    )


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


def test_engine_owns_strategy_specific_configuration_requirements() -> None:
    registry = EngineRegistry()
    registry.register(AcmeEngine, resources=AcmeResources(prefix="token"))
    registry.finalize()
    values = {
        "entity_processing": {
            "stages": [
                {
                    "config": {
                        "engine": "acme-pii",
                        "entities": ["account"],
                    }
                }
            ]
        },
        "on_detection": {"action": "replace"},
    }

    with pytest.raises(PrivacyGuardError):
        registry.validate_config(values)


class ReplaceOnlyConfig(EngineConfig):
    engine: Literal["replace-only"] = "replace-only"


class ReplaceOnlyEngine(EntityProcessingEngine[ReplaceOnlyConfig]):
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
                    }
                }
            ]
        },
        "on_detection": {"action": "detect"},
    }

    with pytest.raises(PrivacyGuardError):
        registry.validate_config(values)

    values["on_detection"] = {"action": "replace"}
    config = registry.validate_config(values)

    config_type = type(config.entity_processing.stages[0].config)
    assert "replacement" not in config_type.model_fields


def test_registry_is_frozen_after_finalize_and_finalize_is_idempotent() -> None:
    registry = EngineRegistry()
    registry.register(RegexEngine)

    assert registry.finalize() is registry
    assert registry.finalize() is registry
    with pytest.raises(EngineRegistryError):
        registry.register(DetectEngine)


def test_registry_rejects_duplicate_discriminators_and_resource_mismatch() -> None:
    registry = EngineRegistry()
    registry.register(AcmeEngine, resources=AcmeResources(prefix="token"))

    with pytest.raises(EngineRegistryError):
        registry.register(AcmeEngine, resources=AcmeResources(prefix="other"))
    with pytest.raises(EngineRegistryError):
        EngineRegistry().register(AcmeEngine)
    with pytest.raises(EngineRegistryError, match="must extend EngineResources"):
        EngineRegistry().register(AcmeEngine, resources=object())
    with pytest.raises(EngineRegistryError):
        EngineRegistry().register(DetectEngine, resources=object())


def _run_without_the_engine_wrapper(
    self: DetectEngine,
    text: str,
    *,
    strategy: EntityProcessingStrategy,
    timeout: Timeout,
) -> TextProcessingResult:
    del self, strategy, timeout
    return TextProcessingResult(text=text, detections=())


def _initialize_without_the_engine_constructor(
    self: DetectEngine,
    config: DetectConfig,
    resources: None,
) -> None:
    del self, config, resources


@pytest.mark.parametrize(
    ("method_name", "method", "expected_error"),
    [
        (
            "run",
            _run_without_the_engine_wrapper,
            "engine lifecycle contract requires EntityProcessingEngine.run; "
            "implement _run() instead",
        ),
        (
            "__init__",
            _initialize_without_the_engine_constructor,
            "engine lifecycle contract requires EntityProcessingEngine.__init__; "
            "use _initialize() instead",
        ),
    ],
)
def test_registry_rejects_direct_and_inherited_lifecycle_overrides(
    method_name: str,
    method: object,
    expected_error: str,
) -> None:
    direct_override = type(
        "LifecycleOverrideEngine",
        (DetectEngine,),
        {method_name: method},
    )
    inherited_override = type(
        "InheritedOverrideEngine",
        (direct_override,),
        {},
    )

    for engine_type in (direct_override, inherited_override):
        with pytest.raises(EngineRegistryError) as error:
            EngineRegistry().register(engine_type)

        assert str(error.value) == expected_error


def test_base_lifecycle_methods_are_final_for_static_feedback() -> None:
    assert getattr(EntityProcessingEngine.__init__, "__final__", False) is True
    assert getattr(EntityProcessingEngine.run, "__final__", False) is True


def test_registry_accepts_base_lifecycle_inherited_through_custom_base() -> None:
    intermediate_base = type(
        "ValidIntermediateEngineBase",
        (DetectEngine,),
        {},
    )
    inherited_lifecycle_engine = type(
        "InheritedLifecycleEngine",
        (intermediate_base,),
        {},
    )

    registry = EngineRegistry()
    registry.register(inherited_lifecycle_engine)

    assert inherited_lifecycle_engine.__init__ is EntityProcessingEngine.__init__
    assert inherited_lifecycle_engine.run is EntityProcessingEngine.run


@pytest.mark.parametrize(
    ("engine_type", "resources"),
    [
        (DetectEngine, None),
        (AcmeEngine, AcmeResources(prefix="token")),
    ],
)
def test_registry_accepts_engines_using_the_base_lifecycle(
    engine_type: type[object],
    resources: object,
) -> None:
    registry = EngineRegistry()

    registry.register(engine_type, resources=resources)

    assert registry.finalize().is_finalized is True


def test_describe_does_not_construct_an_engine() -> None:
    class CountingEngine(EntityProcessingEngine[DetectConfig]):
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
    assert descriptions[0].engine_name == "detect-only"
    assert descriptions[0].supported_strategies == frozenset(
        {EntityProcessingStrategy.DETECT}
    )


def test_registry_requires_at_least_one_engine() -> None:
    with pytest.raises(EngineRegistryError):
        EngineRegistry().finalize()
