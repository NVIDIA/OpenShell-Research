"""Public, transport-neutral models for harness admission."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from egress_gate.base import StrictDomainModel
from egress_gate.constants import MAX_BODY_BYTES, MAX_PROTO_FINDING_GROUPS
from egress_gate.request import HttpTarget
from egress_gate.result import ReasonCode, SourcedFinding
from egress_gate.string_validators import BoundedMetadataString, ScalarString

PI_HARNESS_VERSION = "extension-v1"


class AdmissionHook(StrEnum):
    """Supported Pi admission boundaries."""

    RENDERED_PROMPT = "rendered_prompt_admission"


class AdmissionDecision(StrEnum):
    """Disposition of a harness request."""

    ALLOW = "allow"
    REPLACE = "replace"
    DENY = "deny"


class PromptProvenance(StrictDomainModel):
    """Request-local correlation assertions for one rendered submission."""

    kind: Literal["rendered_prompt"]
    session_id: BoundedMetadataString
    submission_id: BoundedMetadataString


class HarnessAdmissionRequest(StrictDomainModel):
    """One complete harness-native rendered prompt."""

    request_body: bytes = Field(max_length=MAX_BODY_BYTES, repr=False)
    provenance: PromptProvenance


class HarnessAdmissionContext(StrictDomainModel):
    """Trusted admission context stamped outside the workload."""

    request_id: BoundedMetadataString
    sandbox_id: BoundedMetadataString
    middleware_name: BoundedMetadataString
    harness: Literal["pi"]
    harness_version: Literal["extension-v1"]
    hook: AdmissionHook
    schema_version: Literal["openshell.pi-input.v1"]
    provider_target: HttpTarget
    provider_adapter_schema: Literal["openai.chat-completions.v1"]


class HarnessAdmissionResult(StrictDomainModel):
    """Atomic policy decision returned to a managed harness."""

    hook: AdmissionHook
    decision: AdmissionDecision
    replacement_body: bytes | None = Field(
        default=None,
        max_length=MAX_BODY_BYTES,
        repr=False,
    )
    receipt: bytes | None = Field(
        default=None,
        min_length=1,
        max_length=8 * 1024,
        repr=False,
    )
    findings: tuple[SourcedFinding, ...] = Field(
        default=(), max_length=MAX_PROTO_FINDING_GROUPS
    )
    reason_code: ReasonCode | None = None
    policy_fingerprint: ScalarString

    @model_validator(mode="after")
    def _decision_contract_is_consistent(self) -> HarnessAdmissionResult:
        if self.decision is AdmissionDecision.DENY:
            if self.reason_code is None:
                raise ValueError("denial requires a reason code")
            if self.replacement_body is not None or self.receipt is not None:
                raise ValueError("denial cannot carry a replacement or receipt")
        else:
            if self.reason_code is not None:
                raise ValueError("allow decisions cannot carry a reason code")
            if (
                self.decision is AdmissionDecision.REPLACE
                and self.replacement_body is None
            ):
                raise ValueError("replace decisions require a replacement body")
            if (
                self.decision is AdmissionDecision.ALLOW
                and self.replacement_body is not None
            ):
                raise ValueError("allow decisions cannot carry a replacement body")
            if self.receipt is None:
                raise ValueError("admission requires a receipt")
        return self


__all__ = [
    "AdmissionDecision",
    "AdmissionHook",
    "HarnessAdmissionContext",
    "HarnessAdmissionRequest",
    "HarnessAdmissionResult",
    "PromptProvenance",
    "PI_HARNESS_VERSION",
]
