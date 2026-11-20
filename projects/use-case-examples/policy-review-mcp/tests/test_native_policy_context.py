# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console
from ruamel.yaml import YAML

from policy_review_mcp.contracts import ExecutionContext, ReviewRequest, TargetedQuestion
from policy_review_mcp.jev import JevConfig, review_delegation
from policy_review_mcp.policy import parse_policy, resolve_pointer
from policy_review_mcp.reporting import print_review_report

FIXTURES = Path(__file__).parents[1] / "demo/fixtures"
ENDPOINT = "/network_policies/github/endpoints/0"


def test_native_documents_and_enclosing_context_reach_one_model_batch() -> None:
    candidate = (FIXTURES / "candidate-comment.yaml").read_text()
    starting = candidate.replace("method: POST", "method: PUT")
    calls = []

    def model(state, questions, config):
        calls.append(state)
        assert state["candidate_policy"] == YAML(typ="safe").load(candidate)
        assert state["starting_policy"] == YAML(typ="safe").load(starting)
        assert "permission_groups" not in state
        assert "trusted_operation_catalog" not in state
        assert len(questions) == 15  # Two filesystem entries and three REST rules.
        assert "/process" in {item["pointer"] for item in state["coverage"]["unassessed"]}
        assert (
            state["policy_semantics"]["openshell_revision"]
            == "484f0768fc6a0d93e0a2be295c1679aed24e18a9"
        )
        for target in state["review_targets"]:
            assert resolve_pointer(state["candidate_policy"], target["pointer"])[0]
            for pointer in target["context_pointers"]:
                assert resolve_pointer(state["candidate_policy"], pointer)[0]
            assert (
                target["pointer"] in questions[f"{target['pointer']}.justification"]["instructions"]
            )
        post = next(t for t in state["review_targets"] if t["pointer"] == f"{ENDPOINT}/rules/2")
        assert "/network_policies/github/binaries" in post["context_pointers"]
        assert ENDPOINT in post["context_pointers"]
        return _answers(state, questions)

    result = review_delegation(
        ReviewRequest(
            task="Read issue 42, return the summary; do not publish.",
            candidate_policy=candidate,
            starting_policy=starting,
            execution_context=ExecutionContext(),
        ),
        JevConfig(),
        model,
    )
    assert len(calls) == 1
    assert result["schema_version"] == 2
    assert result["semantics_version"] == "openshell-review-semantics-v1"
    findings = result["findings"]
    assert findings
    assert {f["target_pointer"] for f in findings} == {f"{ENDPOINT}/rules/2"}
    assert all(f["locations"][0]["pointer"] == f"{ENDPOINT}/rules/2" for f in findings)
    assert all(f["actionable_guidance"] for f in findings)
    assert "group_id" not in json.dumps(result)


def test_rule_targets_have_exact_source_lines_and_escaped_policy_names() -> None:
    candidate = (
        (FIXTURES / "candidate-comment.yaml")
        .read_text()
        .replace("  github:", "  'github/work~repo':")
    )
    parsed = parse_policy(candidate)
    network = [t for t in parsed.targets if t.kind == "network_policies.endpoints.rules.allow"]
    assert len(network) == 3
    for index, target in enumerate(network):
        assert target.pointer == f"/network_policies/github~1work~0repo/endpoints/0/rules/{index}"
        location = target.locations[0]
        assert "- allow:" in candidate.splitlines()[location.line - 1]
        assert resolve_pointer(parsed.data, target.pointer)[0]
    assert len({t.locations[0].line for t in network}) == 3


@pytest.mark.parametrize("change", ["audit", "query", "deny", "unknown_rule"])
def test_unsupported_rule_semantics_remain_visible_but_have_no_core_questions(change) -> None:
    candidate = (FIXTURES / "candidate-comment.yaml").read_text()
    if change == "audit":
        candidate = candidate.replace("enforcement: enforce", "enforcement: audit")
    elif change == "query":
        candidate = candidate.replace("method: POST,", "query: {state: open}, method: POST,")
    elif change == "deny":
        candidate = candidate.replace(
            "        rules:", "        deny_rules: [{method: POST, path: '**'}]\n        rules:"
        )
    else:
        candidate = candidate.replace(
            "- allow: {method: POST", "- unexpected: true\n            allow: {method: POST"
        )

    def model(state, questions, config):
        assert state["candidate_policy"] == YAML(typ="safe").load(candidate)
        assert not any(identifier.startswith("/network_policies/") for identifier in questions)
        assert ENDPOINT in {item["pointer"] for item in state["coverage"]["unassessed"]}
        return _answers(state, questions)

    result = review_delegation(
        ReviewRequest(
            task="Read issue 42.", candidate_policy=candidate, execution_context=ExecutionContext()
        ),
        JevConfig(),
        model,
    )
    assert result["model_request_attempted"] is True


def test_unknown_process_values_are_visible_without_custom_questions() -> None:
    values = []

    def model(state, questions, config):
        values.append(state["candidate_policy"]["process"]["run_as_user"])
        return _answers(state, questions)

    for identity in ("sandbox", "root"):
        review_delegation(
            ReviewRequest(
                task="Read the checkout.",
                candidate_policy=(
                    "version: 1\nfilesystem_policy:\n  read_only: [/workspace]\n"
                    f"process: {{run_as_user: {identity}}}\n"
                ),
                execution_context=ExecutionContext(),
            ),
            JevConfig(),
            model,
        )
    assert values == ["sandbox", "root"]


def test_rule_splitting_respects_question_limit_without_calling_model() -> None:
    def model(*args):
        pytest.fail("Oversized question batch must be rejected before model call")

    result = review_delegation(
        ReviewRequest(
            task="Read issue 42.",
            candidate_policy=(FIXTURES / "candidate-comment.yaml").read_text(),
            execution_context=ExecutionContext(),
        ),
        JevConfig(max_questions=14),
        model,
    )
    assert result["status"] == "invalid_input"
    assert "requires 15 questions" in result["reason"]


def test_custom_root_reference_reports_partial_coverage() -> None:
    def model(state, questions, config):
        reference = state["custom_question_context"][0]["references"][0]
        assert reference["coverage"] == "partial"
        assert reference["value"] == state["candidate_policy"]
        return _answers(state, questions)

    result = review_delegation(
        ReviewRequest(
            task="Read the checkout.",
            candidate_policy=(FIXTURES / "candidate-code-review-read.yaml").read_text(),
            execution_context=ExecutionContext(),
            questions=[
                TargetedQuestion(
                    id="overall",
                    pointers=[""],
                    instructions="Is context sufficient?",
                    criteria={"yes": "Enough context", "no": "Not enough context"},
                )
            ],
        ),
        JevConfig(),
        model,
    )
    assert result["model_request_attempted"] is True


def test_native_context_keeps_overlapping_paths_and_original_method_case() -> None:
    candidate = (
        (FIXTURES / "candidate-comment.yaml")
        .read_text()
        .replace("read_write: []", "read_write: [/usr]")
        .replace("method: GET", "method: get")
    )

    def model(state, questions, config):
        native = state["candidate_policy"]
        assert native["filesystem_policy"]["read_write"] == ["/usr"]
        assert "/usr/bin/gh" in native["filesystem_policy"]["read_only"]
        assert (
            native["network_policies"]["github"]["endpoints"][0]["rules"][0]["allow"]["method"]
            == "get"
        )
        assert (
            "ancestor"
            in state["policy_semantics"]["semantics"]["network_policies.binaries"]["explanation"]
        )
        return _answers(state, questions)

    result = review_delegation(
        ReviewRequest(
            task="Read issue 42.", candidate_policy=candidate, execution_context=ExecutionContext()
        ),
        JevConfig(),
        model,
    )
    assert result["model_request_attempted"] is True


@pytest.mark.parametrize("value", ["2026-09-22", ".nan", ".inf"])
def test_non_json_native_values_reject_before_model_request(value) -> None:
    def model(*args):
        pytest.fail("Unsupported JSON data must not be sent")

    result = review_delegation(
        ReviewRequest(
            task="Read the checkout.",
            candidate_policy=(
                f"version: 1\nfilesystem_policy:\n  read_only: [/workspace]\nunsupported: {value}\n"
            ),
            execution_context=ExecutionContext(),
        ),
        JevConfig(),
        model,
    )
    assert result["status"] == "invalid_input"
    assert "JSON-compatible" in result["reason"]


def test_native_rule_report_is_compact_and_details_locate_the_rule() -> None:
    review = review_delegation(
        ReviewRequest(
            task="Read issue 42.",
            candidate_policy=(FIXTURES / "candidate-comment.yaml").read_text(),
            execution_context=ExecutionContext(),
        ),
        JevConfig(),
        lambda state, questions, config: _answers(state, questions),
    )
    report = {
        "prover": {"status": "complete", "within_boundary": True},
        "jev": review,
        "combined": True,
    }
    stream = StringIO()
    print_review_report(report, console=Console(file=stream, width=80), scenario="read")
    compact = stream.getvalue()
    assert len(compact.splitlines()) <= 40
    assert "permission group" not in compact.lower()
    assert "POST" in compact
    stream = StringIO()
    print_review_report(
        report, console=Console(file=stream, width=120), scenario="read", details=True
    )
    detailed = stream.getvalue()
    assert f"{ENDPOINT}/rules/2" in detailed
    assert "/network_policies/github/binaries" in detailed


def _answers(state, questions):
    answers = {}
    for identifier, question in questions.items():
        is_post = identifier.startswith(f"{ENDPOINT}/rules/2.")
        if question["type"] == "score":
            probabilities = {"0": 0.0 if is_post else 1.0, "1": 0.0, "2": 1.0 if is_post else 0.0}
            answers[identifier] = {
                "type": "score",
                "value": 2.0 if is_post else 0.0,
                "probabilities": probabilities,
                "confidence": 1.0,
            }
        else:
            if identifier.endswith(".context"):
                value = "none"
            elif identifier.endswith(".write_necessity"):
                value = "required"
            elif identifier.endswith(".justification"):
                value = "unjustified" if is_post else "justified"
            else:
                value = next(iter(question["criteria"]))
            answers[identifier] = {
                "type": "choice",
                "value": value,
                "probabilities": {key: float(key == value) for key in question["criteria"]},
                "confidence": 1.0,
            }
    return answers
