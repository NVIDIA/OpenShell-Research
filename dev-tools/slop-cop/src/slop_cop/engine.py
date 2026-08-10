from __future__ import annotations

import asyncio
from collections.abc import Mapping
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any

from slop_cop.config import ErrorPolicy, Severity, SlopCopConfig
from slop_cop.document import Document, Span
from slop_cop.findings import (
    AnalysisState,
    AppliedSuppression,
    Decision,
    FileResult,
    Finding,
    RuleExecutionError,
)
from slop_cop.rules.api import RuleContext, RuleEvaluation, validate_evaluation
from slop_cop.rules.registry import ConfiguredRule, RuleRegistry
from slop_cop.runtime import RuntimeManager
from slop_cop.scoring import score_findings


@dataclass(frozen=True, slots=True)
class EngineOutput:
    file_result: FileResult
    external_audits: tuple[Mapping[str, Any], ...] = ()


def _excerpt(source: str, start: int, end: int, limit: int = 240) -> str:
    left = max(0, start - 80)
    right = min(len(source), max(end + 80, left + limit))
    value = " ".join(source[left:right].split())
    return value[:limit]


def _finding(
    document: Document,
    configured: ConfiguredRule,
    signal: Any,
) -> Finding:
    metadata = configured.metadata
    policy = configured.policy
    span = None
    line = column = None
    excerpt = signal.detail or ""
    if signal.scope == "span":
        span = Span(start=signal.start, end=signal.end)
        line, column = document.line_column(signal.start)
        excerpt = _excerpt(document.source, signal.start, signal.end)
    advisory = policy.severity is Severity.INFO or policy.cap == 0
    return Finding(
        rule_id=metadata.id,
        category=metadata.category,
        severity=policy.severity,
        source_path=document.path,
        span=span,
        line=line,
        column=column,
        excerpt=excerpt[:1000],
        normalized_key=signal.key,
        score_group=metadata.score_group or metadata.id,
        explanation=metadata.rationale,
        advice=metadata.advice,
        units=signal.units,
        advisory=advisory,
        chargeable=not advisory,
        blocking=policy.blocking,
    )


def _apply_suppressions(
    document: Document,
    findings: tuple[Finding, ...],
    registry: RuleRegistry,
) -> tuple[tuple[Finding, ...], tuple[RuleExecutionError, ...]]:
    values = list(findings)
    errors: list[RuleExecutionError] = []
    known_ids = {configured.metadata.id for configured in registry}
    for directive in document.suppressions:
        for rule_id in directive.rule_ids:
            if rule_id not in known_ids:
                errors.append(
                    RuleExecutionError(
                        rule_id=None,
                        source_path=document.path,
                        error_code="unknown_suppression_rule",
                        message=f"suppression references unknown rule {rule_id!r}",
                        fatal=True,
                    )
                )
                continue
            selected = next(
                (
                    index
                    for index, finding in enumerate(values)
                    if finding.rule_id == rule_id
                    and not finding.suppressed
                    and finding.span is not None
                    and directive.target_span.start
                    <= finding.span.start
                    < directive.target_span.end
                ),
                None,
            )
            if selected is None:
                errors.append(
                    RuleExecutionError(
                        rule_id=rule_id,
                        source_path=document.path,
                        error_code="unused_suppression",
                        message=f"suppression for {rule_id!r} did not match the next prose block",
                        fatal=True,
                    )
                )
                continue
            finding = values[selected]
            values[selected] = finding.model_copy(
                update={
                    "suppressed": True,
                    "suppression_reason": directive.reason,
                    "chargeable": False,
                    "advisory": True,
                    "blocking": False,
                }
            )
    return tuple(values), tuple(errors)


def _materially_overlaps(left: Finding, right: Finding) -> bool:
    if left.span is None or right.span is None:
        return False
    overlap = min(left.span.end, right.span.end) - max(left.span.start, right.span.start)
    if overlap <= 0:
        return False
    shorter = min(left.span.end - left.span.start, right.span.end - right.span.start)
    return overlap == shorter or (left.score_group == right.score_group and overlap * 2 >= shorter)


def deduplicate_findings(
    findings: tuple[Finding, ...], registry: RuleRegistry
) -> tuple[Finding, ...]:
    order = {configured.metadata.id: configured.order for configured in registry}
    policies = {configured.metadata.id: configured.policy for configured in registry}
    values = list(findings)
    parent = list(range(len(values)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    mapped = [index for index, finding in enumerate(values) if finding.span is not None]
    mapped.sort(key=lambda index: (values[index].span.start, values[index].span.end))  # type: ignore[union-attr]
    for position, left_index in enumerate(mapped):
        left = values[left_index]
        assert left.span is not None
        for right_index in mapped[position + 1 :]:
            right = values[right_index]
            assert right.span is not None
            if right.span.start >= left.span.end:
                break
            if _materially_overlaps(left, right):
                union(left_index, right_index)

    clusters: dict[int, list[int]] = {}
    for index in range(len(values)):
        clusters.setdefault(find(index), []).append(index)
    severity_rank = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}

    def span_length(finding: Finding) -> int:
        if finding.span is None:
            return 1_000_000
        return finding.span.end - finding.span.start

    for indexes in clusters.values():
        if len(indexes) < 2:
            continue
        primary = min(
            indexes,
            key=lambda index: (
                values[index].suppressed,
                not values[index].blocking,
                severity_rank[values[index].severity],
                policies[values[index].rule_id].fixed_allowance,
                -policies[values[index].rule_id].first_cost,
                order.get(values[index].rule_id, 1_000_000),
                span_length(values[index]),
            ),
        )
        related = tuple(sorted({values[index].rule_id for index in indexes if index != primary}))
        values[primary] = values[primary].model_copy(update={"related_rule_ids": related})
        for index in indexes:
            if index == primary or values[index].suppressed:
                continue
            values[index] = values[index].model_copy(
                update={"chargeable": False, "advisory": True, "blocking": False}
            )
    return tuple(
        sorted(
            values,
            key=lambda finding: (
                finding.span.start if finding.span is not None else len(values) + 1,
                order.get(finding.rule_id, 1_000_000),
                finding.normalized_key,
            ),
        )
    )


async def _evaluate_rule(
    configured: ConfiguredRule,
    context: RuleContext,
    manager: RuntimeManager,
    timeout: float,
) -> tuple[ConfiguredRule, RuleEvaluation | None, Exception | None]:
    try:
        async with asyncio.timeout(timeout):
            evaluation = await configured.rule.evaluate(
                context, manager.for_rule(configured.metadata, configured.policy)
            )
        evaluation = validate_evaluation(
            configured.metadata,
            evaluation,
            document_length=len(context.source),
            max_signal_units=configured.policy.max_signal_units,
        )
        return configured, evaluation, None
    except Exception as error:
        return configured, None, error


async def analyze_document(
    document: Document,
    registry: RuleRegistry,
    config: SlopCopConfig,
    *,
    runtime_manager: RuntimeManager | None = None,
) -> EngineOutput:
    if document.metrics.analyzable_words == 0:
        initial_error = RuleExecutionError(
            source_path=document.path,
            error_code="no_analyzable_prose",
            message="the document contains no analyzable prose",
            fatal=True,
        )
        result = FileResult(
            path=document.path,
            analysis_state=AnalysisState.ERROR,
            decision=Decision.FAIL,
            score=None,
            threshold=config.threshold,
            hard_fail=False,
            metrics=document.metrics,
            errors=(initial_error,),
        )
        return EngineOutput(result)

    context = RuleContext(
        document=document,
        repository_terms=frozenset(term.casefold() for term in config.vocabulary.allowed_terms),
    )

    manager_scope = nullcontext(runtime_manager) if runtime_manager else RuntimeManager(config)
    async with manager_scope as manager:
        async with asyncio.TaskGroup() as group:
            tasks = [
                group.create_task(
                    _evaluate_rule(
                        configured,
                        context,
                        manager,
                        config.external_file_timeout_seconds,
                    )
                )
                for configured in registry.enabled()
            ]
        evaluated = [task.result() for task in tasks]

    evaluated.sort(key=lambda row: row[0].order)
    findings: list[Finding] = []
    errors: list[RuleExecutionError] = []
    audits: list[Mapping[str, Any]] = []
    for configured, evaluation, execution_error in evaluated:
        if execution_error is not None:
            fatal = configured.policy.on_error is ErrorPolicy.FAIL
            errors.append(
                RuleExecutionError(
                    rule_id=configured.metadata.id,
                    source_path=document.path,
                    error_code="rule_execution_failed",
                    message=f"rule {configured.metadata.id!r} could not complete",
                    fatal=fatal,
                )
            )
            continue
        assert evaluation is not None
        findings.extend(_finding(document, configured, signal) for signal in evaluation.signals)
        if evaluation.audit:
            audits.append(
                {
                    "rule_id": configured.metadata.id,
                    "rule_version": configured.metadata.version,
                    **evaluation.audit,
                }
            )

    suppressed, suppression_errors = _apply_suppressions(document, tuple(findings), registry)
    errors.extend(suppression_errors)
    deduplicated = deduplicate_findings(suppressed, registry)
    scored = score_findings(document, deduplicated, registry, config)
    fatal = any(error.fatal for error in errors)
    incomplete = any(not error.fatal for error in errors)
    decision = (
        Decision.PASS
        if scored.score >= config.threshold and not scored.hard_fail and not fatal
        else Decision.FAIL
    )
    state = (
        AnalysisState.ERROR
        if fatal
        else AnalysisState.INCOMPLETE
        if incomplete
        else AnalysisState.COMPLETE
    )
    suppression_records = tuple(
        AppliedSuppression(
            rule_ids=directive.rule_ids,
            reason=directive.reason,
            directive_span=directive.directive_span,
            target_span=directive.target_span,
            suppressed_finding_ids=tuple(
                f"{finding.rule_id}:{finding.line}:{finding.column}"
                for finding in deduplicated
                if finding.suppressed
                and finding.suppression_reason == directive.reason
                and finding.rule_id in directive.rule_ids
            ),
        )
        for directive in document.suppressions
    )
    return EngineOutput(
        FileResult(
            path=document.path,
            analysis_state=state,
            decision=decision,
            score=scored.score,
            threshold=config.threshold,
            hard_fail=scored.hard_fail,
            metrics=document.metrics,
            findings=deduplicated,
            suppressions=suppression_records,
            rule_costs=scored.rule_costs,
            category_costs=scored.category_costs,
            errors=tuple(errors),
        ),
        tuple(audits),
    )
