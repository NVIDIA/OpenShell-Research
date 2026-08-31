from __future__ import annotations

from slop_cop.rules.builtins._helpers import regex_rule

RULES = (
    regex_rule(
        "ending.participial-tail",
        "ending",
        "Participial explanation tail",
        "The sentence adds a generic trailing explanation using a participle.",
        "State the concrete effect in a separate clause or remove the tail.",
        r",\s+(?:ensuring|enabling|allowing|highlighting|underscoring|showcasing|reflecting)\s+[^.!?\n]{8,100}[.!?]",
    ),
    regex_rule(
        "ending.generic-explanation",
        "ending",
        "Generic explanatory ending",
        "The ending claims broad value without naming a concrete result.",
        "End with the specific result, limitation, or next action.",
        r"\b(?:paving the way for|setting the stage for|"
        r"marking a significant step toward)\s+[^.!?\n]{3,100}[.!?]",
    ),
)
