from __future__ import annotations

import regex

from slop_cop.rules.api import FunctionRule, RuleContext, RuleEvaluation, RuleMetadata, RuleSignal

_CITATION = regex.compile(r"\[[^\]\n]+\](?:\([^\n)]+\)|\[[^\]\n]*\])")


def _rule(
    rule_id: str,
    title: str,
    rationale: str,
    advice: str,
    pattern: str,
) -> FunctionRule:
    compiled = regex.compile(pattern, regex.IGNORECASE)

    def evaluate(context: RuleContext, runtime: object) -> RuleEvaluation:
        signals: list[RuleSignal] = []
        for match in compiled.finditer(context.projected_prose):
            paragraph = next(
                item for item in context.paragraphs if item.start <= match.start() < item.end
            )
            if _CITATION.search(context.source[paragraph.start : paragraph.end]):
                continue
            signals.append(RuleSignal(start=match.start(), end=match.end(), key=rule_id))
        return RuleEvaluation(signals=tuple(signals))

    return FunctionRule(
        RuleMetadata(
            id=rule_id,
            category="attribution",
            title=title,
            rationale=rationale,
            advice=advice,
        ),
        evaluate,
    )


RULES = (
    _rule(
        "attribution.vague-authority",
        "Vague authority",
        "The claim attributes a view to unnamed authorities.",
        "Name and link the relevant source or make the claim in the author's voice.",
        r"\b(?:experts|researchers|industry leaders|many observers|critics)\s+"
        r"(?:say|believe|agree|argue|suggest|warn)\b",
    ),
    _rule(
        "attribution.citationless-study",
        "Unidentified study",
        "The sentence cites research without identifying a source nearby.",
        "Name and link the study or describe the evidence directly.",
        r"\b(?:a|one|recent|new)\s+(?:study|report|survey|analysis)\s+"
        r"(?:shows?|finds?|found|suggests?|indicates?)\b",
    ),
)
