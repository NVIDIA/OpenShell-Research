from pathlib import Path

from policy_review_mcp.contracts import ExecutionContext, ReviewRequest, TargetedQuestion
from policy_review_mcp.jev import JevConfig, review_delegation

FIXTURES = Path(__file__).parents[1] / "demo/fixtures"


def _fake_model(state, questions, config):
    assert state["delegated_task"]
    answers = {}
    for identifier, question in questions.items():
        if question["type"] == "score":
            value = 1.8 if identifier.startswith("network.") else 0.1
            answers[identifier] = {
                "type": "score",
                "value": value,
                "probabilities": {"0": 0.1, "1": 0.2, "2": 0.7},
                "confidence": 0.8,
            }
        else:
            if identifier.endswith(".context"):
                value = "none"
            elif identifier.endswith(".write_necessity"):
                value = "required"
            elif identifier.endswith(".justification") and identifier.startswith("network."):
                value = "unjustified"
            elif identifier.startswith("custom."):
                value = next(iter(question["criteria"]))
            else:
                value = "justified"
            option_count = len(question["criteria"])
            remainder = 0.1 / (option_count - 1)
            answers[identifier] = {
                "type": "choice",
                "value": value,
                "probabilities": {
                    option: 0.9 if option == value else remainder for option in question["criteria"]
                },
                "confidence": 0.8,
            }
    return answers


def test_core_and_custom_questions_are_batched_once() -> None:
    calls = 0

    def model(state, questions, config):
        nonlocal calls
        calls += 1
        return _fake_model(state, questions, config)

    candidate = (FIXTURES / "candidate-broad.yaml").read_text()
    request = ReviewRequest(
        task="Summarize issue acme/widget#42 and its discussion. Return it; do not publish it.",
        candidate_policy=candidate,
        execution_context=ExecutionContext(intended_tools=["gh"], scratch_locations=["/tmp"]),
        questions=[
            TargetedQuestion(
                id="posting",
                pointers=["/network_policies/github/endpoints/0"],
                instructions="Should the child publish a comment?",
                criteria={"do_not_post": "Return the summary only", "post": "Publish it"},
            )
        ],
    )
    report = review_delegation(request, JevConfig(), model)
    assert calls == 1
    assert report["model_request_attempted"] is True
    assert report["custom_answers"][0]["id"] == "posting"
    reasons = {item["reason"] for item in report["findings"]}
    assert {"unneeded_action", "resource_scope_too_broad"}.issubset(reasons)
    assert report["candidate_sha256"]
    assert report["review_input_sha256"]


def test_wholly_unassessable_policy_skips_model() -> None:
    called = False

    def model(state, questions, config):
        nonlocal called
        called = True
        return {}

    request = ReviewRequest(
        task="Run a local process.",
        candidate_policy="version: 1\nprocess: {run_as_user: sandbox}\n",
        execution_context=ExecutionContext(),
    )
    report = review_delegation(request, JevConfig(), model)
    assert report["status"] == "incomplete"
    assert report["model_request_attempted"] is False
    assert called is False


def test_context_gap_suppresses_actionable_guidance() -> None:
    def model(state, questions, config):
        answers = _fake_model(state, questions, config)
        for identifier, answer in answers.items():
            if identifier.endswith(".context"):
                answer["value"] = "unknown_dependencies"
                answer["probabilities"] = {
                    option: 0.8 if option == "unknown_dependencies" else 0.04
                    for option in questions[identifier]["criteria"]
                }
        return answers

    request = ReviewRequest(
        task="Summarize issue acme/widget#42.",
        candidate_policy=(FIXTURES / "candidate-read.yaml").read_text(),
        execution_context=ExecutionContext(),
    )
    report = review_delegation(request, JevConfig(), model)
    assert report["status"] == "incomplete"
    assert report["findings"]
    assert all(item["actionable_guidance"] is False for item in report["findings"])


def test_review_fingerprint_changes_with_context() -> None:
    candidate = (FIXTURES / "candidate-read.yaml").read_text()
    common = {
        "task": "Summarize issue acme/widget#42.",
        "candidate_policy": candidate,
    }
    first = review_delegation(
        ReviewRequest(**common, execution_context=ExecutionContext(intended_tools=["gh"])),
        JevConfig(),
        _fake_model,
    )
    second = review_delegation(
        ReviewRequest(
            **common,
            execution_context=ExecutionContext(intended_tools=["gh", "curl"]),
        ),
        JevConfig(),
        _fake_model,
    )
    assert first["candidate_sha256"] == second["candidate_sha256"]
    assert first["review_input_sha256"] != second["review_input_sha256"]


def test_custom_questions_include_resolved_policy_values() -> None:
    captured_states = []
    captured_questions = []

    def model(state, questions, config):
        captured_states.append(state)
        captured_questions.append(questions)
        return _fake_model(state, questions, config)

    def request(run_as_user: str) -> ReviewRequest:
        return ReviewRequest(
            task="Summarize issue acme/widget#42.",
            candidate_policy=(
                "version: 1\n"
                "filesystem_policy:\n  read_only: [/workspace]\n"
                f"process:\n  run_as_user: {run_as_user}\n"
            ),
            execution_context=ExecutionContext(),
            questions=[
                TargetedQuestion(
                    id="identity",
                    pointers=["/process/run_as_user"],
                    instructions="Is the configured identity acceptable?",
                    criteria={"acceptable": "The identity is acceptable."},
                )
            ],
        )

    review_delegation(request("sandbox"), JevConfig(), model)
    review_delegation(request("root"), JevConfig(), model)

    first_reference = captured_states[0]["custom_question_context"][0]["references"][0]
    second_reference = captured_states[1]["custom_question_context"][0]["references"][0]
    assert first_reference["value"] == "sandbox"
    assert second_reference["value"] == "root"
    assert (
        captured_questions[0]["custom.identity"]["instructions"]
        != captured_questions[1]["custom.identity"]["instructions"]
    )


def test_broad_write_scope_does_not_imply_write_is_unnecessary() -> None:
    def model(state, questions, config):
        answers = _fake_model(state, questions, config)
        group = "filesystem.read_write.0"
        answers[f"{group}.justification"]["value"] = "unjustified"
        answers[f"{group}.justification"]["probabilities"] = {
            "justified": 0.02,
            "unjustified": 0.96,
            "insufficient_context": 0.02,
        }
        answers[f"{group}.excess"]["value"] = 1.8
        answers[f"{group}.write_necessity"]["value"] = "required"
        answers[f"{group}.write_necessity"]["probabilities"] = {
            "required": 0.96,
            "not_required": 0.02,
            "insufficient_context": 0.02,
        }
        return answers

    request = ReviewRequest(
        task="Edit /workspace/src/app.py.",
        candidate_policy=("version: 1\nfilesystem_policy:\n  read_write: [/workspace]\n"),
        execution_context=ExecutionContext(output_locations=["/workspace/src/app.py"]),
    )
    report = review_delegation(request, JevConfig(), model)
    reasons = {finding["reason"] for finding in report["findings"]}
    assert "resource_scope_too_broad" in reasons
    assert "write_not_required" not in reasons


def test_low_confidence_choice_is_non_actionable_and_incomplete() -> None:
    def model(state, questions, config):
        answers = _fake_model(state, questions, config)
        group = "filesystem.read_only.0"
        answers[f"{group}.justification"] = {
            "type": "choice",
            "value": "unjustified",
            "probabilities": {
                "unjustified": 0.34,
                "justified": 0.33,
                "insufficient_context": 0.33,
            },
            "confidence": 0.34,
        }
        return answers

    request = ReviewRequest(
        task="Summarize issue acme/widget#42.",
        candidate_policy="version: 1\nfilesystem_policy:\n  read_only: [/workspace]\n",
        execution_context=ExecutionContext(),
    )
    report = review_delegation(request, JevConfig(), model)
    assert report["status"] == "incomplete"
    assert report["findings"]
    assert all(finding["actionable_guidance"] is False for finding in report["findings"])


def test_starting_policy_and_complete_payload_byte_limits() -> None:
    candidate = "version: 1\nfilesystem_policy:\n  read_only: [/workspace]\n"
    oversized_starting = "version: 1\n# " + ("😀" * 270_000)
    starting_report = review_delegation(
        ReviewRequest(
            task="Summarize issue acme/widget#42.",
            candidate_policy=candidate,
            starting_policy=oversized_starting,
            execution_context=ExecutionContext(),
        ),
        JevConfig(),
        _fake_model,
    )
    assert starting_report["status"] == "invalid_input"
    assert "starting policy" in starting_report["reason"]

    payload_report = review_delegation(
        ReviewRequest(
            task="Summarize issue acme/widget#42.",
            candidate_policy=candidate,
            execution_context=ExecutionContext(),
        ),
        JevConfig(max_review_bytes=100),
        _fake_model,
    )
    assert payload_report["status"] == "invalid_input"
    assert "complete review input" in payload_report["reason"]
