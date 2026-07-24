"""RequestProcessor tests for the one-text, ordered-stage contract."""

from __future__ import annotations

from privacy_guard.config import PolicyAction
from privacy_guard.engine_registry import EngineRegistry
from privacy_guard.engines import RegexEngine
from privacy_guard.request_processor import RequestDecision, RequestProcessor


def _values(action: PolicyAction) -> dict[str, object]:
    return {
        "entity_processing": {
            "stages": [
                {
                    "name": "people",
                    "config": {
                        "engine": "regex",
                        "pattern_catalog": {
                            "entities": [
                                {
                                    "name": "person",
                                    "patterns": [
                                        {
                                            "pattern": "Alice",
                                            "confidence": "high",
                                        }
                                    ],
                                }
                            ]
                        },
                        "replacement": {
                            "strategy": "template",
                            "template": "[{entity}]",
                        },
                    },
                },
                {
                    "config": {
                        "engine": "regex",
                        "pattern_catalog": {
                            "entities": [
                                {
                                    "name": "marker",
                                    "patterns": [
                                        {
                                            "pattern": "person",
                                            "confidence": "medium",
                                        }
                                    ],
                                }
                            ]
                        },
                        "replacement": {
                            "strategy": "template",
                            "template": "<{entity}>",
                        },
                    },
                },
            ]
        },
        "on_detection": {"action": action.value},
    }


def _processor(action: PolicyAction) -> RequestProcessor:
    registry = EngineRegistry()
    registry.register(RegexEngine)
    registry.finalize()
    config = registry.validate_config(_values(action))
    stages = tuple(
        (
            stage.diagnostic_name(index),
            registry.create_engine(stage.config),
        )
        for index, stage in enumerate(config.entity_processing.stages, start=1)
    )
    return RequestProcessor(config, stages)


def test_replace_runs_stages_sequentially_over_the_current_text() -> None:
    result = _processor(PolicyAction.REPLACE).process("Hello Alice")

    assert result.decision is RequestDecision.ALLOW
    assert result.replacement_text == "Hello [<marker>]"
    assert tuple(
        (item.entity, item.source_stage, item.count)
        for item in result.detection_summaries
    ) == (
        ("person", "people", 1),
        ("marker", "regex[2]", 1),
    )


def test_detect_reports_without_returning_replacement_text() -> None:
    result = _processor(PolicyAction.DETECT).process("Hello Alice")

    assert result.decision is RequestDecision.ALLOW
    assert result.replacement_text is None
    assert tuple(item.entity for item in result.detection_summaries) == ("person",)


def test_block_is_a_processor_disposition_not_an_engine_strategy() -> None:
    result = _processor(PolicyAction.BLOCK).process("Hello Alice")

    assert result.decision is RequestDecision.DENY
    assert result.replacement_text is None
    assert result.reason_code == "privacy_guard_blocked"
    assert tuple(item.entity for item in result.detection_summaries) == ("person",)
