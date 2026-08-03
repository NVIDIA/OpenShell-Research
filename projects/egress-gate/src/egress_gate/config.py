"""Strict pipeline policy configuration."""

from __future__ import annotations

from enum import StrEnum
from typing import Generic, Self, TypeVar

from pydantic import Field, field_validator, model_validator

from egress_gate.base import StrictDomainModel
from egress_gate.constants import MAX_PIPELINE_GATES
from egress_gate.gates.base import GateConfig
from egress_gate.result import GateName
from egress_gate.string_validators import validate_scalar_string


class DefaultDecision(StrEnum):
    """Pipeline disposition when every gate proceeds."""

    ALLOW = "allow"
    DENY = "deny"


_GateConfigT = TypeVar("_GateConfigT", bound=GateConfig)


class ConfiguredGate(StrictDomainModel, Generic[_GateConfigT]):
    """One named pipeline entry and its exact gate configuration."""

    name: GateName
    config: _GateConfigT = Field(repr=False)


class PipelineConfig(StrictDomainModel, Generic[_GateConfigT]):
    """Ordered configured gates and the required final default."""

    gates: tuple[ConfiguredGate[_GateConfigT], ...] = Field(repr=False)
    default_decision: DefaultDecision

    @field_validator("gates", mode="before")
    @classmethod
    def _gates_are_bounded_tuple(cls, value: object) -> object:
        if not isinstance(value, list | tuple) or not value:
            raise ValueError("pipeline gates must be a non-empty list")
        if len(value) > MAX_PIPELINE_GATES:
            raise ValueError("pipeline has too many gates")
        return tuple(value)

    @field_validator("default_decision", mode="before")
    @classmethod
    def _parse_default_decision(cls, value: object) -> DefaultDecision:
        if isinstance(value, DefaultDecision):
            return value
        return DefaultDecision(validate_scalar_string(value))

    @model_validator(mode="after")
    def _gate_names_are_unique(self) -> Self:
        names = tuple(gate.name for gate in self.gates)
        if len(names) != len(set(names)):
            raise ValueError("pipeline gate names must be unique")
        return self


class EgressGateConfig(StrictDomainModel, Generic[_GateConfigT]):
    """Complete strict Egress Gate policy configuration."""

    pipeline: PipelineConfig[_GateConfigT] = Field(repr=False)


__all__ = [
    "ConfiguredGate",
    "DefaultDecision",
    "EgressGateConfig",
    "PipelineConfig",
]
