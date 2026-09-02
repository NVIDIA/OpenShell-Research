# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Public, transport-neutral models for harness admission."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from egress_gate.base import StrictDomainModel
from egress_gate.constants import MAX_PROTO_FINDING_GROUPS
from egress_gate.request import HttpTarget
from egress_gate.result import ReasonCode, SourcedFinding
from egress_gate.string_validators import BoundedMetadataString, ScalarString

MAX_ADMISSION_BODY_BYTES = 4 * 1024 * 1024
PI_HARNESS_VERSION = "sdk-v1"


class AdmissionHook(StrEnum):
    """Supported harness admission boundaries."""

    USER_MESSAGE = "user_message"
    TOOL_RESULT = "tool_result"
    ASSISTANT_MESSAGE = "assistant_message"
    COMPACTION_SUMMARY = "compaction_summary"
    BRANCH_SUMMARY = "branch_summary"
    EXTENSION_MESSAGE = "extension_message"
    BASH_EXECUTION = "bash_execution"


class AdmissionDecision(StrEnum):
    """Disposition of a harness request."""

    ALLOW = "allow"
    REPLACE = "replace"
    DENY = "deny"


class AdmissionProvenance(StrictDomainModel):
    """Request-local correlation assertions for one context addition."""

    session_id: BoundedMetadataString
    submission_id: BoundedMetadataString


class HarnessAdmissionRequest(StrictDomainModel):
    """One complete harness-native context addition."""

    request_body: bytes = Field(max_length=MAX_ADMISSION_BODY_BYTES, repr=False)
    provenance: AdmissionProvenance


class HarnessAdmissionContext(StrictDomainModel):
    """Trusted admission context stamped outside the workload."""

    request_id: BoundedMetadataString
    sandbox_id: BoundedMetadataString
    middleware_name: BoundedMetadataString
    harness: ScalarString
    harness_version: Literal["sdk-v1"]
    hook: AdmissionHook
    schema_version: ScalarString
    provider_target: HttpTarget
    provider_adapter_schema: Literal["openai.request.v1"]


class HarnessAdmissionResult(StrictDomainModel):
    """Atomic policy decision returned to a managed harness."""

    hook: AdmissionHook
    decision: AdmissionDecision
    replacement_body: bytes | None = Field(
        default=None,
        max_length=MAX_ADMISSION_BODY_BYTES,
        repr=False,
    )
    attestation: bytes | None = Field(
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
            if self.replacement_body is not None or self.attestation is not None:
                raise ValueError("denial cannot carry a replacement or attestation")
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
            if self.attestation is None:
                raise ValueError("admission requires an attestation")
        return self


__all__ = [
    "AdmissionDecision",
    "AdmissionHook",
    "AdmissionProvenance",
    "HarnessAdmissionContext",
    "HarnessAdmissionRequest",
    "HarnessAdmissionResult",
    "MAX_ADMISSION_BODY_BYTES",
    "PI_HARNESS_VERSION",
]
