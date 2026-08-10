from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from slop_cop.config import DensityUnit, PassageDensityPolicy, SlopCopConfig
from slop_cop.document import Document, ProseSegment, Span
from slop_cop.findings import CategoryCost, DensityMeasurement, Finding, RuleCost
from slop_cop.rules.registry import RuleRegistry


@dataclass(frozen=True, slots=True)
class ScoreResult:
    score: int
    hard_fail: bool
    rule_costs: tuple[RuleCost, ...]
    category_costs: tuple[CategoryCost, ...]


def _opportunities(document: Document, unit: DensityUnit) -> int:
    if unit is DensityUnit.WORD:
        return document.metrics.analyzable_words
    if unit is DensityUnit.SENTENCE:
        return document.metrics.analyzable_sentences
    return document.metrics.analyzable_paragraphs


def _segments(document: Document, unit: DensityUnit) -> tuple[ProseSegment, ...]:
    if unit is DensityUnit.WORD:
        return document.tokens
    if unit is DensityUnit.SENTENCE:
        return document.sentences
    return document.paragraphs


def _density(
    document: Document,
    findings: Iterable[Finding],
    policy: PassageDensityPolicy,
) -> DensityMeasurement:
    segments = _segments(document, policy.unit)
    mapped = tuple(
        finding
        for finding in findings
        if finding.span is not None and finding.chargeable and not finding.suppressed
    )
    peak_units = 0
    peak_span: Span | None = None
    if segments:
        width = min(policy.window, len(segments))
        for index in range(0, len(segments) - width + 1):
            start = segments[index].start
            end = segments[index + width - 1].end
            units = sum(
                finding.units
                for finding in mapped
                if finding.span is not None and start <= finding.span.start < end
            )
            if units > peak_units:
                peak_units = units
                peak_span = Span(start=start, end=end)
    peak_excess = max(0, peak_units - policy.allowed_units)
    cost = 0.0
    if peak_excess:
        cost = min(
            policy.cap,
            policy.first_cost + policy.repeat_cost * (peak_excess - 1),
        )
    return DensityMeasurement(
        unit=policy.unit.value,
        window=policy.window,
        allowed_units=policy.allowed_units,
        peak_units=peak_units,
        peak_excess=peak_excess,
        cost=cost,
        window_span=peak_span,
    )


def score_findings(
    document: Document,
    findings: tuple[Finding, ...],
    registry: RuleRegistry,
    config: SlopCopConfig,
) -> ScoreResult:
    rule_costs: list[RuleCost] = []
    costs_by_category: dict[str, float] = {category: 0.0 for category in config.categories}
    findings_by_category: dict[str, list[Finding]] = {
        category: [] for category in config.categories
    }

    for configured in registry:
        policy = configured.policy
        if not policy.enabled:
            continue
        rule_findings = tuple(
            finding
            for finding in findings
            if finding.rule_id == configured.metadata.id
            and finding.chargeable
            and not finding.suppressed
        )
        units = sum(finding.units for finding in rule_findings)
        allowance = policy.fixed_allowance
        if policy.document_density is not None:
            opportunity_count = _opportunities(document, policy.document_density.unit)
            allowance += (
                opportunity_count // policy.document_density.interval
            ) * policy.document_density.allowed_units
        excess = max(0, units - allowance)
        base_cost = 0.0
        if excess:
            base_cost = policy.first_cost + policy.repeat_cost * (excess - 1)
        density = (
            _density(document, rule_findings, policy.density)
            if policy.density is not None
            else None
        )
        density_cost = density.cost if density is not None else 0.0
        charged = min(policy.cap, base_cost + density_cost)
        rule_costs.append(
            RuleCost(
                rule_id=configured.metadata.id,
                deduplicated_units=units,
                allowance=allowance,
                document_excess=excess,
                base_cost=min(100.0, base_cost),
                density=density,
                cap=policy.cap,
                charged_cost=charged,
            )
        )
        costs_by_category[configured.metadata.category] += charged
        findings_by_category[configured.metadata.category].extend(rule_findings)

    category_costs: list[CategoryCost] = []
    for category, category_policy in config.categories.items():
        base = min(100.0, costs_by_category[category])
        density = (
            _density(document, findings_by_category[category], category_policy.density)
            if category_policy.density is not None
            else None
        )
        density_cost = density.cost if density is not None else 0.0
        charged = min(category_policy.cap, base + density_cost)
        category_costs.append(
            CategoryCost(
                category=category,
                rule_cost=base,
                density=density,
                cap=category_policy.cap,
                charged_cost=charged,
            )
        )

    total = sum(category.charged_cost for category in category_costs)
    score = max(0, round(100 - total))
    hard_fail = any(finding.blocking and not finding.suppressed for finding in findings)
    return ScoreResult(
        score=score,
        hard_fail=hard_fail,
        rule_costs=tuple(rule_costs),
        category_costs=tuple(category_costs),
    )
