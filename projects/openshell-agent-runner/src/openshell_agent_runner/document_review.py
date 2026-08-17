# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Built-in structured document-review artifact contract."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from openshell_agent_runner.config import MODEL_IDENTIFIER_PATTERN

REVIEW_IDENTIFIER_PATTERN = r"^[a-z][a-z0-9_-]{0,63}$"


class ReviewModel(BaseModel):
    """Forbid undeclared fields in agent-produced review artifacts."""

    model_config = ConfigDict(extra="forbid", strict=True)


class CriterionScore(ReviewModel):
    criterion: Annotated[str, Field(pattern=REVIEW_IDENTIFIER_PATTERN)]
    score: int = Field(ge=0, le=4)
    explanation: str = Field(min_length=1, max_length=1200)


class DocumentFinding(ReviewModel):
    severity: Literal["advisory", "warning", "blocking"]
    quote: str = Field(min_length=1, max_length=500)
    source_path: str = Field(min_length=1, max_length=4096)
    line: int = Field(ge=1)
    column: int = Field(ge=1)
    explanation: str = Field(min_length=1, max_length=1200)
    recommended_action: str = Field(min_length=1, max_length=1200)


class DocumentReview(ReviewModel):
    reviewer_id: Annotated[str, Field(pattern=REVIEW_IDENTIFIER_PATTERN)]
    model_id: str = Field(pattern=MODEL_IDENTIFIER_PATTERN)
    source_revision: str = Field(min_length=1, max_length=256)
    source_content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    criterion_scores: list[CriterionScore]
    overall_score: int = Field(ge=0, le=100)
    verdict: Literal["pass", "revise", "manual_review"]
    confidence: Literal["low", "medium", "high"]
    findings: list[DocumentFinding]
    overall_assessment: str = Field(min_length=1, max_length=1200)
    request_id: str | None = Field(default=None, min_length=1, max_length=256)
    response_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


def document_review_schema(
    *,
    reviewer_id: str,
    model_id: str,
    criteria: list[str],
    max_findings: int,
) -> dict[str, Any]:
    """Generate the Pi-facing schema from the same Pydantic model used by OAR."""

    schema = DocumentReview.model_json_schema(mode="validation")
    properties = schema["properties"]
    properties["reviewer_id"] = {"const": reviewer_id, "type": "string"}
    properties["model_id"] = {"const": model_id, "type": "string"}
    properties["criterion_scores"] = {
        "type": "array",
        "minItems": len(criteria),
        "maxItems": len(criteria),
        "prefixItems": [
            {
                "allOf": [
                    {"$ref": "#/$defs/CriterionScore"},
                    {
                        "properties": {"criterion": {"const": criterion}},
                        "required": ["criterion"],
                    },
                ]
            }
            for criterion in criteria
        ],
        "items": False,
    }
    findings = properties["findings"]
    if isinstance(findings, dict):
        findings["maxItems"] = max_findings
    return schema
