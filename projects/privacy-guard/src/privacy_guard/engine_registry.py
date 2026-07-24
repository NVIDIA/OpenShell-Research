"""Engine registration and finalized policy-schema construction."""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass
from types import NoneType
from typing import Literal, Self, get_args, get_origin

from pydantic import TypeAdapter
from pydantic_core import PydanticUndefined

from privacy_guard.config import (
    FinalizedPrivacyGuardConfig,
    FinalizedPrivacyGuardConfigType,
    PolicyAction,
    build_privacy_guard_config_type,
    parse_privacy_guard_config,
)
from privacy_guard.engines import (
    EngineConfig,
    EngineConfigurationError,
    EngineResources,
    EntityProcessingEngine,
    EntityProcessingStrategy,
)
from privacy_guard.errors import ErrorCode, PrivacyGuardError


class EngineRegistryError(Exception):
    """A content-safe engine registration or registry lifecycle failure."""


@dataclass(frozen=True)
class EngineDescription:
    """Safe discovery metadata for one registered engine."""

    engine: str
    description: str
    supported_strategies: frozenset[EntityProcessingStrategy]
    configuration_schema: dict[str, object]


class EngineRegistry:
    """Register engine implementations and finalize their exact policy union."""

    def __init__(self) -> None:
        self._registrations: dict[str, _Registration] = {}
        self._config_type: FinalizedPrivacyGuardConfigType | None = None
        self._config_adapter: TypeAdapter[FinalizedPrivacyGuardConfig] | None = None

    @property
    def is_finalized(self) -> bool:
        return self._config_adapter is not None

    @property
    def engine_names(self) -> tuple[str, ...]:
        return tuple(self._registrations)

    @property
    def config_type(self) -> FinalizedPrivacyGuardConfigType:
        if self._config_type is None:
            raise EngineRegistryError("engine registry is not finalized")
        return self._config_type

    @property
    def config_adapter(self) -> TypeAdapter[FinalizedPrivacyGuardConfig]:
        if self._config_adapter is None:
            raise EngineRegistryError("engine registry is not finalized")
        return self._config_adapter

    def register(
        self,
        engine_type: type[object],
        *,
        resources: object = None,
    ) -> None:
        """Register one engine implementation and its operator-owned resources."""
        if self.is_finalized:
            raise EngineRegistryError("cannot register after finalization")
        if not isinstance(engine_type, type) or not issubclass(
            engine_type, EntityProcessingEngine
        ):
            raise EngineRegistryError("registered engine type is invalid")

        try:
            config_type = engine_type.get_config_type()
            resources_type = engine_type.get_resources_type()
        except (AttributeError, TypeError):
            raise EngineRegistryError("engine generic declaration is invalid") from None
        if not isinstance(config_type, type) or not issubclass(
            config_type, EngineConfig
        ):
            raise EngineRegistryError("engine config type is invalid")
        resources_runtime_type = (
            NoneType
            if resources_type is None
            else get_origin(resources_type) or resources_type
        )
        if not isinstance(resources_runtime_type, type):
            raise EngineRegistryError("engine resources type is invalid")
        if resources_runtime_type is not NoneType and not issubclass(
            resources_runtime_type,
            EngineResources,
        ):
            raise EngineRegistryError(
                "engine resources type must extend EngineResources"
            )

        engine_name = _engine_discriminator(config_type)
        if engine_name in self._registrations:
            raise EngineRegistryError("engine discriminator is already registered")
        if any(
            registration.config_type is config_type
            for registration in self._registrations.values()
        ):
            raise EngineRegistryError("engine config type is already registered")

        supported_strategies = getattr(engine_type, "supported_strategies", None)
        if (
            not isinstance(supported_strategies, frozenset)
            or not supported_strategies
            or any(
                not isinstance(strategy, EntityProcessingStrategy)
                for strategy in supported_strategies
            )
        ):
            raise EngineRegistryError("engine supported strategies are invalid")
        if resources_runtime_type is NoneType:
            if resources is not None:
                raise EngineRegistryError("resource-free engine received resources")
        else:
            if resources is not None and not isinstance(resources, EngineResources):
                raise EngineRegistryError(
                    "engine resources must extend EngineResources"
                )
            if resources is None or not isinstance(resources, resources_runtime_type):
                raise EngineRegistryError(
                    "engine resources do not match their declared type"
                )

        self._registrations[engine_name] = _Registration(
            engine_type=engine_type,
            config_type=config_type,
            resources_type=resources_type,
            resources=resources,
            supported_strategies=supported_strategies,
        )

    def finalize(self) -> Self:
        """Freeze registrations, build the policy union, and return this registry."""
        if self.is_finalized:
            return self
        try:
            config_type = build_privacy_guard_config_type(
                tuple(
                    registration.config_type
                    for registration in self._registrations.values()
                )
            )
        except ValueError:
            raise EngineRegistryError(
                "cannot finalize an empty engine registry"
            ) from None
        self._config_type = config_type
        self._config_adapter = TypeAdapter(config_type)
        return self

    def validate_config(self, values: object) -> FinalizedPrivacyGuardConfig:
        """Purely parse and validate an expanded Privacy Guard configuration."""
        config = parse_privacy_guard_config(self.config_adapter, values)
        required_strategy = (
            EntityProcessingStrategy.REPLACE
            if config.on_detection.action is PolicyAction.REPLACE
            else EntityProcessingStrategy.DETECT
        )
        for stage in config.entity_processing.stages:
            registration = self._resolve_registration(stage.config)
            engine_type = registration.engine_type
            if not issubclass(engine_type, EntityProcessingEngine):
                raise EngineRegistryError("registered engine type is invalid")
            try:
                validate_run_config = getattr(engine_type, "validate_run_config")
                validate_run_config(
                    stage.config,
                    registration.resources,
                    strategy=required_strategy,
                )
            except EngineConfigurationError:
                raise PrivacyGuardError(ErrorCode.CONFIG_INVALID) from None
        return config

    def create_engine(
        self,
        config: EngineConfig,
    ) -> EntityProcessingEngine[EngineConfig, EngineResources | None]:
        """Construct an initialized engine from its exact validated config."""
        registration = self._resolve_registration(config)
        if type(config) is not registration.config_type:
            raise EngineRegistryError("engine config concrete type is invalid")
        return registration.engine_type(config, registration.resources)

    def configuration_json_schema(self) -> dict[str, object]:
        """Return the finalized complete policy JSON Schema."""
        return self.config_adapter.json_schema()

    def describe_engines(self) -> tuple[EngineDescription, ...]:
        """Return safe engine metadata without constructing runtime engines."""
        return tuple(
            EngineDescription(
                engine=engine,
                description=_engine_description(registration.engine_type),
                supported_strategies=registration.supported_strategies,
                configuration_schema=registration.config_type.model_json_schema(),
            )
            for engine, registration in self._registrations.items()
        )

    def _resolve_registration(
        self,
        config: EngineConfig,
    ) -> _Registration:
        if not self.is_finalized:
            raise EngineRegistryError("engine registry is not finalized")
        try:
            engine_name = getattr(config, "engine")
            if not isinstance(engine_name, str):
                raise AttributeError
            registration = self._registrations[engine_name]
        except (AttributeError, KeyError):
            raise EngineRegistryError("engine config is not registered") from None
        return registration


@dataclass(frozen=True)
class _Registration:
    engine_type: type[object]
    config_type: type[EngineConfig]
    resources_type: object
    resources: EngineResources | None
    supported_strategies: frozenset[EntityProcessingStrategy]


def _engine_discriminator(
    config_type: type[EngineConfig],
) -> str:
    field = config_type.model_fields.get("engine")
    if field is None:
        raise EngineRegistryError("engine config lacks an engine discriminator")
    if get_origin(field.annotation) is not Literal:
        raise EngineRegistryError("engine discriminator must be one string Literal")
    values = get_args(field.annotation)
    if len(values) != 1 or not isinstance(values[0], str):
        raise EngineRegistryError("engine discriminator must be one string Literal")
    engine = values[0]
    if _ENGINE_NAME.fullmatch(engine) is None or len(engine.encode("ascii")) > 128:
        raise EngineRegistryError("engine discriminator is invalid")
    if field.default is not PydanticUndefined and field.default != engine:
        raise EngineRegistryError("engine discriminator default is inconsistent")
    return engine


def _engine_description(
    engine_type: type[object],
) -> str:
    description = inspect.getdoc(engine_type) or ""
    first_line = description.splitlines()[0] if description else ""
    if len(first_line.encode("utf-8")) > 1024:
        return ""
    return first_line


_ENGINE_NAME = re.compile(r"[a-z][a-z0-9-]{0,127}\Z")


__all__ = [
    "EngineDescription",
    "EngineRegistry",
    "EngineRegistryError",
]
