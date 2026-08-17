# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
from pathlib import Path

import pytest

from openshell_agent_runner.artifacts import atomic_publish, validate_artifact
from openshell_agent_runner.config import OutputConfig
from openshell_agent_runner.errors import ArtifactError


def output(max_bytes: int = 1000) -> OutputConfig:
    return OutputConfig.model_validate(
        {
            "type": "document_review",
            "contract": {
                "reviewer_id": "general",
                "criteria": ["clarity"],
                "max_findings": 2,
            },
            "sandbox_path": "/sandbox/artifacts/result.json",
            "max_bytes": max_bytes,
        }
    )


def valid_review() -> dict[str, object]:
    return {
        "reviewer_id": "general",
        "model_id": "test-model",
        "source_revision": "abc123",
        "source_content_digest": "a" * 64,
        "criterion_scores": [
            {"criterion": "clarity", "score": 4, "explanation": "Clear."}
        ],
        "overall_score": 100,
        "verdict": "pass",
        "confidence": "high",
        "findings": [],
        "overall_assessment": "The document is clear.",
    }


def test_valid_document_review_and_atomic_publish(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    source.write_text(json.dumps(valid_review()))
    assert validate_artifact(source, output(), "test-model").verdict == "pass"
    destination = tmp_path / "out" / "result.json"
    atomic_publish(source, destination)
    assert json.loads(destination.read_text()) == valid_review()


def test_invalid_and_oversized_document_reviews_fail(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    invalid = valid_review()
    invalid["reviewer_id"] = "wrong"
    source.write_text(json.dumps(invalid))
    with pytest.raises(ArtifactError, match="DocumentReview contract"):
        validate_artifact(source, output(), "test-model")
    with pytest.raises(ArtifactError, match="maximum size"):
        validate_artifact(source, output(1), "test-model")


def test_document_review_contract_checks_model_and_criterion_order(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.json"
    invalid = valid_review()
    invalid["model_id"] = "other-model"
    invalid["criterion_scores"] = [
        {"criterion": "other", "score": 4, "explanation": "Clear."}
    ]
    source.write_text(json.dumps(invalid))

    with pytest.raises(ArtifactError) as caught:
        validate_artifact(source, output(), "test-model")

    assert "criterion order" in str(caught.value)
    assert "model_id" in str(caught.value)


@pytest.mark.parametrize("invalid_score", ["4", True])
def test_document_review_rejects_coerced_scores(
    tmp_path: Path, invalid_score: object
) -> None:
    source = tmp_path / "source.json"
    invalid = valid_review()
    invalid["overall_score"] = invalid_score
    source.write_text(json.dumps(invalid))

    with pytest.raises(ArtifactError, match="DocumentReview validation"):
        validate_artifact(source, output(), "test-model")


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
