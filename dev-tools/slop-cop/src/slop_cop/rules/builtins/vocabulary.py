from __future__ import annotations

from slop_cop.rules.builtins._helpers import phrase_rule

_STOCK = (
    ("delves", "delves into"),
    ("tapestry", "rich tapestry"),
    ("realm", "in the realm of"),
    ("underscore", "underscores the importance of"),
    ("navigate", "navigate the complexities of"),
    ("multifaceted", "multifaceted approach"),
)


RULES = tuple(
    phrase_rule(
        f"vocabulary.stock.{slug}",
        "vocabulary",
        f"Stock phrase: {phrase}",
        "The phrase is generic and often adds little technical information.",
        "Replace it with the specific action, constraint, or effect.",
        phrase,
    )
    for slug, phrase in _STOCK
)
