from __future__ import annotations

from slop_cop.rules.api import Rule
from slop_cop.rules.builtins.artifacts import RULES as ARTIFACT_RULES
from slop_cop.rules.builtins.attribution import RULES as ATTRIBUTION_RULES
from slop_cop.rules.builtins.endings import RULES as ENDING_RULES
from slop_cop.rules.builtins.repetition import RULES as REPETITION_RULES
from slop_cop.rules.builtins.rhetoric import RULES as RHETORIC_RULES
from slop_cop.rules.builtins.structure import RULES as STRUCTURE_RULES
from slop_cop.rules.builtins.vocabulary import RULES as VOCABULARY_RULES

BUILTIN_RULES: tuple[Rule, ...] = (
    *ARTIFACT_RULES,
    *RHETORIC_RULES,
    *VOCABULARY_RULES,
    *REPETITION_RULES,
    *ATTRIBUTION_RULES,
    *ENDING_RULES,
    *STRUCTURE_RULES,
)
