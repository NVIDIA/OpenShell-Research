"""Behavior, safety, cache, file-loading, and concurrency tests for regex-body."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from pydantic import ValidationError

import egress_gate.gates.regex_body as regex_module
from egress_gate.errors import (
    GateConfigurationError,
    GateLimitExceededError,
    TimeoutExpiredError,
)
from egress_gate.gates import RegexBodyConfig, RegexBodyGate, RegexPatternCatalog
from egress_gate.request import HttpRequest, HttpTarget, RequestContext
from egress_gate.result import GateControl, GateEvaluation
from egress_gate.timeout import Timeout


def _config(
    rules: list[dict[str, object]],
    *,
    mode: str = "detect",
    replacement: dict[str, object] | None = None,
) -> RegexBodyConfig:
    values: dict[str, object] = {
        "pattern_catalog": {
            "entities": [{"name": "token", "rules": rules}],
        },
        "mode": mode,
    }
    if replacement is not None:
        values["replacement"] = replacement
    return RegexBodyConfig.model_validate(values)


def _request(body: bytes) -> HttpRequest:
    return HttpRequest(
        context=RequestContext(request_id="request-1", sandbox_id="sandbox-1"),
        target=HttpTarget(
            scheme="https",
            host="example.com",
            port=443,
            method="POST",
            path="/",
            query="",
        ),
        headers=(),
        body=body,
    )


def _run(config: RegexBodyConfig, text: str) -> GateEvaluation:
    return RegexBodyGate(config, None).evaluate(
        _request(text.encode("utf-8")), timeout=Timeout.from_seconds(1)
    )


def _catalog(pattern: str) -> RegexPatternCatalog:
    return RegexPatternCatalog.model_validate(
        {
            "entities": [
                {
                    "name": "token",
                    "rules": [{"pattern": pattern, "confidence": "high"}],
                }
            ]
        }
    )


def test_detect_mode_reports_overlaps_without_mutating_the_body() -> None:
    evaluation = _run(
        _config(
            [
                {"name": "pair", "pattern": "aa", "confidence": "high"},
                {"name": "suffix", "pattern": "a$", "confidence": "medium"},
            ]
        ),
        "aaa",
    )

    assert evaluation.control is GateControl.PROCEED
    assert evaluation.patch.replacement_body is None
    assert len(evaluation.findings) == 2
    assert sum(finding.count for finding in evaluation.findings) == 3
    assert {finding.label for finding in evaluation.findings} == {"token"}


def test_equivalent_detections_are_aggregated_before_evaluation_bounds() -> None:
    evaluation = _run(
        _config([{"pattern": "x", "confidence": "high"}]),
        "x" * 33,
    )

    assert evaluation.findings == (
        regex_module.Finding(
            type="sensitive_entity",
            label="token",
            count=33,
            confidence="high",
        ),
    )


def test_deny_mode_is_terminal_and_uses_the_stable_gate_reason() -> None:
    evaluation = _run(
        _config([{"pattern": "secret", "confidence": "high"}], mode="deny"),
        "contains secret",
    )

    assert evaluation.control is GateControl.DENY
    assert evaluation.reason_code == "egress_gate_regex_denied"
    assert evaluation.patch.is_empty
    assert len(evaluation.findings) == 1


def test_replace_mode_preserves_explicit_replacement_intent() -> None:
    config = _config(
        [{"pattern": "secret", "confidence": "high"}],
        mode="replace",
        replacement={"strategy": "template", "template": "[{entity}]"},
    )

    changed = _run(config, "contains secret")
    unchanged = _run(config, "no match")

    assert changed.patch.replacement_body == b"contains [token]"
    assert unchanged.patch.replacement_body == b"no match"
    assert not unchanged.patch.is_empty


def test_replacement_recipe_is_required_only_for_replace_mode() -> None:
    with pytest.raises(ValidationError):
        _config([{"pattern": "x", "confidence": "high"}], mode="replace")

    with pytest.raises(ValidationError):
        _config(
            [{"pattern": "x", "confidence": "high"}],
            mode="detect",
            replacement={"strategy": "template", "template": "[{entity}]"},
        )


def test_regex_features_and_backreferences_keep_their_original_semantics() -> None:
    backreference = _run(
        _config([{"pattern": r"(a)\1", "confidence": "high"}]),
        "aa",
    )
    flags = _run(
        _config(
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
        ),
        "X\n",
    )

    assert len(backreference.findings) == 1
    assert len(flags.findings) == 1


@pytest.mark.parametrize("pattern", ["", "(?i:x)"])
def test_structurally_invalid_patterns_are_rejected_content_safely(
    pattern: str,
) -> None:
    with pytest.raises(ValidationError) as exception_info:
        _config([{"pattern": pattern, "confidence": "high"}])

    if pattern:
        assert pattern not in str(exception_info.value)


@pytest.mark.parametrize("pattern", ["x*", "(?P<user>x)"])
def test_compile_dependent_pattern_errors_are_rejected_during_preparation(
    pattern: str,
) -> None:
    config = _config([{"pattern": pattern, "confidence": "high"}])

    with pytest.raises(GateConfigurationError) as exception_info:
        RegexBodyGate(config, None, timeout=Timeout.from_seconds(1))

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
def test_contextual_zero_width_matches_fail_during_evaluation(
    pattern: str,
    text: str,
) -> None:
    config = _config([{"pattern": pattern, "confidence": "high"}])

    with pytest.raises(
        GateConfigurationError,
        match="regex-body configuration matches an empty span",
    ) as exception_info:
        _run(config, text)

    assert pattern not in str(exception_info.value)


@pytest.mark.parametrize(
    ("pattern", "text"),
    [
        ("(?<=prefix)secret(?=suffix)", "prefixsecretsuffix"),
        (r"\bsecret\b", "a secret value"),
        (
            r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
            r"(?![\w.-])",
            "contact a@b.com",
        ),
    ],
)
def test_consuming_lookarounds_and_boundaries_remain_valid(
    pattern: str,
    text: str,
) -> None:
    evaluation = _run(
        _config([{"pattern": pattern, "confidence": "high"}]),
        text,
    )
    assert len(evaluation.findings) == 1


def test_duplicate_rule_names_are_rejected_but_unnamed_rules_are_allowed() -> None:
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
    evaluation = _run(
        _config(
            [
                {"name": "long-low", "pattern": "abc", "confidence": "low"},
                {"name": "short-high", "pattern": "bc", "confidence": "high"},
            ],
            mode="replace",
            replacement={"strategy": "template", "template": "<{entity}>"},
        ),
        "abc",
    )

    assert evaluation.patch.replacement_body == b"a<token>"
    assert len(evaluation.findings) == 2


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
def test_replacement_template_language_is_constrained(
    replacement: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        _config(
            [{"pattern": "x", "confidence": "high"}],
            mode="replace",
            replacement=replacement,
        )


def test_replacement_size_is_projected_before_rendering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(regex_module, "MAX_BODY_BYTES", 4)
    config = _config(
        [{"pattern": "x", "confidence": "high"}],
        mode="replace",
        replacement={"strategy": "template", "template": "[{entity}]"},
    )

    with pytest.raises(GateLimitExceededError):
        _run(config, "x")


def test_pattern_search_has_an_enforceable_timeout() -> None:
    config = _config([{"pattern": "(a+)+$", "confidence": "high"}])

    with pytest.raises(TimeoutExpiredError):
        RegexBodyGate(config, None).evaluate(
            _request((b"a" * 100_000) + b"!"),
            timeout=Timeout.from_seconds(0.001),
        )


def test_patterns_compile_during_preparation_not_validation_or_each_run(
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
    assert compile_count == 0
    RegexBodyGate(config, None, timeout=Timeout.from_seconds(1))
    prepared_count = compile_count

    _run(config, "x")

    assert prepared_count > 0
    assert compile_count == prepared_count


def test_gate_preparation_honors_an_expired_timeout() -> None:
    regex_module._clear_compiled_pattern_cache()
    config = _config([{"pattern": "x", "confidence": "high"}])

    with pytest.raises(TimeoutExpiredError):
        RegexBodyGate(config, None, timeout=Timeout(deadline=0))


def test_compiled_catalog_cache_wait_honors_preparation_timeout() -> None:
    regex_module._clear_compiled_pattern_cache()
    catalog = _catalog("cache-contention")
    regex_module._COMPILED_PATTERN_CACHE_LOCK.acquire()
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                regex_module._compile_pattern_catalog,
                catalog,
                timeout=Timeout.from_seconds(0.01),
            )
            with pytest.raises(TimeoutExpiredError):
                future.result(timeout=1)
    finally:
        regex_module._COMPILED_PATTERN_CACHE_LOCK.release()
        regex_module._clear_compiled_pattern_cache()


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
        with caplog.at_level(
            logging.DEBUG,
            logger="egress_gate.gates.regex_body",
        ):
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
            "egress_gate_cache_eviction cache=regex_compiled entries=1" in caplog.text
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
        with caplog.at_level(
            logging.DEBUG,
            logger="egress_gate.gates.regex_body",
        ):
            first = regex_module._compile_pattern_catalog(catalog)
            second = regex_module._compile_pattern_catalog(catalog)

        assert first is not second
        assert regex_module._COMPILED_PATTERN_CACHE == {}
        assert regex_module._COMPILED_PATTERN_CACHE_WEIGHT_BYTES == 0
        assert caplog.text.count("egress_gate_cache_skip cache=regex_compiled") == 2
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
        *,
        timeout: Timeout | None = None,
    ) -> regex_module._CompiledRule:
        workers_ready.wait(timeout=5)
        return original_compile_rule(
            entity,
            rule,
            catalog_index,
            entity_rule_index,
            timeout=timeout,
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
            == next(iter(regex_module._COMPILED_PATTERN_CACHE.values()))[1]
        )
    finally:
        regex_module._clear_compiled_pattern_cache()


def test_regex_body_gate_is_safe_for_concurrent_runs() -> None:
    gate = RegexBodyGate(
        _config([{"pattern": "x", "confidence": "high"}]),
        None,
    )

    def run(text: str) -> int:
        return len(
            gate.evaluate(
                _request(text.encode()), timeout=Timeout.from_seconds(1)
            ).findings
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        counts = tuple(executor.map(run, ("x",) * 16))

    assert counts == (1,) * 16


def test_relative_yaml_catalog_loading_rejects_aliases_and_traversal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    directory = tmp_path
    monkeypatch.chdir(directory)
    (directory / "patterns.yaml").write_text(
        "entities:\n"
        "  - name: token\n"
        "    rules:\n"
        "      - pattern: secret\n"
        "        confidence: high\n"
    )

    config = RegexBodyConfig.model_validate(
        {"pattern_catalog": "patterns.yaml", "mode": "detect"}
    )
    assert len(_run(config, "secret").findings) == 1

    with pytest.raises(ValidationError):
        RegexBodyConfig.model_validate(
            {"pattern_catalog": "../patterns.yaml", "mode": "detect"}
        )
    (directory / "aliases.yaml").write_text(
        "shared: &shared\n"
        "  name: token\n"
        "  rules:\n"
        "    - pattern: secret\n"
        "      confidence: high\n"
        "entities:\n"
        "  - *shared\n"
    )
    with pytest.raises(ValidationError):
        RegexBodyConfig.model_validate(
            {"pattern_catalog": "aliases.yaml", "mode": "detect"}
        )
