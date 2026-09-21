"""Task-fit assessment orchestration and TypeSafe JEV integration."""

import hashlib
import json
import os
import time
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from policy_review_mcp.contracts import ReviewRequest
from policy_review_mcp.policy import (
    PolicyInputError,
    parse_policy,
    resolve_pointer,
    validate_annotations,
)

JEV_REPORT_SCHEMA_VERSION = 1
RUBRIC_VERSION = "delegation-rubric-v1"
CATALOG_VERSION = "openshell-github-rest-v1"
OPERATION_CATALOG = {
    "issue": "GET /repos/{owner}/{repo}/issues/{number} reads one issue.",
    "issue_comments": (
        "GET /repos/{owner}/{repo}/issues/{number}/comments reads that issue's comments."
    ),
    "create_issue_comment": (
        "POST /repos/{owner}/{repo}/issues/{number}/comments publishes a comment."
    ),
}


@dataclass(frozen=True)
class JevConfig:
    model: str = "jev-1.13.0"
    timeout_seconds: float = 15.0
    max_policy_bytes: int = 1024 * 1024
    max_task_bytes: int = 32768
    max_questions: int = 32
    max_options_per_question: int = 16
    max_review_bytes: int = 2 * 1024 * 1024
    max_custom_context_bytes: int = 64 * 1024
    min_actionable_confidence: float = 0.6
    min_winner_probability: float = 0.6
    min_choice_margin: float = 0.15
    excess_score_threshold: float = 1.35
    min_excess_probability: float = 0.6

    @classmethod
    def load(cls, path: Path) -> "JevConfig":
        with path.open("rb") as stream:
            values = tomllib.load(stream)
        return cls(**values)


QuestionBatch = dict[str, dict[str, Any]]
ModelCallable = Callable[[dict[str, Any], QuestionBatch, JevConfig], dict[str, Any]]


def invalid_request_report(
    *,
    task: Any,
    candidate_policy: Any,
    execution_context: Any,
    annotations: Any,
    starting_policy: Any,
    questions: Any,
    config: JevConfig,
    reason: str,
) -> dict[str, Any]:
    """Return the ordinary invalid-input envelope when outer validation fails."""

    started = time.perf_counter()
    candidate_text = candidate_policy if isinstance(candidate_policy, str) else ""
    candidate_sha256 = hashlib.sha256(candidate_text.encode("utf-8")).hexdigest()
    raw = {
        "task": task,
        "candidate_policy": candidate_policy,
        "execution_context": execution_context,
        "annotations": annotations,
        "starting_policy": starting_policy,
        "questions": questions,
    }
    canonical = json.dumps(
        raw,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return _invalid(
        {
            "schema_version": JEV_REPORT_SCHEMA_VERSION,
            "candidate_sha256": candidate_sha256,
            "review_input_sha256": hashlib.sha256(canonical).hexdigest(),
            "model_request_attempted": False,
            "model": config.model,
            "rubric_version": RUBRIC_VERSION,
            "catalog_version": CATALOG_VERSION,
        },
        reason,
        started,
    )


def review_delegation(
    request: ReviewRequest,
    config: JevConfig,
    model_call: ModelCallable | None = None,
) -> dict[str, Any]:
    """Validate, batch all questions once, and render deterministic findings."""

    started = time.perf_counter()
    candidate_bytes = request.candidate_policy.encode("utf-8")
    candidate_sha256 = hashlib.sha256(candidate_bytes).hexdigest()
    review_input_bytes = _review_input_bytes(request)
    review_sha256 = hashlib.sha256(review_input_bytes).hexdigest()
    base = {
        "schema_version": JEV_REPORT_SCHEMA_VERSION,
        "candidate_sha256": candidate_sha256,
        "review_input_sha256": review_sha256,
        "model_request_attempted": False,
        "model": config.model,
        "rubric_version": RUBRIC_VERSION,
        "catalog_version": CATALOG_VERSION,
    }
    if len(candidate_bytes) > config.max_policy_bytes:
        return _invalid(base, "candidate policy exceeds configured byte limit", started)
    if (
        request.starting_policy is not None
        and len(request.starting_policy.encode("utf-8")) > config.max_policy_bytes
    ):
        return _invalid(base, "starting policy exceeds configured byte limit", started)
    if len(request.task.encode("utf-8")) > config.max_task_bytes:
        return _invalid(base, "task exceeds configured byte limit", started)
    if len(review_input_bytes) > config.max_review_bytes:
        return _invalid(base, "complete review input exceeds configured byte limit", started)
    if len(request.questions) > config.max_questions:
        return _invalid(base, "too many custom questions", started)
    try:
        candidate = parse_policy(request.candidate_policy)
        starting = (
            parse_policy(request.starting_policy, source_name="starting")
            if request.starting_policy is not None
            else None
        )
        annotations = validate_annotations(request.annotations, candidate, starting)
        custom_context = _build_custom_question_context(request, candidate, config)
    except PolicyInputError as error:
        return _invalid(base, str(error), started)

    coverage = {
        "supported_groups": [group.id for group in candidate.groups],
        "unassessed": list(candidate.unassessed),
        "inventory": list(candidate.inventory),
    }
    if not candidate.groups:
        return {
            **base,
            "status": "incomplete",
            "coverage": coverage,
            "assessments": [],
            "findings": [],
            "custom_answers": [],
            "reason": "policy has no independently assessable permission groups",
            "timings_ms": {"total": round((time.perf_counter() - started) * 1000, 3)},
            "summary": "No supported permission groups were available for JEV assessment.",
        }

    total_question_count = sum(
        4 if group.kind == "filesystem" and group.state["mode"] == "read_write" else 3
        for group in candidate.groups
    ) + len(request.questions)
    if total_question_count > config.max_questions:
        return _invalid(
            base,
            f"review requires {total_question_count} questions; configured limit is "
            f"{config.max_questions}",
            started,
        )

    state = {
        "delegated_task": request.task,
        "execution_context": request.execution_context.model_dump(),
        "permission_groups": [
            {"id": group.id, "kind": group.kind, "summary": group.summary, "state": group.state}
            for group in candidate.groups
        ],
        "field_annotations": annotations,
        "custom_question_context": custom_context,
        "trusted_operation_catalog": OPERATION_CATALOG,
        "review_rules": [
            "Assess the exact delegated task, not a broader parent objective.",
            "Runtime presence does not by itself justify task access.",
            "Treat broad selectors as broad authority, not only as operation examples.",
        ],
    }
    try:
        questions = _build_questions(request, candidate.groups, custom_context, config)
    except PolicyInputError as error:
        return _invalid(base, str(error), started)
    caller = model_call or call_typesafe
    model_started = time.perf_counter()
    try:
        answers = caller(state, questions, config)
        _validate_answers(answers, questions)
    except Exception as error:
        return {
            **base,
            "status": "unavailable",
            "coverage": coverage,
            "assessments": [],
            "findings": [],
            "custom_answers": [],
            "model_request_attempted": True,
            "reason": f"JEV request failed: {error}",
            "timings_ms": {
                "model": round((time.perf_counter() - model_started) * 1000, 3),
                "total": round((time.perf_counter() - started) * 1000, 3),
            },
            "summary": "Task-fit assessment is unavailable; the boundary result remains separate.",
        }

    assessments, findings = _render_core(candidate.groups, answers, config)
    custom_answers = [
        {"id": question.id, **answers[f"custom.{question.id}"]} for question in request.questions
    ]
    incomplete = any(
        any(item["uncertainty"].values())
        or item["context_gap"]["value"] != "none"
        or item["task_justification"]["value"] == "insufficient_context"
        or (
            item["write_necessity"] is not None
            and item["write_necessity"]["value"] == "insufficient_context"
        )
        for item in assessments
    )
    return {
        **base,
        "status": "incomplete" if incomplete else "complete",
        "coverage": coverage,
        "assessments": assessments,
        "findings": findings,
        "custom_answers": custom_answers,
        "model_request_attempted": True,
        "timings_ms": {
            "model": round((time.perf_counter() - model_started) * 1000, 3),
            "total": round((time.perf_counter() - started) * 1000, 3),
        },
        "summary": (
            f"JEV reviewed {len(assessments)} groups and highlighted {len(findings)} findings."
        ),
    }


def call_typesafe(
    state: dict[str, Any], questions: QuestionBatch, config: JevConfig
) -> dict[str, Any]:
    """Translate neutral question specs to the optional TypeSafe SDK."""

    try:
        from typesafe_sdk import Choice, Score, TypeSafeClient
    except ImportError as error:
        raise RuntimeError("install the 'jev' extra to run the JEV service") from error
    sdk_questions: dict[str, Any] = {}
    for identifier, specification in questions.items():
        if specification["type"] == "choice":
            sdk_questions[identifier] = Choice(
                instructions=specification["instructions"], criteria=specification["criteria"]
            )
        else:
            sdk_questions[identifier] = Score(
                instructions=specification["instructions"], criteria=specification["criteria"]
            )
    api_key = os.environ.get("TYPESAFE_API_KEY") or os.environ.get("TYPESAFEAI_API_KEY")
    with TypeSafeClient(
        api_key=api_key, model=config.model, timeout=config.timeout_seconds
    ) as client:
        response = client.system_one(state=state, questions=sdk_questions)
    output: dict[str, Any] = {}
    for identifier, answer in response.answers.items():
        if answer.type == "choice":
            output[identifier] = {
                "type": "choice",
                "value": answer.choice,
                "probabilities": dict(answer.probabilities),
                "confidence": answer.confidence,
            }
        elif answer.type == "score":
            output[identifier] = {
                "type": "score",
                "value": answer.score,
                "probabilities": {str(key): value for key, value in answer.probabilities.items()},
                "confidence": answer.confidence,
            }
    return output


def _build_questions(
    request: ReviewRequest,
    groups: tuple[Any, ...],
    custom_context: list[dict[str, Any]],
    config: JevConfig,
) -> QuestionBatch:
    questions: QuestionBatch = {}
    for group in groups:
        prefix = group.id
        reference = (
            f"permission group '{group.summary}' with state "
            f"{json.dumps(group.state, sort_keys=True)}"
        )
        questions[f"{prefix}.justification"] = {
            "type": "choice",
            "instructions": (
                f"Is every capability in {reference} justified by the exact delegated task "
                "and documented runtime needs?"
            ),
            "criteria": {
                "justified": "Every represented capability is needed.",
                "unjustified": "At least one represented capability is not needed.",
                "insufficient_context": "The supplied state is not enough to decide.",
            },
        }
        questions[f"{prefix}.excess"] = {
            "type": "score",
            "instructions": f"How much authority in {reference} extends beyond the stated task?",
            "criteria": [
                "Fits the stated needs.",
                "Includes identifiable unnecessary access.",
                "Grants substantial unrelated access.",
            ],
        }
        questions[f"{prefix}.context"] = {
            "type": "choice",
            "instructions": f"What is the most important context gap when assessing {reference}?",
            "criteria": {
                "none": "No material context gap.",
                "unclear_assignment": "The delegated assignment is unclear.",
                "unknown_dependencies": "Required dependencies or prepared inputs are unknown.",
                "unknown_executable_needs": "Executable or tool needs are unknown.",
                "unknown_output_runtime_needs": "Output, scratch, or runtime needs are unknown.",
                "other": "A different material context gap exists.",
            },
        }
        if group.kind == "filesystem" and group.state["mode"] == "read_write":
            questions[f"{prefix}.write_necessity"] = {
                "type": "choice",
                "instructions": (
                    f"Does the exact delegated task require writing within {reference}, "
                    "independent of whether the resource path is broader than necessary?"
                ),
                "criteria": {
                    "required": "The task requires some write access within this path.",
                    "not_required": "The task requires no write access within this path.",
                    "insufficient_context": "The supplied state is not enough to decide.",
                },
            }
    for index, question in enumerate(request.questions):
        criteria = dict(question.criteria)
        criteria["none_fit"] = "None of the named alternatives fit."
        criteria["insufficient_context"] = "The supplied state is insufficient to choose."
        if len(criteria) > config.max_options_per_question:
            raise PolicyInputError(f"custom question {question.id} has too many options")
        questions[f"custom.{question.id}"] = {
            "type": "choice",
            "instructions": (
                f"{question.instructions} Referenced policy values and coverage: "
                f"{json.dumps(custom_context[index]['references'], sort_keys=True)}"
            ),
            "criteria": criteria,
        }
    return questions


def _render_core(
    groups: tuple[Any, ...], answers: dict[str, Any], config: JevConfig
) -> tuple[list[Any], list[Any]]:
    assessments: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    for group in groups:
        justification = answers[f"{group.id}.justification"]
        excess = answers[f"{group.id}.excess"]
        context_gap = answers[f"{group.id}.context"]
        write_necessity = answers.get(f"{group.id}.write_necessity")
        uncertainty = {
            "task_justification": _choice_is_uncertain(justification, config),
            "excess_scope": _score_is_uncertain(excess, config),
            "context_gap": _choice_is_uncertain(context_gap, config),
            "write_necessity": (
                _choice_is_uncertain(write_necessity, config)
                if write_necessity is not None
                else False
            ),
        }
        assessment = {
            "group_id": group.id,
            "kind": group.kind,
            "summary": group.summary,
            "locations": [location.as_dict() for location in group.locations],
            "task_justification": justification,
            "excess_scope": excess,
            "context_gap": context_gap,
            "write_necessity": write_necessity,
            "uncertainty": uncertainty,
        }
        assessments.append(assessment)
        missing_context = (
            context_gap["value"] != "none"
            or justification["value"] == "insufficient_context"
            or (write_necessity is not None and write_necessity["value"] == "insufficient_context")
        )
        base_actionable = not missing_context and not any(uncertainty.values())
        if missing_context:
            findings.append(
                _finding(
                    group,
                    "missing_runtime_context",
                    context_gap if context_gap["value"] != "none" else justification,
                    actionable=False,
                )
            )
            if justification["value"] == "unjustified":
                findings.append(
                    _finding(
                        group,
                        _reason_for_group(group, excess),
                        justification,
                        actionable=False,
                    )
                )
        justification_reason = None
        if justification["value"] == "unjustified":
            justification_reason = _reason_for_group(group, excess)
            if not missing_context:
                findings.append(
                    _finding(
                        group,
                        justification_reason,
                        justification,
                        actionable=base_actionable,
                    )
                )
        if float(excess["value"]) >= config.excess_score_threshold:
            excess_reason = "resource_scope_too_broad"
            if justification_reason != excess_reason:
                findings.append(
                    _finding(
                        group,
                        excess_reason,
                        excess,
                        actionable=base_actionable,
                    )
                )
        if write_necessity is not None and write_necessity["value"] == "not_required":
            findings.append(
                _finding(
                    group,
                    "write_not_required",
                    write_necessity,
                    actionable=base_actionable,
                )
            )
    return assessments, findings


def _reason_for_group(group: Any, excess: dict[str, Any]) -> str:
    if group.kind == "github_rest":
        methods = {item["method"] for item in group.state["selectors"]}
        if methods - {"GET", "HEAD", "OPTIONS"}:
            return "unneeded_action"
    return "resource_scope_too_broad" if float(excess["value"]) >= 1.0 else "unneeded_action"


def _choice_is_uncertain(answer: dict[str, Any], config: JevConfig) -> bool:
    probabilities = sorted(answer["probabilities"].values(), reverse=True)
    winner = answer["probabilities"][answer["value"]]
    runner_up = probabilities[1] if len(probabilities) > 1 else 0.0
    return (
        answer["confidence"] < config.min_actionable_confidence
        or winner < config.min_winner_probability
        or winner - runner_up < config.min_choice_margin
    )


def _score_is_uncertain(answer: dict[str, Any], config: JevConfig) -> bool:
    excess_probability = sum(
        probability for level, probability in answer["probabilities"].items() if int(level) >= 1
    )
    return answer["confidence"] < config.min_actionable_confidence or (
        float(answer["value"]) >= config.excess_score_threshold
        and excess_probability < config.min_excess_probability
    )


def _finding(group: Any, reason: str, answer: dict[str, Any], actionable: bool) -> dict[str, Any]:
    messages = {
        "unneeded_action": "The permission includes an action not required by the assignment.",
        "resource_scope_too_broad": "The permission covers resources beyond the stated need.",
        "write_not_required": "The assignment does not establish a need for write access.",
        "missing_runtime_context": (
            "More execution context is needed before suggesting a scope change."
        ),
    }
    return {
        "group_id": group.id,
        "reason": reason,
        "message": messages[reason],
        "locations": [location.as_dict() for location in group.locations],
        "probabilities": answer["probabilities"],
        "confidence": answer["confidence"],
        "actionable_guidance": actionable,
    }


def _validate_answers(answers: Any, questions: QuestionBatch) -> None:
    if not isinstance(answers, dict) or set(answers) != set(questions):
        raise ValueError("response answer IDs do not exactly match request")
    for identifier, specification in questions.items():
        answer = answers[identifier]
        if not isinstance(answer, dict) or answer.get("type") != specification["type"]:
            raise ValueError(f"invalid answer type for {identifier}")
        confidence = answer.get("confidence")
        probabilities = answer.get("probabilities")
        if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            raise ValueError(f"invalid confidence for {identifier}")
        if not isinstance(probabilities, dict) or not probabilities:
            raise ValueError(f"missing probabilities for {identifier}")
        if any(
            not isinstance(value, (int, float)) or not 0 <= value <= 1
            for value in probabilities.values()
        ):
            raise ValueError(f"invalid probabilities for {identifier}")
        if abs(sum(probabilities.values()) - 1.0) > 0.02:
            raise ValueError(f"probabilities do not sum to one for {identifier}")
        if (
            specification["type"] == "choice"
            and answer.get("value") not in specification["criteria"]
        ):
            raise ValueError(f"unknown choice for {identifier}")
        if specification["type"] == "choice" and set(probabilities) != set(
            specification["criteria"]
        ):
            raise ValueError(f"choice probabilities do not match criteria for {identifier}")
        if specification["type"] == "score" and not isinstance(answer.get("value"), (int, float)):
            raise ValueError(f"invalid score for {identifier}")
        if specification["type"] == "score" and set(probabilities) != {
            str(index) for index in range(len(specification["criteria"]))
        }:
            raise ValueError(f"score probabilities do not match criteria for {identifier}")


def _build_custom_question_context(
    request: ReviewRequest, candidate: Any, config: JevConfig
) -> list[dict[str, Any]]:
    known = set(candidate.locations)
    identifiers: set[str] = set()
    contexts: list[dict[str, Any]] = []
    total_bytes = 0
    for question in request.questions:
        if question.id in identifiers:
            raise PolicyInputError(f"duplicate custom question ID: {question.id}")
        identifiers.add(question.id)
        missing = [pointer for pointer in question.pointers if pointer not in known]
        if missing:
            raise PolicyInputError(f"custom question {question.id} has unknown pointers: {missing}")
        references = []
        for pointer in question.pointers:
            _, value = resolve_pointer(candidate.data, pointer)
            group_ids = [
                group.id
                for group in candidate.groups
                if any(
                    pointer == group_pointer
                    or pointer.startswith(f"{group_pointer}/")
                    or group_pointer.startswith(f"{pointer}/")
                    for group_pointer in group.pointers
                )
            ]
            references.append(
                {
                    "pointer": pointer,
                    "value": _plain_json_value(value),
                    "location": candidate.locations[pointer].as_dict(),
                    "supported_groups": group_ids,
                    "coverage": "supported" if group_ids else "unassessed",
                }
            )
        context = {"references": references}
        total_bytes += len(
            json.dumps(context, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        if total_bytes > config.max_custom_context_bytes:
            raise PolicyInputError("custom question context exceeds configured byte limit")
        contexts.append(context)
    return contexts


def _plain_json_value(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError) as error:
        raise PolicyInputError(f"custom question value is not JSON-compatible: {error}") from error


def _review_input_bytes(request: ReviewRequest) -> bytes:
    return json.dumps(
        request.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _invalid(base: dict[str, Any], reason: str, started: float) -> dict[str, Any]:
    return {
        **base,
        "status": "invalid_input",
        "coverage": {"supported_groups": [], "unassessed": [], "inventory": []},
        "assessments": [],
        "findings": [],
        "custom_answers": [],
        "reason": reason,
        "timings_ms": {"total": round((time.perf_counter() - started) * 1000, 3)},
        "summary": f"Invalid JEV review input: {reason}",
    }
