"""Immutable gate evaluations and Egress Gate result models.

These models deliberately contain no protobuf or gRPC types. ``SourcedFinding``
keeps gate provenance inside the runtime; the current OpenShell wire contract
serializes only the five fields on ``Finding``.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Self, TypeAlias

from pydantic import (
    Field,
    model_validator,
)

from egress_gate.base import StrictDomainModel
from egress_gate.constants import (
    DEFAULT_DENY_REASON_CODE,
    LIMIT_REASON_CODE,
    MAX_FINDING_COUNT,
    MAX_GATE_TRACES,
    MAX_PROTO_FINDING_BYTES,
    MAX_PROTO_FINDING_GROUPS,
    MAX_RESULT_METADATA_BYTES,
    MAX_RESULT_METADATA_ENTRIES,
    MAX_TRACE_MUTATION_KINDS,
    REASON_CODE_PATTERN,
)
from egress_gate.request import RequestMutations
from egress_gate.string_validators import BoundedMetadataString

ReasonCode = Annotated[str, Field(pattern=REASON_CODE_PATTERN)]
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

    @model_validator(mode="after")
    def _wire_size_is_bounded(self) -> Self:
        encoded_size = (
            _encoded_string_field_size(self.type)
            + _encoded_string_field_size(self.label)
            + 1
            + _varint_size(self.count)
        )
        if self.confidence is not None:
            encoded_size += _encoded_string_field_size(self.confidence)
        if self.severity is not None:
            encoded_size += _encoded_string_field_size(self.severity)
        if encoded_size > MAX_PROTO_FINDING_BYTES:
            raise ValueError("finding exceeds the encoded size limit")
        return self


class FindingTypeDefinition(StrictDomainModel):
    """A runtime-owned declaration for one possible finding type."""

    type: FindingType


class SourcedFinding(StrictDomainModel):
    """Runtime-internal finding provenance excluded from wire serialization."""

    source_gate: GateName
    finding: Finding


class GateDecisionSource(StrictDomainModel):
    """A final decision made by one configured gate."""

    kind: Literal[DecisionSourceKind.GATE]
    gate_name: GateName
    gate_type: GateType


class PipelineDefaultDecisionSource(StrictDomainModel):
    """A final decision made by the pipeline default."""

    kind: Literal[DecisionSourceKind.PIPELINE_DEFAULT]


class RuntimeLimitDecisionSource(StrictDomainModel):
    """A fail-closed decision caused by a runtime safety limit."""

    kind: Literal[DecisionSourceKind.RUNTIME_LIMIT]


DecisionSource: TypeAlias = Annotated[
    GateDecisionSource | PipelineDefaultDecisionSource | RuntimeLimitDecisionSource,
    Field(discriminator="kind"),
]


class GateEvaluation(StrictDomainModel):
    """Validated output of one gate invocation."""

    control: GateControl
    request_mutations: RequestMutations = Field(default_factory=RequestMutations)
    findings: tuple[Finding, ...] = Field(
        default=(),
        max_length=MAX_PROTO_FINDING_GROUPS,
    )
    reason_code: ReasonCode | None = None

    @model_validator(mode="after")
    def _control_contract_is_valid(self) -> Self:
        if self.control is GateControl.PROCEED:
            if self.reason_code is not None:
                raise ValueError("proceed evaluations cannot carry a reason code")
            return self
        if not self.request_mutations.is_empty:
            raise ValueError("terminal evaluations cannot carry request mutations")
        if self.control is GateControl.ALLOW and self.reason_code is not None:
            raise ValueError("allow evaluations cannot carry a reason code")
        if self.control is GateControl.DENY and self.reason_code is None:
            raise ValueError("deny evaluations require a reason code")
        return self

    @classmethod
    def proceed(
        cls,
        *,
        request_mutations: RequestMutations | None = None,
        findings: tuple[Finding, ...] = (),
    ) -> Self:
        """Create a non-terminal evaluation."""
        return cls(
            control=GateControl.PROCEED,
            request_mutations=(
                RequestMutations() if request_mutations is None else request_mutations
            ),
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
    mutation_kinds: tuple[MutationKind, ...] = Field(
        default=(),
        max_length=MAX_TRACE_MUTATION_KINDS,
    )


class ResultMetadata(StrictDomainModel):
    """One bounded runtime-owned result metadata entry."""

    key: BoundedMetadataString
    value: BoundedMetadataString


class EgressResult(StrictDomainModel):
    """Final domain result returned after pipeline execution."""

    decision: EgressDecision
    decision_source: DecisionSource
    request_mutations: RequestMutations = Field(default_factory=RequestMutations)
    findings: tuple[SourcedFinding, ...] = Field(
        default=(),
        max_length=MAX_PROTO_FINDING_GROUPS,
    )
    reason_code: ReasonCode | None = None
    metadata: tuple[ResultMetadata, ...] = Field(
        default=(),
        max_length=MAX_RESULT_METADATA_ENTRIES,
    )
    policy_fingerprint: BoundedMetadataString | None = None
    traces: tuple[GateTrace, ...] = Field(
        default=(),
        max_length=MAX_GATE_TRACES,
    )

    @model_validator(mode="after")
    def _result_contract_is_valid(self) -> Self:
        metadata_bytes = sum(
            len(item.key.encode("utf-8")) + len(item.value.encode("utf-8"))
            for item in self.metadata
        )
        if metadata_bytes > MAX_RESULT_METADATA_BYTES:
            raise ValueError("result metadata exceeds the size limit")
        source_kind = self.decision_source.kind
        if self.decision is EgressDecision.DENY:
            if not self.request_mutations.is_empty:
                raise ValueError("denied results cannot carry request mutations")
            if self.reason_code is None:
                raise ValueError("denied results require a reason code")
        elif self.reason_code is not None:
            raise ValueError("allowed results cannot carry a reason code")

        if source_kind is DecisionSourceKind.RUNTIME_LIMIT:
            if self.decision is not EgressDecision.DENY:
                raise ValueError("runtime-limit results must deny")
            if self.reason_code != LIMIT_REASON_CODE:
                raise ValueError("runtime-limit results require the limit reason")
        elif source_kind is DecisionSourceKind.PIPELINE_DEFAULT:
            if self.decision is EgressDecision.DENY:
                if self.reason_code != DEFAULT_DENY_REASON_CODE:
                    raise ValueError("default denies require the default reason")
            elif self.reason_code is not None:
                raise ValueError("default allows cannot carry a reason code")
        return self


def _varint_size(value: int) -> int:
    size = 1
    while value >= 0x80:
        value >>= 7
        size += 1
    return size


def _encoded_string_field_size(value: str) -> int:
    length = len(value.encode("utf-8"))
    return 1 + _varint_size(length) + length


__all__ = [
    "DecisionSource",
    "DecisionSourceKind",
    "EgressDecision",
    "EgressResult",
    "Finding",
    "FindingTypeDefinition",
    "FindingLabel",
    "FindingType",
    "GateControl",
    "GateDecisionSource",
    "GateEvaluation",
    "GateName",
    "GateTrace",
    "GateType",
    "MutationKind",
    "PipelineDefaultDecisionSource",
    "ReasonCode",
    "ResultMetadata",
    "RuntimeLimitDecisionSource",
    "SourcedFinding",
]
