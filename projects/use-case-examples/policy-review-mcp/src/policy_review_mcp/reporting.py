# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Human-readable presentation of independent prover and JEV results."""

import json
from typing import Any

from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

ANSWER_LABELS = {
    "justified": "Fits the task",
    "unjustified": "Not justified as scoped",
    "insufficient_context": "Not enough context to decide",
    "none": "No context gap identified",
    "unclear_assignment": "Clarify the assignment",
    "unknown_dependencies": "Explain dependencies / prepared inputs",
    "unknown_executable_needs": "Explain executable / tool needs",
    "unknown_output_runtime_needs": "Explain output / scratch / runtime needs",
    "other": "Other context is missing",
    "required": "Some write access is needed",
    "not_required": "No write access is needed",
}


def print_review_report(
    report: dict[str, Any], *, console: Console, scenario: str, details: bool = False
) -> None:
    """Lead with decisions; reserve the complete evidence view for --details."""
    if report.get("status") == "runner_error":
        console.print(
            Panel(
                Text(report.get("reason", "Unknown error")),
                title="Demo could not finish",
                border_style="red",
            )
        )
        console.print("Check the server configuration; use --verbose for service logs.")
        return
    console.rule(Text(f"Policy review · {scenario}", style="bold cyan"))
    console.print(Text(report.get("task", "")))
    demo = report.get("demo", {})
    if demo.get("lesson"):
        console.print(Text(demo["lesson"], style="dim"))
    proof, review = report.get("prover", {}), report.get("jev", {})
    passed = proof.get("status") == "complete" and proof.get("within_boundary") is True
    matched = passed and report.get("combined") is True
    status = review.get("status", "not_assessed")
    proof_label = (
        "WITHIN BOUNDARY"
        if passed
        else ("EXCEEDS BOUNDARY" if proof.get("result") == "exceeds_boundary" else "NOT VERIFIED")
    )
    console.print(Text(f"Prover: {proof_label}", style="green" if passed else "yellow"))
    labels = {
        "complete": "ASSESSED",
        "incomplete": "PARTIAL / UNCERTAIN",
        "not_assessed": "SKIPPED",
        "unavailable": "UNAVAILABLE",
        "invalid_input": "INVALID INPUT",
    }
    console.print(Text(f"JEV: {labels.get(status, status)}", style="cyan"))
    for name, result in (("Prover", proof), ("JEV", review)):
        if result.get("reason"):
            console.print(Text(f"{name}: {result['reason']}", style="yellow"))
    if not passed:
        console.print("JEV was not called because the prover did not pass.")
        witness = proof.get("counterexample")
        if witness:
            operation = " ".join(
                str(witness[key])
                for key in ("method", "host", "port", "path")
                if witness.get(key) is not None
            )
            console.print(
                Text(f"Boundary counterexample (proof witness): {operation or json.dumps(witness)}")
            )
        console.print(
            "Next: Set executable in prover.toml to a working openshell-prover path."
            if proof.get("reason_code") == "prover_unavailable"
            else "Next: Resolve the boundary failure or prover error, then rerun."
        )
    elif not matched:
        console.print(
            "Reports cannot be combined: Candidate fingerprints do not match. "
            "Rerun both checks; do not act on these findings.",
            style="red",
        )
    if status in {"unavailable", "invalid_input"}:
        console.print("Next: Resolve the JEV error and rerun.")
    findings = review.get("findings", [])
    assessments = review.get("assessments", [])
    custom_answers = review.get("custom_answers", [])
    diagnostics = {
        answer["pointers"][0]: answer
        for answer in custom_answers
        if answer.get("diagnostic_outcomes")
    }
    if assessments:
        table = Table(expand=True, padding=(0, 1), show_lines=True)
        table.add_column("Policy entry", ratio=2)
        table.add_column("Assessment", ratio=1)
        if diagnostics:
            table.add_column("Diagnostic", ratio=2)
        actionable_targets = 0
        for item in assessments:
            related = [f for f in findings if f["target_pointer"] == item["target_pointer"]]
            actionable = [f for f in related if matched and f.get("actionable_guidance")]
            actionable_targets += bool(actionable)
            uncertainty = item.get("uncertainty", {})
            if item.get("contradictions"):
                verdict = "Conflicting answers"
            elif uncertainty.get("task_justification"):
                verdict = "Uncertain task fit"
            else:
                verdict = ANSWER_LABELS.get(item["task_justification"]["value"], "Needs context")
                if any(uncertainty.values()):
                    verdict += " (partial)"
            priority = {
                "write_not_required": 0,
                "resource_scope_too_broad": 1,
                "permission_not_justified": 2,
                "missing_runtime_context": 3,
            }
            ranked = sorted(actionable or related, key=lambda f: priority.get(f.get("reason"), 4))
            selected = ranked[0] if ranked else None
            if selected and actionable:
                specific = {
                    "write_not_required": "Writes not needed",
                    "resource_scope_too_broad": "Excess scope",
                }.get(selected.get("reason"))
                if specific:
                    verdict += "\n" + specific
                verdict += "\nChange supported"
            elif selected or any(uncertainty.values()) or item.get("contradictions"):
                verdict += "\nInvestigate"
            diagnostic = diagnostics.get(item["target_pointer"])
            row = [Text(item["summary"], overflow="fold"), Text(verdict)]
            if diagnostics:
                row.append(Text(_diagnostic_text(diagnostic) if diagnostic else "—"))
            table.add_row(*row)
        console.print(table)
        if diagnostics:
            console.print(
                "Diagnostic: JEV selects among agent-supplied hypotheses, not a causal explanation "
                "of its core assessment.",
                style="dim",
            )
        console.print(
            Text(
                f"{len(assessments)} policy entries · {actionable_targets} with change guidance · "
                + (
                    "unresolved evidence remains"
                    if status == "incomplete"
                    else "supported scope assessed"
                )
            )
        )
        if matched:
            console.print(
                "Next: Review change guidance and unresolved evidence. "
                "Rerun the prover after any policy edit."
                if actionable_targets or status == "incomplete"
                else "Next: No change indicated here; review unassessed fields separately."
            )
    unassessed = (review.get("coverage") or {}).get("unassessed", [])
    if unassessed:
        console.print(
            Text(
                "Not assessed by JEV: "
                + ", ".join(dict.fromkeys(i["pointer"] for i in unassessed)),
                style="yellow",
            )
        )
    _print_custom_answers(
        [answer for answer in custom_answers if details or not answer.get("diagnostic_outcomes")],
        console,
        details=details,
    )
    if demo.get("compare_with"):
        console.print(Text(f"Compare: {demo['compare_with']}", style="dim"))
    console.print(
        "Boundary containment is not task fit. JEV is advisory. This is not approval.", style="dim"
    )
    console.print(
        "Use --details for confidence, blockers, and locations; --json for raw evidence.",
        style="dim",
    )
    if details:
        console.rule("Evidence and diagnostics")
        console.print(
            "Confidence is model certainty, not policy safety. Excess: 0 = fits, "
            "1 = some excess, 2 = substantial excess. UNCERTAIN answers do not meet "
            "the configured thresholds.",
            style="dim",
        )
        for item in assessments:
            console.print(_assessment_panel(item, findings, matched))
        for item in unassessed:
            console.print(Text(f"Not assessed: {item['pointer']} — {item['reason']}"))
        if proof.get("counterexample"):
            console.print(Text(json.dumps(proof["counterexample"], indent=2)))
        console.print(Text(f"Prover coverage: {proof.get('coverage', {})}"))
        console.print(
            Text(
                f"Model: {review.get('model', 'not called')} · "
                f"timings (ms): {report.get('timings_ms', {})}"
            )
        )


def _diagnostic_text(answer: dict[str, Any]) -> str:
    status = answer.get("diagnostic_status", "uncertain")
    description = answer["criteria"][answer["value"]]
    prefixes = {
        "uncertain": "UNCERTAIN preference: ",
        "core_uncertain": "Core assessment unresolved: ",
        "conflict": "CONFLICT with task fit: ",
    }
    if answer.get("uncertain") and status not in {"none_fit", "insufficient_context", "uncertain"}:
        return "UNCERTAIN preference: " + description
    return prefixes.get(status, "") + description


def _print_custom_answers(
    answers: list[dict[str, Any]], console: Console, *, details: bool = False
) -> None:
    for answer in answers:
        label = "UNCERTAIN" if answer.get("uncertain", True) else "MODEL PREFERENCE"
        console.print(
            Text(f"Custom answers — caller interpretation required · {label}", style="yellow")
        )
        console.print(Text(answer.get("instructions", answer["id"])))
        if answer.get("diagnostic_outcomes"):
            console.print(Text("Diagnostic: " + _diagnostic_text(answer)))
        console.print(
            Text(
                f"Confidence {_percent(answer['confidence'])} (model certainty, not safety)",
                style="dim",
            )
        )
        if answer.get("pointers"):
            console.print(Text("References: " + ", ".join(answer["pointers"]), style="dim"))
        ranked = sorted(answer["probabilities"].items(), key=lambda pair: pair[1], reverse=True)
        for key, probability in ranked if details else ranked[:2]:
            description = answer.get("criteria", {}).get(key, key)
            console.print(Text(f"  {description.rstrip('.')}: {_percent(probability)}"))


def _assessment_panel(
    assessment: dict[str, Any], findings: list[dict[str, Any]], matched: bool
) -> Panel:
    table = Table(expand=True, box=None, padding=(0, 1))
    table.add_column("Question", style="bold")
    table.add_column("Model answer", ratio=3)
    uncertainty = assessment.get("uncertainty", {})
    for key, label in (
        ("task_justification", "Task fit"),
        ("excess_scope", "Excess scope"),
        ("context_gap", "Context"),
        ("write_necessity", "Write needed?"),
    ):
        answer = assessment.get(key)
        if answer is None:
            continue
        uncertain = uncertainty.get(key, False)
        if answer["type"] == "score":
            value = f"{answer['value']:.2f} / 2"
        else:
            value = ANSWER_LABELS.get(answer["value"], answer["value"])
        rendered = Text(str(value), style="yellow" if uncertain else "default")
        if uncertain:
            rendered.append(" — UNCERTAIN", style="bold yellow")
        rendered.append(f"\nConfidence {_percent(answer['confidence'])}", style="dim")
        if answer["type"] == "choice":
            rendered.append(
                f" · Selected probability {_percent(answer['probabilities'][answer['value']])}",
                style="dim",
            )
        else:
            rendered.append(f" · {_distribution(answer)}", style="dim")
        table.add_row(label, rendered)

    parts: list[Any] = [Text(assessment["summary"], style="bold"), table]
    for conflict in assessment.get("contradictions", []):
        parts.append(Text(f"Conflicting answers: {conflict}", style="yellow"))
    for finding in findings:
        if finding["target_pointer"] != assessment["target_pointer"]:
            continue
        actionable = finding.get("actionable_guidance", False) and matched
        label = "Consider a change" if actionable else "Needs investigation"
        parts.append(
            Text(f"{label}: {finding['message']}", style="cyan" if actionable else "yellow")
        )
        if finding.get("blocked_by"):
            parts.append(Text("Blocked by: " + ", ".join(finding["blocked_by"]), style="dim"))
    locations = assessment.get("locations", [])
    if assessment.get("context_pointers"):
        parts.append(Text("Context: " + ", ".join(assessment["context_pointers"]), style="dim"))
    for location in locations:
        parts.append(
            Text(
                f"{location.get('source', 'candidate')}:{location['line']}:{location['column']} "
                f"{location['pointer']}",
                style="dim",
            )
        )
    return Panel(Group(*parts), title=Text(assessment["target_pointer"]), border_style="blue")


def _percent(value: float) -> str:
    return f"{value:.0%}"


def _distribution(answer: dict[str, Any]) -> str:
    return " · ".join(f"{key}: {_percent(value)}" for key, value in answer["probabilities"].items())
