# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
from pathlib import Path

import pytest

from openshell_agent_runner.artifacts import (
    MAX_ARTIFACT_BYTES,
    atomic_publish,
    validate_artifact,
)
from openshell_agent_runner.errors import ArtifactError

REPOSITORY = Path(__file__).resolve().parents[3]
DEV_NOTE_SCHEMA = (
    REPOSITORY
    / ".github/openshell-agents/profiles/dev-note-reviewer/schemas/review.json"
)
PACKAGED_PROFILE_SCHEMAS = (
    REPOSITORY / "projects/openshell-agent-runner/src/openshell_agent_runner/profiles"
)


def test_plain_result_is_accepted_and_published(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.write_text("Useful result.\n")

    validate_artifact(source)
    destination = tmp_path / "out" / "result.md"
    atomic_publish(source, destination)

    assert destination.read_text() == "Useful result.\n"


def test_json_result_is_validated_against_configured_schema(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.write_text('{"status":"pass"}\n')
    schema = tmp_path / "schema.json"
    schema.write_text(
        json.dumps(
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["status"],
                "properties": {"status": {"enum": ["pass", "fail"]}},
            }
        )
    )

    validate_artifact(source, schema)

    source.write_text('{"status":"unknown"}\n')
    with pytest.raises(ArtifactError, match="output schema validation"):
        validate_artifact(source, schema)


def test_invalid_json_fails_when_schema_is_configured(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.write_text("not json")
    schema = tmp_path / "schema.json"
    schema.write_text('{"type":"object"}')

    with pytest.raises(ArtifactError, match="not valid JSON"):
        validate_artifact(source, schema)


def test_dev_note_schema_requires_each_editorial_criterion_in_order(
    tmp_path: Path,
) -> None:
    criteria = [
        "formulaic_language",
        "empty_emphasis",
        "repetitive_cadence",
        "unnecessary_summary",
        "inflated_claims",
        "vague_attribution",
        "directness",
    ]
    result = {
        "reviewer_id": "editorial",
        "model_id": "provider/model",
        "source_revision": "abc123",
        "source_content_digest": "0" * 64,
        "criterion_scores": [
            {"criterion": criterion, "score": 3, "explanation": "Clear."}
            for criterion in criteria
        ],
        "overall_score": 75,
        "verdict": "pass",
        "confidence": "high",
        "findings": [],
        "overall_assessment": "Ready.",
    }
    source = tmp_path / "review.json"
    source.write_text(json.dumps(result))
    validate_artifact(source, DEV_NOTE_SCHEMA)

    result["criterion_scores"][1]["criterion"] = "formulaic_language"
    source.write_text(json.dumps(result))
    with pytest.raises(ArtifactError, match="output schema validation"):
        validate_artifact(source, DEV_NOTE_SCHEMA)


@pytest.mark.parametrize(
    ("profile_name", "result"),
    [
        (
            "code-reviewer",
            {
                "verdict": "findings",
                "summary": "One material issue.",
                "criterion_scores": [
                    {
                        "criterion": "correctness",
                        "score": 70,
                        "explanation": "A boundary defect affects valid requests.",
                    },
                    {
                        "criterion": "robustness_security",
                        "score": 85,
                        "explanation": "No material robustness or security issue found.",
                    },
                    {
                        "criterion": "maintainability_complexity",
                        "score": 85,
                        "explanation": "The affected logic remains easy to follow.",
                    },
                    {
                        "criterion": "tests_verification",
                        "score": 75,
                        "explanation": "The boundary behavior lacks effective coverage.",
                    },
                    {
                        "criterion": "usability_integration",
                        "score": 85,
                        "explanation": "Integration behavior is otherwise coherent.",
                    },
                ],
                "overall_score": 80,
                "findings": [
                    {
                        "severity": "high",
                        "category": "correctness",
                        "title": "Wrong boundary check",
                        "path": "src/example.py",
                        "line": 12,
                        "evidence": "The final valid item is rejected.",
                        "impact": "Valid requests fail.",
                        "recommendation": "Use an inclusive upper bound.",
                    }
                ],
                "strengths": [],
                "limitations": [],
            },
        ),
        (
            "technical-writing-reviewer",
            {
                "verdict": "findings",
                "summary": "One unclear instruction.",
                "criterion_scores": [
                    {
                        "criterion": "accuracy_grounding",
                        "score": 90,
                        "explanation": "The claims are adequately grounded.",
                    },
                    {
                        "criterion": "clarity_precision",
                        "score": 65,
                        "explanation": "A key instruction is ambiguous.",
                    },
                    {
                        "criterion": "completeness",
                        "score": 75,
                        "explanation": "The working-directory context is missing.",
                    },
                    {
                        "criterion": "structure_navigation",
                        "score": 85,
                        "explanation": "The document is otherwise easy to navigate.",
                    },
                    {
                        "criterion": "audience_fit",
                        "score": 85,
                        "explanation": "The depth suits the intended reader.",
                    },
                    {
                        "criterion": "actionability_evidence",
                        "score": 80,
                        "explanation": "Most instructions support the intended task.",
                    },
                ],
                "overall_score": 80,
                "findings": [
                    {
                        "severity": "medium",
                        "category": "clarity",
                        "title": "Unspecified command location",
                        "quote": "Run the command.",
                        "line": 8,
                        "explanation": "The reader cannot tell where to run it.",
                        "recommendation": "Name the required working directory.",
                    }
                ],
                "strengths": [],
                "limitations": [],
            },
        ),
        (
            "slop-cop",
            {
                "verdict": "polish",
                "summary": "One formulaic opening.",
                "criterion_scores": [
                    {
                        "criterion": "substance_directness",
                        "score": 74,
                        "explanation": "One opener delays its useful claim.",
                    },
                    {
                        "criterion": "specificity",
                        "score": 84,
                        "explanation": "Claims are generally concrete.",
                    },
                    {
                        "criterion": "structural_naturalness",
                        "score": 82,
                        "explanation": "The broader structure serves the content.",
                    },
                    {
                        "criterion": "rhythm_style",
                        "score": 80,
                        "explanation": "The prose is readable outside the opening.",
                    },
                    {
                        "criterion": "distinctive_voice",
                        "score": 80,
                        "explanation": "The document mostly retains a clear voice.",
                    },
                ],
                "overall_score": 80,
                "findings": [
                    {
                        "prevalence": "isolated",
                        "category": "formulaic_structure",
                        "quote": "It is important to note that the API is stable.",
                        "line": 3,
                        "effect": "The opener delays the useful claim.",
                        "suggested_rewrite": "The API is stable.",
                    }
                ],
                "voice_to_preserve": [],
                "limitations": [],
            },
        ),
    ],
)
def test_packaged_profile_schemas_accept_expected_results(
    tmp_path: Path, profile_name: str, result: dict[str, object]
) -> None:
    source = tmp_path / "review.json"
    source.write_text(json.dumps(result))
    schema = PACKAGED_PROFILE_SCHEMAS / profile_name / "schemas/review.json"

    validate_artifact(source, schema)

    criterion_scores = result["criterion_scores"]
    assert isinstance(criterion_scores, list)
    first_score = criterion_scores[0]
    assert isinstance(first_score, dict)
    original_score = first_score["score"]
    first_score["score"] = 101
    source.write_text(json.dumps(result))
    with pytest.raises(ArtifactError, match="output schema validation"):
        validate_artifact(source, schema)

    first_score["score"] = original_score
    result["unexpected"] = True
    source.write_text(json.dumps(result))
    with pytest.raises(ArtifactError, match="output schema validation"):
        validate_artifact(source, schema)


@pytest.mark.parametrize("content", ["", "x" * (MAX_ARTIFACT_BYTES + 1)])
def test_empty_and_oversized_results_fail(tmp_path: Path, content: str) -> None:
    source = tmp_path / "source"
    source.write_text(content)

    with pytest.raises(ArtifactError):
        validate_artifact(source)


def test_symlink_destination_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.write_text("safe")
    target = tmp_path / "target"
    target.write_text("existing")
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(ArtifactError, match="symlink"):
        atomic_publish(source, link)


def test_unwritable_publication_target_is_reported_as_artifact_error(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.write_text("safe")
    destination = tmp_path / "directory"
    destination.mkdir()

    with pytest.raises(ArtifactError, match="cannot publish artifact"):
        atomic_publish(source, destination)
