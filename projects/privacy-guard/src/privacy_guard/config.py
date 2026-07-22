"""Strict middleware policy configuration at the untrusted config boundary."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from string import Formatter
from typing import Literal, Self, TypeAlias

from pydantic import Field, field_validator

from privacy_guard.errors import ErrorCode, PrivacyGuardError
from privacy_guard.scanners import Confidence
from privacy_guard.validation import (
    BoundedMetadataString,
    NonEmptyScalarString,
    ScalarString,
    StrictSensitiveModel,
    parse_scalar_string,
)


class PolicyAction(StrEnum):
    """Supported actions for scanner findings."""

    OBSERVE = "observe"
    REDACT = "redact"
    BLOCK = "block"


class ActionConfig(StrictSensitiveModel):
    """Finding criteria shared by every policy action."""

    action: PolicyAction
    entity_types: frozenset[BoundedMetadataString] | None = Field(
        default=None, repr=False
    )
    minimum_confidence: Confidence | None = None


class ObserveActionConfig(ActionConfig):
    """Observe selected findings without changing or blocking the request."""

    action: Literal[PolicyAction.OBSERVE] = PolicyAction.OBSERVE


class BlockActionConfig(ActionConfig):
    """Block the request when at least one selected finding is present."""

    action: Literal[PolicyAction.BLOCK] = PolicyAction.BLOCK


class RedactActionConfig(ActionConfig):
    """Replace selected finding spans using a bounded text template."""

    action: Literal[PolicyAction.REDACT] = PolicyAction.REDACT
    template: ScalarString = Field(default="[{entity}]", repr=False)

    @field_validator("template")
    @classmethod
    def _validate_template(cls, value: str) -> str:
        """Allow static text and the finding entity, but no formatting features."""
        try:
            parsed_fields = Formatter().parse(value)
            for _, field_name, format_spec, conversion in parsed_fields:
                if field_name is not None and field_name != "entity":
                    raise ValueError("template field is unsupported")
                if format_spec or conversion is not None:
                    raise ValueError("template formatting options are unsupported")
        except ValueError:
            raise ValueError("redaction template syntax is invalid") from None
        return value


PolicyActionConfig: TypeAlias = (
    ObserveActionConfig | BlockActionConfig | RedactActionConfig
)


class PolicyConfig(StrictSensitiveModel):
    """Strict, immutable policy parsed from the supervisor's request config."""

    body_format: NonEmptyScalarString = Field(default="json", repr=False)
    on_finding: PolicyActionConfig = Field(
        default_factory=RedactActionConfig,
        discriminator="action",
        repr=False,
    )

    @classmethod
    def from_mapping(cls, values: object) -> Self:
        """Parse untrusted values while discarding Pydantic error content."""
        try:
            if not isinstance(values, Mapping):
                raise TypeError("configuration input must be a mapping")
            prepared = dict(values)
            if "on_finding" in prepared:
                prepared["on_finding"] = _prepare_action_config(prepared["on_finding"])
            return cls.model_validate(prepared)
        except (TypeError, ValueError):
            raise PrivacyGuardError(ErrorCode.CONFIG_INVALID) from None


def _parse_policy_action(value: object) -> PolicyAction:
    return PolicyAction(parse_scalar_string(value))


def _parse_confidence(value: object) -> Confidence:
    return Confidence(parse_scalar_string(value))


def _prepare_action_config(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("action configuration must be a mapping")
    prepared = dict(value)
    if "action" in prepared:
        prepared["action"] = _parse_policy_action(prepared["action"])
    if prepared.get("minimum_confidence") is not None:
        prepared["minimum_confidence"] = _parse_confidence(
            prepared["minimum_confidence"]
        )
    if "entity_types" in prepared:
        prepared["entity_types"] = _parse_entity_type_list(prepared["entity_types"])
    return prepared


def _parse_entity_type_list(value: object) -> frozenset[str] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError("entity types must be a list of strings or null")
    parsed = tuple(parse_scalar_string(item) for item in value)
    if len(set(parsed)) != len(parsed):
        raise ValueError("entity types must be unique")
    return frozenset(parsed)
