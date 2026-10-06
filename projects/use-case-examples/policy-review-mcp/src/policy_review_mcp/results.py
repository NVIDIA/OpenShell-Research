# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Discoverable MCP output contracts; external prover payloads remain opaque."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from policy_review_mcp.policy import SourceLocation


class ResultModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChoiceAnswer(ResultModel):
    type: Literal["choice"]
    value: str
    probabilities: dict[str, float]
    confidence: float = Field(description="Model certainty, not probability of policy safety.")


class ScoreAnswer(ResultModel):
    type: Literal["score"]
    value: float = Field(description="Expected excess score: 0 fits, 1 some excess, 2 substantial.")
    probabilities: dict[str, float]
    confidence: float


class AssessmentUncertainty(ResultModel):
    task_justification: bool
    excess_scope: bool
    context_gap: bool
    write_necessity: bool


class Assessment(ResultModel):
    target_pointer: str
    context_pointers: list[str]
    kind: str
    summary: str
    locations: list[SourceLocation]
    task_justification: ChoiceAnswer
    excess_scope: ScoreAnswer
    context_gap: ChoiceAnswer
    write_necessity: ChoiceAnswer | None
    uncertainty: AssessmentUncertainty
    contradictions: list[str]


class Finding(ResultModel):
    target_pointer: str
    reason: Literal[
        "permission_not_justified",
        "resource_scope_too_broad",
        "write_not_required",
        "missing_runtime_context",
    ]
    message: str
    locations: list[SourceLocation]
    probabilities: dict[str, float]
    confidence: float
    actionable_guidance: bool = Field(
        description="Evidence supports considering a change, not approval or authorization to edit."
    )
    blocked_by: list[str] = Field(description="Reasons guidance is not actionable.")


class CustomAnswer(ChoiceAnswer):
    id: str
    instructions: str
    pointers: list[str]
    criteria: dict[str, str]
    uncertain: bool
    diagnostic_outcomes: dict[str, Literal["justified", "unjustified"]] | None = None
    diagnostic_status: (
        Literal[
            "aligned", "conflict", "uncertain", "core_uncertain", "none_fit", "insufficient_context"
        ]
        | None
    ) = Field(default=None, description="Independent diagnostic compared to core task fit.")


class UnassessedField(ResultModel):
    pointer: str
    reason: str


class ReviewCoverage(ResultModel):
    supported_targets: list[str]
    unassessed: list[UnassessedField]
    inventory: list[str]


class JevReport(ResultModel):
    """Advisory task-fit report. Complete does not imply approval or full policy coverage."""

    schema_version: Literal[2]
    status: Literal["complete", "incomplete", "invalid_input", "unavailable"] = Field(
        description="Incomplete means uncertainty/context/conflicts; inspect individual findings."
    )
    candidate_sha256: str = Field(description="SHA-256 of exact candidate UTF-8 bytes.")
    review_input_sha256: str = Field(description="Fingerprint of candidate plus all review inputs.")
    model_request_attempted: bool
    model: str
    rubric_version: str
    catalog_version: str
    semantics_version: str
    coverage: ReviewCoverage
    assessments: list[Assessment]
    findings: list[Finding]
    custom_answers: list[CustomAnswer]
    reason: str | None = None
    timings_ms: dict[str, float]
    summary: str


class ProverReport(ResultModel):
    """Local boundary result, not a task-fit judgment or approval."""

    schema_version: Literal[1]
    status: Literal["complete", "unresolved", "adapter_error"] = Field(
        description="Complete includes both within and exceeds; it does not itself mean pass."
    )
    within_boundary: bool = Field(
        description="Only status complete AND within_boundary true passes the boundary gate."
    )
    candidate_sha256: str
    boundary_sha256: str | None
    result: Literal[
        "within_boundary",
        "exceeds_boundary",
        "unsupported",
        "inconclusive",
        "error",
        "adapter_error",
    ]
    prover_version: str | None = None
    coverage: dict[str, Any] | None = Field(
        default=None, description="External prover's own coverage, separate from JEV coverage."
    )
    counterexample: dict[str, Any] | None = None
    reason_code: str | None = None
    reason: str | None = None
    prover_report: Any = Field(
        description="Unmodified external JSON, including invalid output on errors."
    )
    timings_ms: dict[str, float]
    summary: str
