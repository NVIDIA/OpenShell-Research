from __future__ import annotations

from typing import Any

import regex

from slop_cop.rules.api import (
    Evaluator,
    FunctionRule,
    RuleContext,
    RuleEvaluation,
    RuleMetadata,
    RuleSignal,
)


def _pattern_evaluator(
    rule_id: str, pattern: regex.Pattern[str], minimum_matches: int
) -> Evaluator:
    def evaluate(context: RuleContext, runtime: Any) -> RuleEvaluation:
        matches = list(pattern.finditer(context.projected_prose))
        if len(matches) < minimum_matches:
            return RuleEvaluation()
        return RuleEvaluation(
            signals=tuple(
                RuleSignal(start=match.start(), end=match.end(), key=rule_id) for match in matches
            )
        )

    return evaluate


def _duplicate_title(context: RuleContext, runtime: Any) -> RuleEvaluation:
    front_matter = dict(getattr(context.document, "front_matter", ()))
    title = front_matter.get("title", "").strip().casefold()
    if not title:
        return RuleEvaluation()
    signals = tuple(
        RuleSignal(start=match.start(), end=match.end(), key="duplicate-title")
        for match in regex.finditer(r"(?m)^#[ \t]+([^\r\n]+?)[ \t]*$", context.projected_prose)
        if match.group(1).strip().casefold() == title
    )
    return RuleEvaluation(signals=signals)


def _make(
    rule_id: str, title: str, rationale: str, advice: str, pattern: str, minimum: int
) -> FunctionRule:
    metadata = RuleMetadata(
        id=rule_id,
        category="structure",
        title=title,
        rationale=rationale,
        advice=advice,
    )
    compiled = regex.compile(pattern, regex.MULTILINE | regex.VERSION1)
    return FunctionRule(
        metadata,
        _pattern_evaluator(rule_id, compiled, minimum),
    )


RULES = (
    _make(
        "structure.bold-leadins",
        "Repeated bold lead-ins",
        "Several adjacent sections use bold labels as sentence prefixes.",
        "Use headings or ordinary prose when the labels do not aid scanning.",
        r"(?m)^[ \t]*(?:[-*+][ \t]+)?\*\*[^*\n]{2,80}\*\*[:.]?",
        4,
    ),
    _make(
        "structure.triad",
        "Repeated triads",
        "Several sentences enumerate exactly three parallel elements.",
        "Keep triads only when the grouping is meaningful.",
        r"\b[^,\n]{2,40},\s+[^,\n]{2,40},\s+and\s+[^.!?\n]{2,40}",
        3,
    ),
    _make(
        "structure.bullet-run",
        "Long bullet run",
        "A long uninterrupted bullet list can obscure hierarchy.",
        "Group related items under specific subheadings when useful.",
        r"(?m)^(?:[ \t]*[-*+][ \t]+[^\n]+\n){7,}",
        1,
    ),
    _make(
        "structure.horizontal-rules",
        "Frequent horizontal rules",
        "Frequent horizontal rules can fragment the note.",
        "Use headings to express section hierarchy.",
        r"(?m)^[ \t]*(?:---+|___+|\*\*\*+)[ \t]*$",
        4,
    ),
    FunctionRule(
        RuleMetadata(
            id="structure.duplicate-title",
            category="structure",
            title="Duplicate title",
            rationale="The visible heading repeats the front-matter title.",
            advice="Keep a single visible title unless the renderer requires both.",
        ),
        _duplicate_title,
    ),
)
