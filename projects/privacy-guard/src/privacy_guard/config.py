"""Strict entity-processing policy configuration.

The concrete model accepted at the policy boundary is finalized by
``EngineRegistry``.  Its stage ``config`` field is a Pydantic discriminated
union containing the exact config model registered by every engine.
"""

from __future__ import annotations

import json
from enum import StrEnum
from hashlib import sha256
from typing import Generic, Self, TypeVar

from pydantic import (
    Field,
    field_validator,
    model_validator,
)

from privacy_guard.base import StrictDomainModel
from privacy_guard.constants import MAX_ENTITY_PROCESSING_STAGES
from privacy_guard.engines import EngineConfig
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
    bound=EngineConfig,
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


class EntityProcessingStages(
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
        if len(value) > MAX_ENTITY_PROCESSING_STAGES:
            raise ValueError("policy has too many entity-processing stages")
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

    entity_processing: EntityProcessingStages[_EngineConfigT] = Field(repr=False)
    on_detection: OnDetection = Field(repr=False)


def configuration_fingerprint(
    config: PrivacyGuardConfig[EngineConfig],
) -> str:
    """Return the canonical SHA-256 fingerprint of an expanded policy config."""
    fingerprint, _ = _configuration_fingerprint_and_size(config)
    return fingerprint


def _configuration_fingerprint_and_size(
    config: PrivacyGuardConfig[EngineConfig],
) -> tuple[str, int]:
    """Return the canonical fingerprint and encoded size of an expanded config."""
    serialized = json.dumps(
        config.model_dump(mode="json"),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return sha256(serialized).hexdigest(), len(serialized)


__all__ = [
    "EntityProcessingStage",
    "EntityProcessingStages",
    "OnDetection",
    "PolicyAction",
    "PrivacyGuardConfig",
    "configuration_fingerprint",
]
