"""RequestProcessor tests for the one-text, ordered-stage contract."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from time import monotonic

import pytest

from privacy_guard.config import PolicyAction
from privacy_guard.constants import MAX_BODY_BYTES
from privacy_guard.engines import RegexEngine
from privacy_guard.engines.registry import EngineRegistry
from privacy_guard.errors import (
    EngineConfigurationError,
    EngineLimitExceededError,
    ErrorCode,
    PrivacyGuardError,
)
from privacy_guard.request_processor import RequestDecision, RequestProcessor
from privacy_guard.string_validators import validate_scalar_string
from privacy_guard.timeout import Timeout


def _values(
    action: PolicyAction,
    *,
    include_second_stage: bool = True,
) -> dict[str, object]:
    stages: list[dict[str, object]] = [
        {
            "name": "people",
            "config": {
                "engine": "regex",
                "pattern_catalog": {
                    "entities": [
                        {
                            "name": "person",
                            "rules": [
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
        }
    ]
    if include_second_stage:
        stages.append(
            {
                "config": {
                    "engine": "regex",
                    "pattern_catalog": {
                        "entities": [
                            {
                                "name": "marker",
                                "rules": [
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
            }
        )
    return {
        "entity_processing": {"stages": stages},
        "on_detection": {"action": action.value},
    }


def _processor(
    action: PolicyAction,
    *,
    include_second_stage: bool = True,
) -> RequestProcessor:
    registry = EngineRegistry()
    registry.register(RegexEngine)
    registry.finalize()
    config = registry.validate_config(
        _values(action, include_second_stage=include_second_stage)
    )
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


def test_scalar_validation_rejects_lone_surrogates() -> None:
    with pytest.raises(ValueError, match="Unicode scalar"):
        validate_scalar_string("\ud800")


def test_processor_accepts_exact_body_limit_and_rejects_one_byte_more() -> None:
    processor = _processor(PolicyAction.DETECT, include_second_stage=False)

    exact = processor.process("x" * MAX_BODY_BYTES)

    assert exact.decision is RequestDecision.ALLOW
    with pytest.raises(PrivacyGuardError) as captured:
        processor.process("x" * (MAX_BODY_BYTES + 1))
    assert captured.value.code is ErrorCode.REQUEST_BODY_TOO_LARGE


def test_exact_limit_requests_complete_concurrently() -> None:
    processor = _processor(PolicyAction.DETECT, include_second_stage=False)
    text = "x" * MAX_BODY_BYTES

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = tuple(executor.map(processor.process, (text,) * 4))

    assert all(result.decision is RequestDecision.ALLOW for result in results)


def test_exact_limit_request_completes_with_multiple_stages() -> None:
    result = _processor(PolicyAction.DETECT).process("x" * MAX_BODY_BYTES)

    assert result.decision is RequestDecision.ALLOW


def test_timeout_returns_the_bounded_limit_deny(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(
        Timeout,
        "from_seconds",
        classmethod(lambda cls, seconds: cls(deadline=monotonic() - 1)),
    )

    with caplog.at_level(logging.INFO, logger="privacy_guard.request_processor"):
        result = _processor(PolicyAction.DETECT).process("Hello Alice")

    assert result.decision is RequestDecision.DENY
    assert result.reason_code == "privacy_guard_limit_exceeded"
    assert "privacy_guard_processing_limit kind=timeout" in caplog.text
    assert "Alice" not in caplog.text


def test_engine_resource_limit_returns_the_bounded_limit_deny(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def exceed_limit(*_: object, **__: object) -> object:
        raise EngineLimitExceededError("sensitive resource detail")

    monkeypatch.setattr(RegexEngine, "_run", exceed_limit)

    with caplog.at_level(logging.INFO, logger="privacy_guard.request_processor"):
        result = _processor(PolicyAction.DETECT).process("Hello Alice")

    assert result.decision is RequestDecision.DENY
    assert result.reason_code == "privacy_guard_limit_exceeded"
    assert "privacy_guard_processing_limit kind=resource" in caplog.text
    assert "Alice" not in caplog.text
    assert "sensitive resource detail" not in caplog.text


def test_engine_configuration_failure_maps_to_invalid_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_config(*_: object, **__: object) -> object:
        raise EngineConfigurationError("sensitive configuration detail")

    monkeypatch.setattr(RegexEngine, "_run", reject_config)

    with pytest.raises(PrivacyGuardError) as captured:
        _processor(PolicyAction.DETECT).process("Hello Alice")

    assert captured.value.code is ErrorCode.CONFIG_INVALID
    assert "sensitive configuration detail" not in str(captured.value)
