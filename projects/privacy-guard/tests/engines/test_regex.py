from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError

import privacy_guard.engines.regex as regex_module
from privacy_guard.engines import (
    EngineConfigurationError,
    EngineLimitExceeded,
    EntityProcessingStrategy,
    RegexEngine,
    RegexEngineConfig,
)
from privacy_guard.timeout import Timeout, TimeoutExpired


def _config(
    patterns: list[dict[str, object]],
    *,
    replacement: dict[str, object] | None = None,
) -> RegexEngineConfig:
    values: dict[str, object] = {
        "engine": "regex",
        "pattern_catalog": {
            "entities": [
                {
                    "name": "token",
                    "patterns": patterns,
                }
            ]
        },
    }
    if replacement is not None:
        values["replacement"] = replacement
    return RegexEngineConfig.model_validate(values)


def _run(
    config: RegexEngineConfig,
    text: str,
    strategy: EntityProcessingStrategy = EntityProcessingStrategy.DETECT,
) -> tuple[str, list[tuple[str, int, int, str]]]:
    result = RegexEngine(config, None).run(
        text,
        strategy=strategy,
        timeout=Timeout.from_seconds(1),
    )
    return result.text, [
        (
            detection.entity,
            detection.start,
            detection.end,
            detection.metadata["pattern"],
        )
        for detection in result.detections
    ]


def test_detects_overlaps_and_orders_matches_deterministically() -> None:
    config = _config(
        [
            {"name": "pair", "pattern": "aa", "confidence": "high"},
            {"name": "suffix", "pattern": "a$", "confidence": "medium"},
        ]
    )

    output, detections = _run(config, "aaa")

    assert output == "aaa"
    assert detections == [
        ("token", 0, 2, "pair"),
        ("token", 1, 3, "pair"),
        ("token", 2, 3, "suffix"),
    ]


def test_optional_names_derive_identity_without_affecting_internal_marker() -> None:
    config = _config(
        [
            {"name": "same-name", "pattern": "x", "confidence": "high"},
            {"name": "same_name", "pattern": "y", "confidence": "high"},
            {"pattern": "z", "confidence": "high"},
        ]
    )

    _, detections = _run(config, "xyz")

    assert [item[3] for item in detections] == [
        "same-name",
        "same_name",
        "token.patterns[2]",
    ]


def test_numeric_backreferences_keep_original_group_numbers() -> None:
    config = _config([{"pattern": r"(a)\1", "confidence": "high"}])

    _, detections = _run(config, "aa")

    assert [(item[1], item[2]) for item in detections] == [(0, 2)]


def test_explicit_flags_are_supported() -> None:
    config = _config(
        [
            {
                "pattern": "^x.$",
                "confidence": "high",
                "ignore_case": True,
                "multiline": True,
                "dot_all": True,
                "ascii": True,
            }
        ]
    )

    _, detections = _run(config, "X\n")

    assert [(item[1], item[2]) for item in detections] == [(0, 2)]


@pytest.mark.parametrize(
    "pattern",
    [
        "",
        "x*",
        "(?P<user>x)",
        "(?i:x)",
    ],
)
def test_invalid_patterns_are_rejected_content_safely(pattern: str) -> None:
    with pytest.raises(ValidationError) as exception_info:
        _config([{"pattern": pattern, "confidence": "high"}])

    if pattern:
        assert pattern not in str(exception_info.value)


def test_contextual_zero_width_failure_is_atomic_at_runtime() -> None:
    config = _config([{"pattern": "(?=a)", "confidence": "high"}])
    engine = RegexEngine(config, None)

    with pytest.raises(EngineConfigurationError):
        engine.run(
            "a",
            strategy=EntityProcessingStrategy.DETECT,
            timeout=Timeout.from_seconds(1),
        )


def test_duplicate_supplied_names_are_rejected_but_unnamed_patterns_are_not() -> None:
    with pytest.raises(ValidationError):
        _config(
            [
                {"name": "duplicate", "pattern": "x", "confidence": "high"},
                {"name": "duplicate", "pattern": "y", "confidence": "high"},
            ]
        )

    config = _config(
        [
            {"pattern": "x", "confidence": "high"},
            {"pattern": "y", "confidence": "high"},
        ]
    )
    assert len(config.pattern_catalog.entities[0].patterns) == 2


def test_replacement_selects_ranked_non_overlapping_winners() -> None:
    config = _config(
        [
            {"name": "long-low", "pattern": "abc", "confidence": "low"},
            {"name": "short-high", "pattern": "bc", "confidence": "high"},
        ],
        replacement={"strategy": "template", "template": "<{entity}>"},
    )

    output, detections = _run(
        config,
        "abc",
        EntityProcessingStrategy.REPLACE,
    )

    assert output == "a<token>"
    assert len(detections) == 2


def test_replacement_requires_an_engine_specific_recipe() -> None:
    config = _config([{"pattern": "x", "confidence": "high"}])

    with pytest.raises(EngineConfigurationError):
        _run(config, "x", EntityProcessingStrategy.REPLACE)


@pytest.mark.parametrize(
    "replacement",
    [
        {"strategy": "template", "template": "{unknown}"},
        {"strategy": "template", "template": "{entity.attr}"},
        {"strategy": "template", "template": "{entity!r}"},
        {"strategy": "template", "template": "{entity:>10}"},
        {"strategy": "template", "template": "{"},
    ],
)
def test_template_language_allows_only_literal_text_and_entity(
    replacement: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        _config(
            [{"pattern": "x", "confidence": "high"}],
            replacement=replacement,
        )


def test_replacement_size_is_projected_before_rendering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(regex_module, "MAX_BODY_BYTES", 4)
    config = _config(
        [{"pattern": "x", "confidence": "high"}],
        replacement={"strategy": "template", "template": "[{entity}]"},
    )

    with pytest.raises(EngineLimitExceeded):
        _run(config, "x", EntityProcessingStrategy.REPLACE)


def test_pattern_search_has_an_enforceable_timeout() -> None:
    config = _config([{"pattern": "(a+)+$", "confidence": "high"}])
    engine = RegexEngine(config, None)

    with pytest.raises(TimeoutExpired):
        engine.run(
            "a" * 100_000 + "!",
            strategy=EntityProcessingStrategy.DETECT,
            timeout=Timeout.from_seconds(0.001),
        )


def test_patterns_compile_during_validation_and_preparation_not_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compile_count = 0
    original_compile = regex_module.regex.compile

    def recording_compile(pattern: str, flags: int = 0) -> object:
        nonlocal compile_count
        compile_count += 1
        return original_compile(pattern, flags)

    monkeypatch.setattr(regex_module.regex, "compile", recording_compile)
    config = _config([{"pattern": "x", "confidence": "high"}])
    engine = RegexEngine(config, None)
    prepared_count = compile_count

    engine.run(
        "x",
        strategy=EntityProcessingStrategy.DETECT,
        timeout=Timeout.from_seconds(1),
    )

    assert prepared_count > 0
    assert compile_count == prepared_count


def test_regex_engine_is_safe_for_concurrent_runs() -> None:
    engine = RegexEngine(
        _config([{"pattern": "x", "confidence": "high"}]),
        None,
    )

    def run(text: str) -> int:
        return len(
            engine.run(
                text,
                strategy=EntityProcessingStrategy.DETECT,
                timeout=Timeout.from_seconds(1),
            ).detections
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        counts = tuple(executor.map(run, ("x",) * 16))

    assert counts == (1,) * 16
