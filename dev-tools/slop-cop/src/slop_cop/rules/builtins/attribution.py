from __future__ import annotations

from slop_cop.rules.builtins._helpers import regex_rule

RULES = (
    regex_rule(
        "attribution.vague-authority",
        "attribution",
        "Vague authority",
        "The claim attributes a view to unnamed authorities.",
        "Name and link the relevant source or make the claim in the author's voice.",
        r"\b(?:experts|researchers|industry leaders|many observers|critics)\s+"
        r"(?:say|believe|agree|argue|suggest|warn)\b(?![^\n]{0,120}\]\([^\n)]+\))",
    ),
    regex_rule(
        "attribution.citationless-study",
        "attribution",
        "Unidentified study",
        "The sentence cites research without identifying a source nearby.",
        "Name and link the study or describe the evidence directly.",
        r"\b(?:a|one|recent|new)\s+(?:study|report|survey|analysis)\s+(?:shows?|finds?|found|suggests?|indicates?)\b(?![^\n]{0,160}\]\([^\n)]+\))",
    ),
)
