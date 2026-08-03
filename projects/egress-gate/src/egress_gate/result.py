"""Immutable gate evaluations and Egress Gate result models.

These models deliberately contain no protobuf or gRPC types. ``SourcedFinding``
keeps gate provenance inside the runtime; the current OpenShell wire contract
serializes only the five fields on ``Finding``.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, Self, TypeAlias

from pydantic import (
    BeforeValidator,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from egress_gate.base import StrictDomainModel
from egress_gate.constants import MAX_FINDING_COUNT, MAX_PROTO_FINDING_GROUPS
from egress_gate.request import RequestPatch
from egress_gate.string_validators import (
    BoundedMetadataString,
    validate_scalar_string,
)


def _require_tuple(value: object, field_name: str) -> tuple[object, ...]:
    if not isinstance(value, tuple):
        raise ValueError(f"{field_name} must be a tuple")
    return value


def _validate_reason_code(value: object) -> str:
    reason_code = validate_scalar_string(value)
    if not reason_code or not _REASON_CODE_PATTERN.fullmatch(reason_code):
        raise ValueError("reason code is invalid")
    return reason_code


ReasonCode = Annotated[str, BeforeValidator(_validate_reason_code)]
FindingType: TypeAlias = BoundedMetadataString
FindingLabel: TypeAlias = BoundedMetadataString
GateName: TypeAlias = BoundedMetadataString
GateType: TypeAlias = BoundedMetadataString


class GateControl(StrEnum):
    """The complete control result of one gate invocation."""

    PROCEED = "proceed"
    ALLOW = "allow"
    DENY = "deny"


class EgressDecision(StrEnum):
    """The final OpenShell request disposition."""

    ALLOW = "allow"
    DENY = "deny"


class DecisionSourceKind(StrEnum):
    """The owner of the final decision."""

    GATE = "gate"
    PIPELINE_DEFAULT = "pipeline_default"
    RUNTIME_LIMIT = "runtime_limit"


class MutationKind(StrEnum):
    """The kinds of mutation represented in a gate trace."""

    BODY = "body"
    HEADERS = "headers"


class Finding(StrictDomainModel):
    """One audit-safe observation matching the current OpenShell wire shape."""

    type: FindingType
    label: FindingLabel
    count: int = Field(default=1, ge=1, le=MAX_FINDING_COUNT)
    confidence: BoundedMetadataString | None = None
    severity: BoundedMetadataString | None = None


class FindingDefinition(StrictDomainModel):
    """A runtime-owned declaration for one possible finding type."""

    type: FindingType


class SourcedFinding(StrictDomainModel):
    """Runtime-internal finding provenance excluded from wire serialization."""

    source_gate: GateName
    finding: Finding


class DecisionSource(StrictDomainModel):
    """Runtime-owned attribution for a final decision."""

    kind: DecisionSourceKind
    gate_name: GateName | None = None
    gate_type: GateType | None = None

    @model_validator(mode="after")
    def _gate_fields_match_kind(self) -> Self:
        has_gate = self.gate_name is not None or self.gate_type is not None
        if self.kind is DecisionSourceKind.GATE and not (
            self.gate_name is not None and self.gate_type is not None
        ):
            raise ValueError("gate decision sources require gate name and type")
        if self.kind is not DecisionSourceKind.GATE and has_gate:
            raise ValueError("non-gate decision sources cannot name a gate")
        return self

    @classmethod
    def gate(cls, *, name: str, gate_type: str) -> Self:
        """Create a source attributed to one configured gate."""
        return cls(
            kind=DecisionSourceKind.GATE,
            gate_name=name,
            gate_type=gate_type,
        )

    @classmethod
    def pipeline_default(cls) -> Self:
        """Create a source attributed to the pipeline default."""
        return cls(kind=DecisionSourceKind.PIPELINE_DEFAULT)

    @classmethod
    def runtime_limit(cls) -> Self:
        """Create a source attributed to a runtime safety limit."""
        return cls(kind=DecisionSourceKind.RUNTIME_LIMIT)


class GateEvaluation(StrictDomainModel):
    """Validated output of one gate invocation."""

    control: GateControl
    patch: RequestPatch = Field(default_factory=RequestPatch)
    findings: tuple[Finding, ...] = ()
    reason_code: ReasonCode | None = None

    @field_validator("findings", mode="before")
    @classmethod
    def _findings_are_a_tuple(cls, value: object) -> object:
        return _require_tuple(value, "findings")

    @model_validator(mode="after")
    def _control_contract_is_valid(self) -> Self:
        if self.control is GateControl.PROCEED:
            if self.reason_code is not None:
                raise ValueError("proceed evaluations cannot carry a reason code")
            return self
        if not self.patch.is_empty:
            raise ValueError("terminal evaluations cannot carry a patch")
        if self.control is GateControl.ALLOW and self.reason_code is not None:
            raise ValueError("allow evaluations cannot carry a reason code")
        if self.control is GateControl.DENY and self.reason_code is None:
            raise ValueError("deny evaluations require a reason code")
        return self

    @classmethod
    def proceed(
        cls,
        *,
        patch: RequestPatch | None = None,
        findings: tuple[Finding, ...] = (),
    ) -> Self:
        """Create a non-terminal evaluation."""
        return cls(
            control=GateControl.PROCEED,
            patch=RequestPatch() if patch is None else patch,
            findings=findings,
        )

    @classmethod
    def allow(cls, *, findings: tuple[Finding, ...] = ()) -> Self:
        """Create a terminal allow evaluation."""
        return cls(control=GateControl.ALLOW, findings=findings)

    @classmethod
    def deny(
        cls,
        reason_code: ReasonCode,
        *,
        findings: tuple[Finding, ...] = (),
    ) -> Self:
        """Create a terminal deny evaluation."""
        return cls(
            control=GateControl.DENY,
            reason_code=reason_code,
            findings=findings,
        )


class GateTrace(StrictDomainModel):
    """Content-safe runtime trace data for one configured gate."""

    gate_name: GateName
    gate_type: GateType
    control: GateControl
    duration_ms: float = Field(ge=0, allow_inf_nan=False)
    finding_count: int = Field(ge=0, le=MAX_FINDING_COUNT)
    mutation_kinds: tuple[MutationKind, ...] = ()

    @field_validator("mutation_kinds", mode="before")
    @classmethod
    def _mutation_kinds_are_a_tuple(cls, value: object) -> object:
        return _require_tuple(value, "mutation_kinds")


class ResultMetadata(StrictDomainModel):
    """One bounded runtime-owned result metadata entry."""

    key: BoundedMetadataString
    value: BoundedMetadataString


class EgressResult(StrictDomainModel):
    """Final domain result returned after pipeline execution."""

    decision: EgressDecision
    decision_source: DecisionSource
    patch: RequestPatch = Field(default_factory=RequestPatch)
    findings: tuple[SourcedFinding, ...] = ()
    reason_code: ReasonCode | None = None
    metadata: tuple[ResultMetadata, ...] = ()
    policy_fingerprint: BoundedMetadataString | None = None
    traces: tuple[GateTrace, ...] = ()

    @field_validator("findings", "metadata", "traces", mode="before")
    @classmethod
    def _result_sequences_are_tuples(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> object:
        return _require_tuple(value, info.field_name or "result field")

    @model_validator(mode="after")
    def _result_contract_is_valid(self) -> Self:
        if len(self.findings) > MAX_PROTO_FINDING_GROUPS:
            raise ValueError("result has too many finding groups")
        if self.decision is EgressDecision.DENY:
            if not self.patch.is_empty:
                raise ValueError("denied results cannot carry a patch")
            if self.reason_code is None:
                raise ValueError("denied results require a reason code")
        elif self.reason_code is not None:
            raise ValueError("allowed results cannot carry a reason code")
        return self


_REASON_CODE_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")


__all__ = [
    "DecisionSource",
    "DecisionSourceKind",
    "EgressDecision",
    "EgressResult",
    "Finding",
    "FindingDefinition",
    "FindingLabel",
    "FindingType",
    "GateControl",
    "GateEvaluation",
    "GateName",
    "GateTrace",
    "GateType",
    "MutationKind",
    "ReasonCode",
    "ResultMetadata",
    "SourcedFinding",
]
