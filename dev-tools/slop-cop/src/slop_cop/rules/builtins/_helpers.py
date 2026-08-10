from __future__ import annotations

from slop_cop.rules.api import RuleMetadata
from slop_cop.rules.declarative import PhraseRule, RegexRule


def phrase_rule(
    rule_id: str,
    category: str,
    title: str,
    rationale: str,
    advice: str,
    *phrases: str,
    score_group: str | None = None,
) -> PhraseRule:
    return PhraseRule(
        RuleMetadata(
            id=rule_id,
            category=category,
            title=title,
            rationale=rationale,
            advice=advice,
            score_group=score_group,
        ),
        tuple(phrases),
    )


def regex_rule(
    rule_id: str,
    category: str,
    title: str,
    rationale: str,
    advice: str,
    pattern: str,
    *,
    score_group: str | None = None,
    flags: tuple[str, ...] = ("IGNORECASE",),
) -> RegexRule:
    return RegexRule(
        RuleMetadata(
            id=rule_id,
            category=category,
            title=title,
            rationale=rationale,
            advice=advice,
            score_group=score_group,
        ),
        pattern,
        flags,
    )
