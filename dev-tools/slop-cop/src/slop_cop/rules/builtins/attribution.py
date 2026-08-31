from __future__ import annotations

import regex

from slop_cop.rules.api import FunctionRule, RuleContext, RuleEvaluation, RuleMetadata, RuleSignal

_CITATION = regex.compile(
    r"(?<!!)\[(?P<label>[^\]\n]+)\]"
    r"(?:\((?P<destination>[^\n)]+)\)|\[(?P<reference>[^\]\n]*)\])"
)
_REFERENCE_DEFINITION = regex.compile(r"^ {0,3}\[(?P<label>[^\]\n]+)\]:\s*\S+", regex.MULTILINE)


def _reference_key(value: str) -> str:
    return " ".join(value.casefold().split())


def _rule(
    rule_id: str,
    title: str,
    rationale: str,
    advice: str,
    pattern: str,
    citation_distance: int,
) -> FunctionRule:
    compiled = regex.compile(pattern, regex.IGNORECASE)

    def evaluate(context: RuleContext, runtime: object) -> RuleEvaluation:
        signals: list[RuleSignal] = []
        references = {
            _reference_key(match.group("label"))
            for match in _REFERENCE_DEFINITION.finditer(context.source)
            if any(
                masked.start <= match.start() < masked.end
                and set(masked.reasons) == {"link-destination"}
                for masked in context.document.masked_ranges
            )
        }
        for match in compiled.finditer(context.projected_prose):
            sentence = next(
                item for item in context.sentences if item.start <= match.start() < item.end
            )
            citation_end = min(sentence.end, match.end() + citation_distance)
            citations = _CITATION.finditer(context.source, match.end(), citation_end)
            if any(
                (
                    citation.group("destination") is not None
                    or _reference_key(citation.group("reference") or citation.group("label"))
                    in references
                )
                and any(
                    masked.start <= citation.start() < masked.end
                    and set(masked.reasons) == {"link-markup"}
                    for masked in context.document.masked_ranges
                )
                for citation in citations
            ):
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
        120,
    ),
    _rule(
        "attribution.citationless-study",
        "Unidentified study",
        "The sentence cites research without identifying a source nearby.",
        "Name and link the study or describe the evidence directly.",
        r"\b(?:a|one|recent|new)\s+(?:study|report|survey|analysis)\s+"
        r"(?:shows?|finds?|found|suggests?|indicates?)\b",
        160,
    ),
)
