"""Strict entity-processing policy configuration.

The concrete model accepted at the policy boundary is finalized by
``EngineRegistry``.  Its stage ``config`` field is a Pydantic discriminated
union containing the exact config model registered by every engine.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from enum import StrEnum
from functools import reduce
from hashlib import sha256
from operator import or_
from typing import Annotated, Generic, Self, TypeAlias, TypeVar

from pydantic import (
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from privacy_guard.base import StrictDomainModel
from privacy_guard.engines import EngineConfig
from privacy_guard.errors import ErrorCode, PrivacyGuardError
from privacy_guard.string_validators import (
    BoundedMetadataString,
    validate_scalar_string,
)


class PolicyAction(StrEnum):
    """User-facing disposition applied after all configured stages run."""

    DETECT = "detect"
    BLOCK = "block"
    REPLACE = "replace"


class OnDetection(StrictDomainModel):
    """Required policy disposition for detected entities."""

    action: PolicyAction

    @field_validator("action", mode="before")
    @classmethod
    def _parse_action(cls, value: object) -> PolicyAction:
        if isinstance(value, PolicyAction):
            return value
        return PolicyAction(validate_scalar_string(value))


_EngineConfigT = TypeVar(
    "_EngineConfigT",
    bound=EngineConfig[StrictDomainModel],
)


class EntityProcessingStage(
    StrictDomainModel,
    Generic[_EngineConfigT],
):
    """One ordered invocation of an engine with an optional diagnostic name."""

    name: BoundedMetadataString | None = None
    config: _EngineConfigT = Field(repr=False)

    def diagnostic_name(self, stage_number: int) -> str:
        """Return the explicit name or a deterministic one-based source label."""
        if self.name is not None:
            return self.name
        if isinstance(stage_number, bool) or stage_number < 1:
            raise ValueError("stage number must be a positive integer")
        engine = getattr(self.config, "engine", None)
        if not isinstance(engine, str):
            raise ValueError("stage config has no engine discriminator")
        return f"{engine}[{stage_number}]"


class EntityProcessing(
    StrictDomainModel,
    Generic[_EngineConfigT],
):
    """The ordered entity-processing stages for one policy."""

    stages: tuple[EntityProcessingStage[_EngineConfigT], ...] = Field(repr=False)

    @field_validator("stages", mode="before")
    @classmethod
    def _parse_stages(cls, value: object) -> object:
        if not isinstance(value, list | tuple) or not value:
            raise ValueError("stages must be a non-empty list")
        return tuple(value)

    @model_validator(mode="after")
    def _diagnostic_names_are_unique(self) -> Self:
        names = [
            stage.diagnostic_name(index)
            for index, stage in enumerate(self.stages, start=1)
        ]
        if len(names) != len(set(names)):
            raise ValueError("stage diagnostic names must be unique")
        return self


class PrivacyGuardConfig(
    StrictDomainModel,
    Generic[_EngineConfigT],
):
    """Complete validated Privacy Guard behavior for one OpenShell policy."""

    entity_processing: EntityProcessing[_EngineConfigT] = Field(repr=False)
    on_detection: OnDetection = Field(repr=False)


FinalizedPrivacyGuardConfig: TypeAlias = PrivacyGuardConfig[
    EngineConfig[StrictDomainModel]
]
FinalizedPrivacyGuardConfigType = type[FinalizedPrivacyGuardConfig]


def build_privacy_guard_config_type(
    config_types: Sequence[type[EngineConfig[StrictDomainModel]]],
) -> FinalizedPrivacyGuardConfigType:
    """Build the exact registry-dependent discriminated policy model."""
    if not config_types:
        raise ValueError("at least one engine config type must be registered")
    registered_union = reduce(or_, config_types)
    registered_config = Annotated[
        registered_union,  # ty: ignore[invalid-type-form]
        Field(discriminator="engine"),
    ]
    config_type = PrivacyGuardConfig.__class_getitem__(
        registered_config  # ty: ignore[invalid-argument-type]
    )
    if not isinstance(config_type, type) or not issubclass(
        config_type, PrivacyGuardConfig
    ):
        raise TypeError("Pydantic did not construct a policy config type")
    return config_type  # ty: ignore[invalid-return-type]


def build_privacy_guard_config_adapter(
    config_types: Sequence[type[EngineConfig[StrictDomainModel]]],
) -> TypeAdapter[FinalizedPrivacyGuardConfig]:
    """Build the registry-dependent adapter used for validation and schemas."""
    config_type = build_privacy_guard_config_type(config_types)
    return TypeAdapter(config_type)


def parse_privacy_guard_config(
    adapter: TypeAdapter[FinalizedPrivacyGuardConfig],
    values: object,
) -> FinalizedPrivacyGuardConfig:
    """Parse an expanded mapping without exposing rejected values in errors."""
    if not isinstance(values, Mapping):
        raise PrivacyGuardError(ErrorCode.CONFIG_INVALID)
    try:
        return adapter.validate_python(dict(values))
    except (TypeError, ValueError, ValidationError):
        raise PrivacyGuardError(ErrorCode.CONFIG_INVALID) from None


def canonical_config_json(
    config: PrivacyGuardConfig[EngineConfig[StrictDomainModel]],
) -> bytes:
    """Serialize every concrete engine field deterministically for hashing."""
    return json.dumps(
        config.model_dump(mode="json"),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def configuration_fingerprint(
    config: PrivacyGuardConfig[EngineConfig[StrictDomainModel]],
) -> str:
    """Return the canonical SHA-256 fingerprint of an expanded policy config."""
    return sha256(canonical_config_json(config)).hexdigest()


__all__ = [
    "EntityProcessing",
    "EntityProcessingStage",
    "FinalizedPrivacyGuardConfig",
    "FinalizedPrivacyGuardConfigType",
    "OnDetection",
    "PolicyAction",
    "PrivacyGuardConfig",
    "build_privacy_guard_config_adapter",
    "build_privacy_guard_config_type",
    "canonical_config_json",
    "configuration_fingerprint",
    "parse_privacy_guard_config",
]
