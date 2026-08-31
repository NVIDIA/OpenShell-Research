from __future__ import annotations

from slop_cop.rules.builtins._helpers import phrase_rule, regex_rule

RULES = (
    phrase_rule(
        "artifact.ai-disclosure",
        "artifact",
        "Assistant disclosure",
        "The prose contains a disclosure made by an automated assistant.",
        "Remove the disclosure and state the relevant fact directly.",
        "as an AI language model",
        "as an artificial intelligence",
    ),
    regex_rule(
        "artifact.chat-preamble",
        "artifact",
        "Chat response preamble",
        "The opening addresses a requester instead of the Dev Note reader.",
        "Remove the response preamble and begin with the technical subject.",
        r"\b(?:certainly|absolutely|of course)[,!]?\s+(?:here(?:'s| is)|i can|let(?:'s| us))\b",
    ),
    regex_rule(
        "artifact.continuation-offer",
        "artifact",
        "Continuation offer",
        "The prose offers another response rather than completing the note.",
        "Remove the offer or add the useful material to the note.",
        r"\b(?:let me know if you(?:'d| would) like|"
        r"i can also (?:provide|expand|help)|would you like me to)\b",
    ),
    regex_rule(
        "artifact.placeholder",
        "artifact",
        "Unresolved placeholder",
        "The prose contains an unresolved drafting placeholder.",
        "Replace the placeholder with final content or remove it.",
        r"(?:\[(?:insert|add|todo|tbd)[^\]\n]{0,80}\]|\b(?:TODO|TBD):?\b)",
        flags=(),
    ),
    regex_rule(
        "artifact.instruction-residue",
        "artifact",
        "Drafting instruction",
        "The prose contains an instruction for producing the answer.",
        "Remove the drafting instruction from the published note.",
        r"\b(?:rewrite|revise|generate|draft)\s+(?:the|this)\s+(?:answer|response|section)\s+(?:to|so|using|with)\b",
    ),
)
