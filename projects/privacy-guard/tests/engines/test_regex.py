from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from pydantic import ValidationError

import privacy_guard.engines._replacement as replacement_module
import privacy_guard.engines.regex as regex_module
from privacy_guard.engines import (
    EngineConfigurationError,
    EngineLimitExceededError,
    EntityProcessingStrategy,
    RegexEngine,
    RegexEngineConfig,
    RegexPatternCatalog,
)
from privacy_guard.errors import TimeoutExpiredError
from privacy_guard.timeout import Timeout


def _config(
    rules: list[dict[str, object]],
    *,
    replacement: dict[str, object] | None = None,
) -> RegexEngineConfig:
    values: dict[str, object] = {
        "engine": "regex",
        "pattern_catalog": {
            "entities": [
                {
                    "name": "token",
                    "rules": rules,
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
            detection.metadata["rule"],
        )
        for detection in result.detections
    ]


def _catalog(pattern: str) -> RegexPatternCatalog:
    return RegexPatternCatalog.model_validate(
        {
            "entities": [
                {
                    "name": "token",
                    "rules": [
                        {
                            "pattern": pattern,
                            "confidence": "high",
                        }
                    ],
                }
            ]
        }
    )


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
        "token.rules[2]",
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


@pytest.mark.parametrize(
    ("pattern", "text"),
    [
        ("x|(?=SECRET-zero-width-493)", "SECRET-zero-width-493"),
        ("(?=secret)", "secret"),
        ("(?<=prefix)", "prefix"),
        (r"\b", "secret"),
        ("x|(?:y|(?=secret))", "secret"),
    ],
)
def test_contextual_zero_width_match_is_invalid_configuration_at_runtime(
    pattern: str,
    text: str,
) -> None:
    config = _config([{"pattern": pattern, "confidence": "high"}])
    engine = RegexEngine(config, None)

    with pytest.raises(
        EngineConfigurationError,
        match="regex engine configuration is invalid",
    ) as exception_info:
        engine.run(
            text,
            strategy=EntityProcessingStrategy.DETECT,
            timeout=Timeout.from_seconds(1),
        )

    assert pattern not in str(exception_info.value)


@pytest.mark.parametrize(
    ("pattern", "text", "expected_span"),
    [
        ("(?<=prefix)secret(?=suffix)", "prefixsecretsuffix", (6, 12)),
        (r"\bsecret\b", "a secret value", (2, 8)),
        (
            r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
            r"(?![\w.-])",
            "contact a@b.com",
            (8, 15),
        ),
    ],
)
def test_patterns_with_consuming_lookarounds_or_boundaries_remain_valid(
    pattern: str,
    text: str,
    expected_span: tuple[int, int],
) -> None:
    config = _config([{"pattern": pattern, "confidence": "high"}])

    _, detections = _run(config, text)

    assert [(item[1], item[2]) for item in detections] == [expected_span]


def test_duplicate_supplied_names_are_rejected_but_unnamed_rules_are_not() -> None:
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
    assert len(config.pattern_catalog.entities[0].rules) == 2


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
    monkeypatch.setattr(replacement_module, "MAX_BODY_BYTES", 4)
    config = _config(
        [{"pattern": "x", "confidence": "high"}],
        replacement={"strategy": "template", "template": "[{entity}]"},
    )

    with pytest.raises(EngineLimitExceededError):
        _run(config, "x", EntityProcessingStrategy.REPLACE)


def test_pattern_search_has_an_enforceable_timeout() -> None:
    config = _config([{"pattern": "(a+)+$", "confidence": "high"}])
    engine = RegexEngine(config, None)

    with pytest.raises(TimeoutExpiredError):
        engine.run(
            "a" * 100_000 + "!",
            strategy=EntityProcessingStrategy.DETECT,
            timeout=Timeout.from_seconds(0.001),
        )


def test_patterns_compile_during_validation_and_preparation_not_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    regex_module._clear_compiled_pattern_cache()
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


def test_compiled_catalog_cache_evicts_least_recently_used_entry(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    regex_module._clear_compiled_pattern_cache()
    catalogs = tuple(_catalog(f"sensitive-pattern-{suffix}") for suffix in "abc")

    try:
        first_rules = regex_module._compile_pattern_catalog(catalogs[0])
        entry_weight = regex_module._COMPILED_PATTERN_CACHE[catalogs[0]][1]
        monkeypatch.setattr(
            regex_module,
            "MAX_REGEX_COMPILED_CACHE_WEIGHT_BYTES",
            entry_weight * 2,
        )
        with caplog.at_level(logging.DEBUG, logger="privacy_guard.engines.regex"):
            regex_module._compile_pattern_catalog(catalogs[1])
            assert regex_module._compile_pattern_catalog(catalogs[0]) is first_rules
            regex_module._compile_pattern_catalog(catalogs[2])

        assert tuple(regex_module._COMPILED_PATTERN_CACHE) == (
            catalogs[0],
            catalogs[2],
        )
        assert regex_module._COMPILED_PATTERN_CACHE_WEIGHT_BYTES == sum(
            entry[1] for entry in regex_module._COMPILED_PATTERN_CACHE.values()
        )
        assert (
            "privacy_guard_cache_eviction cache=regex_compiled entries=1" in caplog.text
        )
        assert "sensitive-pattern" not in caplog.text
    finally:
        regex_module._clear_compiled_pattern_cache()


def test_compiled_catalog_cache_skips_oversized_valid_entry(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    regex_module._clear_compiled_pattern_cache()
    monkeypatch.setattr(regex_module, "MAX_REGEX_COMPILED_CACHE_WEIGHT_BYTES", 1)
    catalog = _catalog("sensitive-oversized-pattern")

    try:
        with caplog.at_level(logging.DEBUG, logger="privacy_guard.engines.regex"):
            first = regex_module._compile_pattern_catalog(catalog)
            second = regex_module._compile_pattern_catalog(catalog)

        assert first is not second
        assert regex_module._COMPILED_PATTERN_CACHE == {}
        assert regex_module._COMPILED_PATTERN_CACHE_WEIGHT_BYTES == 0
        assert caplog.text.count("privacy_guard_cache_skip cache=regex_compiled") == 2
        assert "sensitive-oversized-pattern" not in caplog.text
    finally:
        regex_module._clear_compiled_pattern_cache()


def test_compiled_catalog_failure_preserves_existing_weight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    regex_module._clear_compiled_pattern_cache()
    retained_catalog = _catalog("retained")
    regex_module._compile_pattern_catalog(retained_catalog)
    retained_weight = regex_module._COMPILED_PATTERN_CACHE_WEIGHT_BYTES
    retained_entries = tuple(regex_module._COMPILED_PATTERN_CACHE)

    def fail_compile(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise ValueError("expected test failure")

    monkeypatch.setattr(regex_module, "_compile_rule", fail_compile)
    try:
        with pytest.raises(ValueError, match="expected test failure"):
            regex_module._compile_pattern_catalog(_catalog("failing"))

        assert tuple(regex_module._COMPILED_PATTERN_CACHE) == retained_entries
        assert regex_module._COMPILED_PATTERN_CACHE_WEIGHT_BYTES == retained_weight
    finally:
        regex_module._clear_compiled_pattern_cache()


def test_compiled_catalog_same_key_race_accounts_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    regex_module._clear_compiled_pattern_cache()
    worker_count = 4
    workers_ready = Barrier(worker_count)
    catalog = _catalog("same-key")
    original_compile_rule = regex_module._compile_rule

    def synchronized_compile(
        entity: regex_module.RegexEntity,
        rule: regex_module.RegexRule,
        catalog_index: int,
        entity_rule_index: int,
    ) -> regex_module._CompiledRule:
        workers_ready.wait(timeout=5)
        return original_compile_rule(
            entity,
            rule,
            catalog_index,
            entity_rule_index,
        )

    monkeypatch.setattr(regex_module, "_compile_rule", synchronized_compile)
    try:
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            results = tuple(
                executor.map(
                    lambda _: regex_module._compile_pattern_catalog(catalog),
                    range(worker_count),
                )
            )

        assert all(result is results[0] for result in results)
        assert len(regex_module._COMPILED_PATTERN_CACHE) == 1
        assert (
            regex_module._COMPILED_PATTERN_CACHE_WEIGHT_BYTES
            == (next(iter(regex_module._COMPILED_PATTERN_CACHE.values()))[1])
        )
    finally:
        regex_module._clear_compiled_pattern_cache()


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
