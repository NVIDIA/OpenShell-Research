"""Immutable analysis and report records."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from slop_cop.config import CategoryId, RuleId, Severity, StrictModel
from slop_cop.document import DocumentMetrics, Span

BoundedText = Annotated[str, StringConstraints(max_length=1_000)]
Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Revision = Annotated[str, StringConstraints(min_length=7, max_length=64, pattern=r"^[0-9a-f]+$")]


class AnalysisState(StrEnum):
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    ERROR = "error"
    NOT_APPLICABLE = "not_applicable"


class Decision(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    OVERRIDDEN = "overridden"
    NOT_APPLICABLE = "not_applicable"


class Finding(StrictModel):
    rule_id: RuleId
    category: CategoryId
    severity: Severity
    source_path: str = Field(min_length=1, max_length=4_096)
    span: Span | None
    line: int | None = Field(default=None, ge=1)
    column: int | None = Field(default=None, ge=1)
    excerpt: BoundedText = ""
    normalized_key: str = Field(min_length=1, max_length=256)
    score_group: str = Field(min_length=1, max_length=128)
    explanation: BoundedText
    advice: BoundedText
    units: int = Field(default=1, ge=1, le=5_000)
    advisory: bool = False
    chargeable: bool = True
    suppressed: bool = False
    blocking: bool = False
    suppression_reason: BoundedText | None = None
    related_rule_ids: tuple[RuleId, ...] = ()

    @model_validator(mode="after")
    def validate_location_and_state(self) -> Finding:
        if self.span is None and (self.line is not None or self.column is not None):
            raise ValueError("document-scoped findings cannot have line or column")
        if self.span is not None and (self.line is None or self.column is None):
            raise ValueError("source-mapped findings require line and column")
        if self.suppressed and not self.suppression_reason:
            raise ValueError("suppressed findings require a reason")
        if not self.suppressed and self.suppression_reason is not None:
            raise ValueError("unsuppressed findings cannot have a suppression reason")
        if self.advisory and self.chargeable:
            raise ValueError("advisory findings cannot be chargeable")
        return self


class DensityMeasurement(StrictModel):
    unit: Literal["word", "sentence", "paragraph"]
    window: int = Field(ge=1)
    allowed_units: int = Field(ge=0)
    peak_units: int = Field(ge=0)
    peak_excess: int = Field(ge=0)
    cost: float = Field(ge=0, le=100)
    window_span: Span | None = None


class RuleCost(StrictModel):
    rule_id: RuleId
    deduplicated_units: int = Field(ge=0)
    allowance: int = Field(ge=0)
    document_excess: int = Field(ge=0)
    base_cost: float = Field(ge=0, le=100)
    density: DensityMeasurement | None = None
    cap: float = Field(ge=0, le=100)
    charged_cost: float = Field(ge=0, le=100)

    @model_validator(mode="after")
    def validate_arithmetic_bounds(self) -> RuleCost:
        if self.document_excess != max(0, self.deduplicated_units - self.allowance):
            raise ValueError("document_excess does not match units and allowance")
        if self.charged_cost > self.cap:
            raise ValueError("charged rule cost cannot exceed its cap")
        return self


class CategoryCost(StrictModel):
    category: CategoryId
    rule_cost: float = Field(ge=0, le=100)
    density: DensityMeasurement | None = None
    cap: float = Field(ge=0, le=100)
    charged_cost: float = Field(ge=0, le=100)

    @model_validator(mode="after")
    def validate_cap(self) -> CategoryCost:
        if self.charged_cost > self.cap:
            raise ValueError("charged category cost cannot exceed its cap")
        return self


class RuleExecutionError(StrictModel):
    rule_id: RuleId | None = None
    source_path: str | None = Field(default=None, max_length=4_096)
    error_code: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")
    message: BoundedText
    fatal: bool


class AppliedSuppression(StrictModel):
    rule_ids: tuple[RuleId, ...] = Field(min_length=1)
    reason: BoundedText
    directive_span: Span
    target_span: Span
    suppressed_finding_ids: tuple[str, ...] = ()


class ExternalAudit(StrictModel):
    rule_id: RuleId
    rule_version: int = Field(ge=1)
    service: str = Field(min_length=1, max_length=64)
    endpoint_hostname: str = Field(min_length=1, max_length=253)
    content_digest: Digest
    request_schema_version: str = Field(min_length=1, max_length=64)
    response_schema_version: str | None = Field(default=None, max_length=64)
    service_request_id: str | None = Field(default=None, max_length=256)
    judge_revision: str | None = Field(default=None, max_length=128)
    attempts: int = Field(ge=1, le=3)
    latency_ms: int = Field(ge=0, le=600_000)
    outcome: str = Field(min_length=1, max_length=64)
    response_digest: Digest | None = None


class FindingChange(StrictModel):
    added: tuple[Finding, ...] = ()
    removed: tuple[Finding, ...] = ()
    persistent: tuple[Finding, ...] = ()


class BaseComparison(StrictModel):
    score: int | None = Field(default=None, ge=0, le=100)
    delta: int | None = Field(default=None, ge=-100, le=100)
    analysis_state: AnalysisState
    findings: FindingChange = FindingChange()
    errors: tuple[RuleExecutionError, ...] = ()


class FileResult(StrictModel):
    path: str = Field(min_length=1, max_length=4_096)
    analysis_state: AnalysisState
    decision: Decision
    score: int | None = Field(default=None, ge=0, le=100)
    threshold: int = Field(ge=0, le=100)
    hard_fail: bool = False
    metrics: DocumentMetrics
    findings: tuple[Finding, ...] = ()
    suppressions: tuple[AppliedSuppression, ...] = ()
    rule_costs: tuple[RuleCost, ...] = ()
    category_costs: tuple[CategoryCost, ...] = ()
    errors: tuple[RuleExecutionError, ...] = ()
    base: BaseComparison | None = None

    @model_validator(mode="after")
    def validate_decision(self) -> FileResult:
        if self.analysis_state is AnalysisState.NOT_APPLICABLE:
            if self.decision is not Decision.NOT_APPLICABLE or self.score is not None:
                raise ValueError("not-applicable file results require no score and no decision")
        elif self.analysis_state is not AnalysisState.ERROR and self.score is None:
            raise ValueError("analyzed file results require a score")
        return self


class OverrideRecord(StrictModel):
    reviewer: str = Field(min_length=1, max_length=256)
    reason: BoundedText
    review_id: int = Field(ge=1)
    review_url: str = Field(min_length=1, max_length=2_048)
    head_sha: Revision


class RunResult(StrictModel):
    schema_version: Literal[1] = 1
    analysis_state: AnalysisState
    decision: Decision
    score: int | None = Field(default=None, ge=0, le=100)
    threshold: int = Field(ge=0, le=100)
    repository: str | None = Field(default=None, max_length=256)
    pull_request_number: int | None = Field(default=None, ge=1)
    base_sha: Revision | None = None
    head_sha: Revision | None = None
    tool_version: str = Field(min_length=1, max_length=64)
    config_digest: Digest
    files: tuple[FileResult, ...] = ()
    rule_errors: tuple[RuleExecutionError, ...] = ()
    external_audits: tuple[ExternalAudit, ...] = ()
    override: OverrideRecord | None = None

    @model_validator(mode="after")
    def validate_aggregate(self) -> RunResult:
        if self.analysis_state is AnalysisState.NOT_APPLICABLE:
            if self.decision is not Decision.NOT_APPLICABLE or self.score is not None or self.files:
                raise ValueError("not-applicable runs require no score, files, or policy decision")
            return self
        if not self.files:
            raise ValueError("applicable runs require at least one file result")
        scores = [item.score for item in self.files if item.score is not None]
        if scores and self.score != min(scores):
            raise ValueError("run score must be the minimum scored file result")
        if not scores and self.score is not None:
            raise ValueError("a run without scored file results cannot have a score")
        if self.decision is Decision.OVERRIDDEN and self.override is None:
            raise ValueError("overridden decisions require override metadata")
        if self.decision is not Decision.OVERRIDDEN and self.override is not None:
            raise ValueError("override metadata requires an overridden decision")
        if self.decision is Decision.OVERRIDDEN:
            if self.analysis_state is not AnalysisState.COMPLETE:
                raise ValueError("overrides require complete analysis")
            if not any(item.decision is Decision.FAIL for item in self.files):
                raise ValueError("overrides require a policy failure")
        return self


__all__ = [
    "AnalysisState",
    "AppliedSuppression",
    "BaseComparison",
    "CategoryCost",
    "Decision",
    "DensityMeasurement",
    "ExternalAudit",
    "FileResult",
    "Finding",
    "FindingChange",
    "OverrideRecord",
    "RuleCost",
    "RuleExecutionError",
    "RunResult",
]
