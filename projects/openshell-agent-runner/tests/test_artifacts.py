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
