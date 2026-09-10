# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Verify saved live results: OAR_REVIEW_RESULTS=/path uv run pytest tests/reviewer_smoke.py.

The workflow calls OAR directly. This module only checks the resulting files and
is deliberately outside default offline test collection.
"""

import json
import os
from importlib.resources import files
from pathlib import Path

import pytest

from openshell_agent_runner.artifacts import validate_artifact

FIXTURES = Path(__file__).parent / "fixtures" / "reviewer-e2e"
CASES = json.loads((FIXTURES / "packaged-cases.json").read_text())


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_packaged_review(case: dict[str, str]) -> None:
    output = Path(os.environ["OAR_REVIEW_RESULTS"]) / f"{case['id']}.json"
    schema = files("openshell_agent_runner.profiles").joinpath(
        case["profile"], "schemas", "review.json"
    )
    validate_artifact(output, Path(str(schema)))
    result = json.loads(output.read_text())
    scores = [item["score"] for item in result["criterion_scores"]]
    assert abs(result["overall_score"] - sum(scores) / len(scores)) <= 0.5
    assert result["verdict"] == case["verdict"], result
    if case["verdict"] == "pass":
        assert result["findings"] == [], result
    else:
        assert result["findings"], result

    if case["profile"] == "technical-writing-reviewer":
        source = (FIXTURES / case["input"]).read_text()
        lines = source.splitlines()
        for finding in result["findings"]:
            assert finding["quote"] in source, finding
            assert 1 <= finding["line"] <= len(lines), finding
            assert finding["quote"].splitlines()[0] in lines[finding["line"] - 1], (
                finding
            )
    if case["id"] == "writing-defect":
        assert any(
            "three passing tests" in finding["quote"] for finding in result["findings"]
        ), result
    if case["id"] == "code-defect":
        assert any(
            finding["path"].endswith("mean.py") and finding["category"] == "correctness"
            for finding in result["findings"]
        ), result
