from __future__ import annotations

import asyncio
from pathlib import Path

from slop_cop.config import load_config
from slop_cop.document import build_document
from slop_cop.rules.api import RuleContext, RuleEvaluation
from slop_cop.rules.registry import build_registry

CONFIG_PATH = Path(__file__).parents[1] / "slop-cop.toml"


def test_every_configured_rule_has_valid_metadata_and_evaluation() -> None:
    config = load_config(CONFIG_PATH)
    registry = build_registry(config)
    document = build_document("note.md", "A direct technical statement with enough prose.")
    context = RuleContext(document=document)

    async def evaluate_all() -> None:
        for configured in registry:
            result = await configured.rule.evaluate(context, object())
            assert isinstance(result, RuleEvaluation)
            assert configured.metadata.category in config.categories
            assert (
                configured.policy.cap <= config.categories[configured.metadata.category].cap
                or configured.metadata.category == "artifact"
            )

    asyncio.run(evaluate_all())
    assert len({rule.metadata.id for rule in registry}) == len(registry.rules)


def test_production_custom_registry_is_empty() -> None:
    from slop_cop.rules.custom import CUSTOM_RULES

    assert CUSTOM_RULES == ()
