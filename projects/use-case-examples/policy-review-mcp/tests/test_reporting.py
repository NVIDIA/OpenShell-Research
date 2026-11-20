# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from io import StringIO

import pytest
from rich.console import Console

from policy_review_mcp.reporting import print_review_report


def test_unavailable_prover_explains_why_jev_was_skipped() -> None:
    output = _render(
        {
            "prover": {
                "status": "adapter_error",
                "within_boundary": False,
                "reason_code": "prover_unavailable",
                "reason": "No such file: openshell-prover",
            },
            "jev": {"status": "not_assessed"},
            "combined": False,
        }
    )
    assert "NOT VERIFIED" in output
    assert "SKIPPED" in output
    assert "JEV was not called because the prover did not pass" in output
    assert "prover.toml" in output
    assert "No such file: openshell-prover" in output


@pytest.mark.parametrize("matched", [True, False])
def test_uncertain_review_preserves_evidence_and_never_recommends_edit(matched: bool) -> None:
    report = _report()
    report["combined"] = matched
    output = _render(report)
    assert "WITHIN BOUNDARY" in output
    assert "PARTIAL / UNCERTAIN" in output
    assert "UNCERTAIN" in output
    assert "Needs investigation" in output
    assert "Consider a change" not in output
    assert "Confidence 34%" in output
    assert "Selected probability 34%" in output
    assert "1.50 / 2" in output
    assert "candidate:4:3" in output
    assert "/process" in output
    assert "Not assessed by JEV" in output
    assert "[red]literal[/red]" in output
    if not matched:
        assert "Candidate fingerprints do not match" in output


def test_fingerprint_mismatch_suppresses_otherwise_actionable_guidance() -> None:
    report = _report()
    report["combined"] = False
    report["jev"]["findings"][0]["actionable_guidance"] = True
    output = _render(report)
    assert "Reports cannot be combined" in output
    assert "Consider a change" not in output


def test_actionable_finding_is_labeled_without_approval() -> None:
    report = _report()
    report["jev"]["status"] = "complete"
    report["jev"]["findings"][0]["actionable_guidance"] = True
    report["jev"]["assessments"][0]["uncertainty"] = {}
    output = _render(report)
    assert "Consider a change" in output
    assert "Rerun the prover after any policy edit" in output
    assert "This is not approval" in output


@pytest.mark.parametrize("status", ["unavailable", "invalid_input"])
def test_jev_failures_remain_distinct_from_prover_result(status: str) -> None:
    report = _report()
    report["jev"] = {"status": status, "reason": "Test failure"}
    output = _render(report)
    assert "WITHIN BOUNDARY" in output
    assert "Test failure" in output
    assert "Resolve the JEV error" in output


def test_boundary_counterexample_and_custom_answers_are_visible() -> None:
    report = _report()
    report["prover"].update(
        result="exceeds_boundary", within_boundary=False, counterexample={"path": "/private"}
    )
    report["jev"] = {"status": "not_assessed"}
    report["combined"] = False
    output = _render(report)
    assert "EXCEEDS BOUNDARY" in output
    assert "Boundary counterexample" in output
    assert "/private" in output

    report = _report()
    report["jev"]["custom_answers"] = [
        {
            "id": "checkout",
            "value": "read_only",
            "confidence": 0.8,
            "probabilities": {"read_only": 0.9, "write": 0.1},
        }
    ]
    output = _render(report)
    assert "Custom answers" in output
    assert "caller interpretation required" in output
    assert "read_only: 90%" in output


def test_compact_report_keeps_uncertainty_and_coverage_without_diagnostics() -> None:
    stream = StringIO()
    print_review_report(_report(), console=Console(file=stream, width=80), scenario="test")
    output = stream.getvalue()
    assert len(output.splitlines()) < 35
    assert "Uncertain" in output
    assert "Investigate" in output
    assert "/process" in output
    assert "Confidence" not in output
    assert "candidate:4:3" not in output
    assert "Follow-up" not in output
    assert "Diagnostic" not in output


def test_custom_near_tie_shows_question_descriptions_and_uncertainty() -> None:
    report = _report()
    report["jev"]["custom_answers"] = [
        {
            "id": "checkout",
            "instructions": "Which checkout mode fits?",
            "pointers": ["/filesystem_policy/read_write/0"],
            "criteria": {"read": "Read-only checkout", "write": "Writable checkout"},
            "value": "read",
            "confidence": 0.35,
            "uncertain": True,
            "probabilities": {"read": 0.51, "write": 0.49},
        }
    ]
    output = _render(report)
    assert "UNCERTAIN" in output
    assert "Which checkout mode fits?" in output
    assert "Read-only checkout: 51%" in output
    assert "Writable checkout: 49%" in output


def _render(report: dict) -> str:
    stream = StringIO()
    print_review_report(
        report,
        console=Console(file=stream, width=120, color_system=None),
        scenario="test",
        details=True,
    )
    return stream.getvalue()


def _report() -> dict:
    choice = {
        "type": "choice",
        "value": "unjustified",
        "confidence": 0.34,
        "probabilities": {"unjustified": 0.34, "justified": 0.33, "insufficient_context": 0.33},
    }
    return {
        "task": "Read [red]literal[/red] without modifying it.",
        "prover": {"status": "complete", "within_boundary": True},
        "combined": True,
        "jev": {
            "status": "incomplete",
            "assessments": [
                {
                    "target_pointer": "fs",
                    "summary": "Read [red]literal[/red]",
                    "task_justification": choice,
                    "excess_scope": {
                        "type": "score",
                        "value": 1.5,
                        "confidence": 0.2,
                        "probabilities": {"0": 0.1, "1": 0.3, "2": 0.6},
                    },
                    "uncertainty": {"task_justification": True, "excess_scope": True},
                    "locations": [
                        {"pointer": "/filesystem_policy/read_only/0", "line": 4, "column": 3}
                    ],
                }
            ],
            "findings": [
                {
                    "target_pointer": "fs",
                    "message": "Scope may be broader than needed.",
                    "actionable_guidance": False,
                }
            ],
            "coverage": {
                "unassessed": [{"pointer": "/process", "reason": "unsupported_policy_family"}]
            },
        },
    }
