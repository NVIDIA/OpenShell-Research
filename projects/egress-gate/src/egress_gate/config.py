"""Strict Egress Gate policy configuration."""

from __future__ import annotations

from enum import StrEnum
from typing import Generic, TypeVar

from pydantic import Field, field_validator

from egress_gate.base import StrictDomainModel
from egress_gate.constants import MAX_PIPELINE_GATES
from egress_gate.gates.base import GateConfig
from egress_gate.string_validators import validate_scalar_string


class DefaultDecision(StrEnum):
    """Pipeline disposition when every gate proceeds."""

    ALLOW = "allow"
    DENY = "deny"


_GateConfigT = TypeVar("_GateConfigT", bound=GateConfig)


class EgressGateConfig(StrictDomainModel, Generic[_GateConfigT]):
    """Flat policy with ordered named gates and a required fallback decision."""

    gates: tuple[_GateConfigT, ...] = Field(
        min_length=1,
        max_length=MAX_PIPELINE_GATES,
        description="Ordered gate configurations. Each gate name must be unique.",
        repr=False,
    )
    default_decision: DefaultDecision = Field(
        description="Decision used when every configured gate proceeds."
    )

    @field_validator("gates", mode="before")
    @classmethod
    def _gates_are_bounded_tuple(cls, value: object) -> object:
        if not isinstance(value, list | tuple):
            raise ValueError("policy gates must be a non-empty list")
        return tuple(value)

    @field_validator("gates")
    @classmethod
    def _gate_names_are_unique(
        cls,
        value: tuple[_GateConfigT, ...],
    ) -> tuple[_GateConfigT, ...]:
        names = tuple(gate.name for gate in value)
        if len(names) != len(set(names)):
            raise ValueError("policy gate names must be unique")
        return value

    @field_validator("default_decision", mode="before")
    @classmethod
    def _parse_default_decision(cls, value: object) -> DefaultDecision:
        if isinstance(value, DefaultDecision):
            return value
        return DefaultDecision(validate_scalar_string(value))


__all__ = [
    "DefaultDecision",
    "EgressGateConfig",
]
