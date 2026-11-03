# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from io import StringIO

import pytest
from pydantic import ValidationError
from rich.console import Console

from policy_review_mcp.contracts import ExecutionContext, ReviewRequest, TargetedQuestion
from policy_review_mcp.jev import JevConfig, review_delegation
from policy_review_mcp.reporting import print_review_report

TARGET = "/filesystem_policy/read_only/0"
CRITERIA = {
    "unneeded": "The task never reads this directory.",
    "broad": "Reads are needed, but only inside a narrower directory.",
    "fits": "The task needs reads throughout this directory.",
}
OUTCOMES = {"unneeded": "unjustified", "broad": "unjustified", "fits": "justified"}


@pytest.mark.parametrize(
    "overrides",
    [
        {"pointers": [TARGET, "/filesystem_policy"]},
        {"diagnostic_outcomes": {"unneeded": "unjustified"}},
        {"diagnostic_outcomes": dict.fromkeys(CRITERIA, "unjustified")},
        {"diagnostic_outcomes": {**OUTCOMES, "none_fit": "justified"}},
        {"criteria": {**CRITERIA, "none_fit": "Override fallback"}},
        {"criteria": {**CRITERIA, "insufficient_context": "Override fallback"}},
        {"criteria": {**CRITERIA, "fits": "  "}},
    ],
)
def test_invalid_diagnostic_contracts_are_rejected(overrides) -> None:
    with pytest.raises(ValidationError):
        _question(**overrides)


@pytest.mark.parametrize("pointer", ["/filesystem_policy", "/process/run_as_user"])
def test_diagnostics_require_an_exact_supported_target_before_call(pointer) -> None:
    report = review_delegation(
        _request([_question(pointers=[pointer])]), JevConfig(), _must_not_call
    )
    assert report["status"] == "invalid_input"
    assert "exact supported target" in report["reason"]
    assert report["model_request_attempted"] is False


def test_duplicate_diagnostic_targets_are_rejected() -> None:
    report = review_delegation(
        _request([_question(), _question(id="another")]), JevConfig(), _must_not_call
    )
    assert report["status"] == "invalid_input"
    assert "duplicate diagnostic target" in report["reason"]


@pytest.mark.parametrize(
    "selection,core,confidence,expected",
    [
        ("unneeded", "unjustified", 0.9, "aligned"),
        ("broad", "unjustified", 0.9, "aligned"),
        ("fits", "justified", 0.9, "aligned"),
        ("fits", "unjustified", 0.9, "conflict"),
        ("unneeded", "justified", 0.9, "conflict"),
        ("unneeded", "unjustified", 0.2, "uncertain"),
        ("none_fit", "unjustified", 0.9, "none_fit"),
        ("insufficient_context", "unjustified", 0.9, "insufficient_context"),
        ("unneeded", "insufficient_context", 0.9, "core_uncertain"),
    ],
)
def test_diagnostics_are_one_batched_independent_choice(selection, core, confidence, expected):
    calls = []

    def model(state, questions, config):
        calls.append((state, questions))
        answers = _answers(questions, selection, core)
        answers["custom.diagnosis"]["confidence"] = confidence
        return answers

    report = review_delegation(_request(), JevConfig(), model)
    assert len(calls) == 1
    state, questions = calls[0]
    assert len(questions) == 7  # Two read targets, three core questions each, one diagnostic.
    assert questions["custom.diagnosis"]["criteria"] == {
        **CRITERIA,
        "none_fit": "None of the named alternatives fit.",
        "insufficient_context": "The supplied state is insufficient to choose.",
    }
    assert "hypotheses, not facts" in questions["custom.diagnosis"]["instructions"]
    assert state["custom_question_context"][0]["references"][0]["value"] == "/workspace"
    answer = report["custom_answers"][0]
    assert answer["diagnostic_status"] == expected
    assert answer["diagnostic_outcomes"] == OUTCOMES
    assert report["status"] == ("complete" if expected == "aligned" else "incomplete")
    if expected == "conflict":
        for finding in report["findings"]:
            assert finding["actionable_guidance"] is False
            assert "diagnostic_conflict" in finding["blocked_by"]
    elif core == "unjustified":
        # An uncertain/none-fit diagnostic never manufactures or erases independent core evidence.
        assert report["findings"][0]["actionable_guidance"] is True


def test_diagnostic_near_tie_and_uncertain_core_are_not_confident_disagreements() -> None:
    def model(state, questions, config):
        answers = _answers(questions, "fits", "unjustified")
        answers["custom.diagnosis"]["probabilities"] = {
            "fits": 0.48,
            "unneeded": 0.46,
            "broad": 0.02,
            "none_fit": 0.02,
            "insufficient_context": 0.02,
        }
        return answers

    report = review_delegation(_request(), JevConfig(), model)
    assert report["custom_answers"][0]["diagnostic_status"] == "uncertain"
    assert report["findings"][0]["actionable_guidance"] is True

    def uncertain_core(state, questions, config):
        answers = _answers(questions, "fits", "unjustified")
        answers[f"{TARGET}.justification"]["confidence"] = 0.2
        return answers

    report = review_delegation(_request(), JevConfig(), uncertain_core)
    assert report["custom_answers"][0]["diagnostic_status"] == "core_uncertain"
    assert report["findings"][0]["actionable_guidance"] is False


@pytest.mark.parametrize("selection", ["unneeded", "fits", "none_fit", "insufficient_context"])
@pytest.mark.parametrize("width", [80, 120])
def test_compact_diagnostic_column_is_local_and_uses_descriptions(selection, width) -> None:
    review = review_delegation(
        _request(), JevConfig(), lambda s, q, c: _answers(q, selection, "unjustified")
    )
    report = {
        "prover": {"status": "complete", "within_boundary": True},
        "jev": review,
        "combined": True,
    }
    stream = StringIO()
    print_review_report(report, console=Console(file=stream, width=width), scenario="diagnostic")
    output = stream.getvalue()
    assert "Diagnostic" in output
    assert "Follow-up" not in output
    assert "Custom answers" not in output  # No duplicate block in the compact view.
    assert "Confidence" not in output
    assert "—" in output  # Other target has no diagnostic.
    normalized = " ".join(output.replace("│", " ").split())
    if selection == "fits":
        assert "CONFLICT" in output
        assert "Change supported" not in output
    elif selection == "none_fit":
        assert "None of the named" in normalized
    elif selection == "insufficient_context":
        assert "insufficient" in normalized
        assert "choose." in normalized
    else:
        assert "The task never" in normalized
    assert len(output.splitlines()) < 45


def test_diagnostic_choices_change_review_fingerprint_not_candidate() -> None:
    def model(s, q, c):
        return _answers(q, "unneeded", "unjustified")

    original = review_delegation(_request(), JevConfig(), model)
    changed = review_delegation(
        _request([_question(criteria={**CRITERIA, "unneeded": "Different hypothesis."})]),
        JevConfig(),
        model,
    )
    assert original["candidate_sha256"] == changed["candidate_sha256"]
    assert original["review_input_sha256"] != changed["review_input_sha256"]


def test_diagnostic_conflict_blocks_only_its_exact_target() -> None:
    other = "/filesystem_policy/read_only/1"

    def model(s, q, c):
        answers = _answers(q, "fits", "unjustified")
        answers[f"{other}.justification"] = answers[f"{TARGET}.justification"].copy()
        return answers

    report = review_delegation(_request(), JevConfig(), model)
    by_pointer = {finding["target_pointer"]: finding for finding in report["findings"]}
    assert by_pointer[TARGET]["actionable_guidance"] is False
    assert by_pointer[other]["actionable_guidance"] is True
    assert "diagnostic_conflict" not in by_pointer[other]["blocked_by"]


def test_uncertain_diagnostic_display_preserves_full_evidence_on_request() -> None:
    def model(s, q, c):
        answers = _answers(q, "unneeded", "unjustified")
        answers["custom.diagnosis"]["confidence"] = 0.2
        return answers

    review = review_delegation(_request(), JevConfig(), model)
    report = {
        "prover": {"status": "complete", "within_boundary": True},
        "jev": review,
        "combined": True,
    }
    for details in (False, True):
        stream = StringIO()
        print_review_report(
            report, console=Console(file=stream, width=120), scenario="test", details=details
        )
        output = stream.getvalue()
        assert "UNCERTAIN preference" in output
        assert ("Custom answers" in output) is details
        if details:
            assert "Confidence 20%" in output
            assert "None of the named alternatives fit" in output
            assert "The supplied state is insufficient to choose" in output


def _question(**overrides):
    return TargetedQuestion(
        **{
            "id": "diagnosis",
            "pointers": [TARGET],
            "instructions": "Which explanation fits this entry?",
            "criteria": CRITERIA,
            "diagnostic_outcomes": OUTCOMES,
            **overrides,
        }
    )


def _request(questions=None):
    return ReviewRequest(
        task="Read the prepared checkout.",
        candidate_policy=(
            "version: 1\nfilesystem_policy:\n  read_only: [/workspace, /runtime]\n"
            "process: {run_as_user: sandbox}\n"
        ),
        execution_context=ExecutionContext(),
        questions=[_question()] if questions is None else questions,
    )


def _must_not_call(*args):
    pytest.fail("invalid diagnostic reached the model")


def _answers(questions, selection, core):
    answers = {}
    for identifier, spec in questions.items():
        if spec["type"] == "score":
            answers[identifier] = {
                "type": "score",
                "value": 0.1,
                "probabilities": {"0": 0.9, "1": 0.1, "2": 0.0},
                "confidence": 0.9,
            }
            continue
        value = (
            selection
            if identifier.startswith("custom.")
            else core
            if identifier == f"{TARGET}.justification"
            else "none"
            if identifier.endswith(".context")
            else "justified"
        )
        answers[identifier] = {
            "type": "choice",
            "value": value,
            "confidence": 0.9,
            "probabilities": {
                key: 0.9 if key == value else 0.1 / (len(spec["criteria"]) - 1)
                for key in spec["criteria"]
            },
        }
    return answers
