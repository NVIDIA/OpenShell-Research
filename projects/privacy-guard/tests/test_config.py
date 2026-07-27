from __future__ import annotations

import logging
import os
import subprocess
import sys
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

import privacy_guard.engines.regex as regex_module
from privacy_guard.config import (
    PolicyAction,
    configuration_fingerprint_and_size,
)
from privacy_guard.engines import (
    RegexEngine,
    RegexEngineConfig,
    RegexPatternCatalog,
)
from privacy_guard.engines.registry import EngineRegistry
from privacy_guard.errors import ErrorCode, PrivacyGuardError


def _registry() -> EngineRegistry:
    registry = EngineRegistry()
    registry.register(RegexEngine)
    registry.finalize()
    return registry


def _config(
    *,
    action: str = "detect",
    replacement: dict[str, object] | None = None,
    stage_name: str | None = None,
):
    engine_config = {
        "engine": "regex",
        "pattern_catalog": {
            "entities": [
                {
                    "name": "email",
                    "patterns": [
                        {
                            "pattern": r"\buser@example\.com\b",
                            "confidence": "high",
                        }
                    ],
                }
            ]
        },
    }
    if replacement is not None:
        engine_config["replacement"] = replacement
    stage = {"config": engine_config}
    if stage_name is not None:
        stage["name"] = stage_name
    return {
        "entity_processing": {"stages": [stage]},
        "on_detection": {"action": action},
    }


@pytest.mark.parametrize("action", list(PolicyAction))
def test_policy_action_uses_detect_block_replace(action: PolicyAction) -> None:
    replacement: dict[str, object] | None = (
        {"strategy": "template", "template": "[{entity}]"}
        if action is PolicyAction.REPLACE
        else None
    )
    config = _registry().validate_config(
        _config(action=action.value, replacement=replacement)
    )

    assert config.on_detection.action is action
    assert [item.value for item in PolicyAction] == ["detect", "block", "replace"]


def test_known_discriminator_constructs_the_exact_engine_config() -> None:
    config = _registry().validate_config(_config())
    stage = config.entity_processing.stages[0]

    assert type(stage.config) is RegexEngineConfig
    assert type(stage.config.pattern_catalog) is RegexPatternCatalog
    assert stage.config.pattern_catalog.entities[0].patterns[0].name is None
    assert stage.diagnostic_name(1) == "regex[1]"


def test_policy_accepts_ten_stages_and_rejects_eleven() -> None:
    registry = _registry()
    exact = _config()
    stage = deepcopy(exact["entity_processing"]["stages"][0])
    exact["entity_processing"]["stages"] = [deepcopy(stage) for _ in range(10)]
    oversized = deepcopy(exact)
    oversized["entity_processing"]["stages"].append(deepcopy(stage))

    parsed = registry.validate_config(exact)

    assert len(parsed.entity_processing.stages) == 10
    with pytest.raises(PrivacyGuardError) as captured:
        registry.validate_config(oversized)
    assert captured.value.code is ErrorCode.CONFIG_INVALID


def test_explicit_stage_name_is_the_diagnostic_source() -> None:
    config = _registry().validate_config(_config(stage_name="credentials"))

    assert config.entity_processing.stages[0].diagnostic_name(1) == "credentials"


def test_discriminated_union_round_trip_preserves_concrete_fields() -> None:
    registry = _registry()
    parsed = registry.validate_config(
        _config(
            action="replace",
            replacement={"strategy": "template", "template": "[{entity}]"},
        )
    )
    serialized = parsed.model_dump(mode="json")
    reparsed = registry.validate_config(serialized)

    assert type(reparsed.entity_processing.stages[0].config) is RegexEngineConfig
    assert reparsed == parsed
    assert serialized["entity_processing"]["stages"][0]["config"]["engine"] == "regex"
    assert (
        serialized["entity_processing"]["stages"][0]["config"]["replacement"][
            "strategy"
        ]
        == "template"
    )


def test_generated_schema_declares_the_engine_discriminator() -> None:
    schema = _registry().configuration_json_schema()
    definitions = _required_dict(schema, "$defs")
    stage_definition = next(
        definition
        for name, definition in definitions.items()
        if isinstance(name, str) and name.startswith("EntityProcessingStage")
    )
    properties = _required_dict(stage_definition, "properties")
    config_schema = _required_dict(properties, "config")

    assert config_schema["discriminator"] == {
        "mapping": {"regex": "#/$defs/RegexEngineConfig"},
        "propertyName": "engine",
    }


def _required_dict(mapping: object, key: str):
    assert isinstance(mapping, dict)
    value = mapping.get(key)
    assert isinstance(value, dict)
    return value


def test_catalog_file_and_inline_catalog_produce_the_same_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _registry()
    inline_values = _config()
    file_values = deepcopy(inline_values)
    inline_catalog = file_values["entity_processing"]["stages"][0]["config"][
        "pattern_catalog"
    ]
    (tmp_path / "patterns.yaml").write_text(
        yaml.safe_dump(inline_catalog),
        encoding="utf-8",
    )
    file_values["entity_processing"]["stages"][0]["config"]["pattern_catalog"] = (
        "patterns.yaml"
    )
    monkeypatch.chdir(tmp_path)

    inline_config = registry.validate_config(inline_values)
    file_config = registry.validate_config(file_values)

    assert file_config == inline_config
    assert configuration_fingerprint_and_size(
        file_config
    ) == configuration_fingerprint_and_size(inline_config)
    serialized_catalog = file_config.model_dump(mode="json")["entity_processing"][
        "stages"
    ][0]["config"]["pattern_catalog"]
    inline_serialized_catalog = inline_config.model_dump(mode="json")[
        "entity_processing"
    ]["stages"][0]["config"]["pattern_catalog"]
    assert serialized_catalog == inline_serialized_catalog
    assert isinstance(serialized_catalog, dict)


def test_catalog_file_change_produces_a_new_fingerprint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog_path = tmp_path / "patterns.yaml"
    values = _config()
    catalog = values["entity_processing"]["stages"][0]["config"]["pattern_catalog"]
    catalog_path.write_text(yaml.safe_dump(catalog), encoding="utf-8")
    values["entity_processing"]["stages"][0]["config"]["pattern_catalog"] = (
        "patterns.yaml"
    )
    monkeypatch.chdir(tmp_path)
    registry = _registry()
    first = registry.validate_config(values)

    catalog["entities"][0]["patterns"][0]["confidence"] = "low"
    catalog_path.write_text(yaml.safe_dump(catalog), encoding="utf-8")
    second = registry.validate_config(values)

    assert configuration_fingerprint_and_size(
        first
    ) != configuration_fingerprint_and_size(second)


def test_parsed_catalog_cache_evicts_least_recently_used_file_by_weight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    regex_module._clear_parsed_pattern_catalog_cache()
    monkeypatch.chdir(tmp_path)
    paths = tuple(Path(f"sensitive-{suffix}.yaml") for suffix in "abc")
    sizes: list[int] = []
    for suffix, path in zip("abc", paths, strict=True):
        catalog = _config()["entity_processing"]["stages"][0]["config"][
            "pattern_catalog"
        ]
        catalog["entities"][0]["patterns"][0]["pattern"] = f"pattern-{suffix}"
        contents = yaml.safe_dump(catalog)
        path.write_text(contents, encoding="utf-8")
        sizes.append(len(contents.encode("utf-8")))
    assert len(set(sizes)) == 1
    monkeypatch.setattr(
        regex_module,
        "MAX_REGEX_PARSED_CATALOG_CACHE_BYTES",
        sizes[0] * 2,
    )

    try:
        with caplog.at_level(logging.DEBUG, logger="privacy_guard.engines.regex"):
            first = regex_module._load_pattern_catalog_file(str(paths[0]))
            regex_module._load_pattern_catalog_file(str(paths[1]))
            assert regex_module._load_pattern_catalog_file(str(paths[0])) is first
            regex_module._load_pattern_catalog_file(str(paths[2]))

        assert tuple(
            cache_key[0] for cache_key in regex_module._PATTERN_CATALOG_CACHE
        ) == (str(paths[0]), str(paths[2]))
        assert regex_module._PATTERN_CATALOG_CACHE_WEIGHT_BYTES == sizes[0] * 2
        assert regex_module._PATTERN_CATALOG_CACHE_WEIGHT_BYTES == sum(
            entry[1] for entry in regex_module._PATTERN_CATALOG_CACHE.values()
        )
        assert (
            "privacy_guard_cache_eviction cache=regex_parsed_catalog entries=1"
            in caplog.text
        )
        assert "sensitive-" not in caplog.text
    finally:
        regex_module._clear_parsed_pattern_catalog_cache()


def test_parsed_catalog_cache_skips_oversized_valid_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    regex_module._clear_parsed_pattern_catalog_cache()
    monkeypatch.chdir(tmp_path)
    catalog = _config()["entity_processing"]["stages"][0]["config"]["pattern_catalog"]
    catalog["entities"][0]["patterns"][0]["pattern"] = "sensitive-pattern"
    contents = yaml.safe_dump(catalog)
    path = Path("sensitive-oversized.yaml")
    path.write_text(contents, encoding="utf-8")
    size = len(contents.encode("utf-8"))
    monkeypatch.setattr(
        regex_module,
        "MAX_REGEX_PARSED_CATALOG_CACHE_BYTES",
        size - 1,
    )

    try:
        with caplog.at_level(logging.DEBUG, logger="privacy_guard.engines.regex"):
            first = regex_module._load_pattern_catalog_file(str(path))
            second = regex_module._load_pattern_catalog_file(str(path))

        assert first is not second
        assert regex_module._PATTERN_CATALOG_CACHE == {}
        assert regex_module._PATTERN_CATALOG_CACHE_WEIGHT_BYTES == 0
        assert (
            caplog.text.count("privacy_guard_cache_skip cache=regex_parsed_catalog")
            == 2
        )
        assert "sensitive-oversized" not in caplog.text
        assert "sensitive-pattern" not in caplog.text
    finally:
        regex_module._clear_parsed_pattern_catalog_cache()


def test_parsed_catalog_failure_preserves_existing_weight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    regex_module._clear_parsed_pattern_catalog_cache()
    monkeypatch.chdir(tmp_path)
    valid_catalog = _config()["entity_processing"]["stages"][0]["config"][
        "pattern_catalog"
    ]
    Path("valid.yaml").write_text(
        yaml.safe_dump(valid_catalog),
        encoding="utf-8",
    )
    Path("invalid.yaml").write_text("entities: []\n", encoding="utf-8")

    try:
        regex_module._load_pattern_catalog_file("valid.yaml")
        retained_keys = tuple(regex_module._PATTERN_CATALOG_CACHE)
        retained_weight = regex_module._PATTERN_CATALOG_CACHE_WEIGHT_BYTES

        with pytest.raises(ValueError, match="catalog file is invalid"):
            regex_module._load_pattern_catalog_file("invalid.yaml")

        assert tuple(regex_module._PATTERN_CATALOG_CACHE) == retained_keys
        assert regex_module._PATTERN_CATALOG_CACHE_WEIGHT_BYTES == retained_weight
    finally:
        regex_module._clear_parsed_pattern_catalog_cache()


def test_equivalent_catalogs_reuse_compiled_regex_rules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    regex_module._clear_compiled_pattern_cache()
    original_compile_rule = regex_module._compile_rule
    compile_calls = 0

    def record_compile_rule(
        entity: regex_module.RegexEntity,
        pattern: regex_module.RegexPattern,
        catalog_index: int,
        entity_pattern_index: int,
    ) -> regex_module._CompiledRule:
        nonlocal compile_calls
        compile_calls += 1
        return original_compile_rule(
            entity,
            pattern,
            catalog_index,
            entity_pattern_index,
        )

    monkeypatch.setattr(regex_module, "_compile_rule", record_compile_rule)
    registry = _registry()
    first = registry.validate_config(_config())
    registry.create_engine(first.entity_processing.stages[0].config)
    second = registry.validate_config(deepcopy(_config()))
    registry.create_engine(second.entity_processing.stages[0].config)
    changed_values = deepcopy(_config())
    changed_values["entity_processing"]["stages"][0]["config"]["pattern_catalog"][
        "entities"
    ][0]["patterns"][0]["pattern"] = "changed"
    changed = registry.validate_config(changed_values)
    registry.create_engine(changed.entity_processing.stages[0].config)

    assert compile_calls == 2
    regex_module._clear_compiled_pattern_cache()


@pytest.mark.parametrize(
    "catalog_path",
    [
        "missing.yaml",
        "../patterns.yaml",
        "patterns.json",
    ],
)
def test_catalog_file_rejects_invalid_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    catalog_path: str,
) -> None:
    values = _config()
    values["entity_processing"]["stages"][0]["config"]["pattern_catalog"] = catalog_path
    monkeypatch.chdir(tmp_path)

    with pytest.raises(PrivacyGuardError) as exception_info:
        _registry().validate_config(values)

    assert exception_info.value.code is ErrorCode.CONFIG_INVALID


def test_catalog_file_rejects_absolute_paths(
    tmp_path: Path,
) -> None:
    values = _config()
    values["entity_processing"]["stages"][0]["config"]["pattern_catalog"] = str(
        tmp_path / "patterns.yaml"
    )

    with pytest.raises(PrivacyGuardError) as exception_info:
        _registry().validate_config(values)

    assert exception_info.value.code is ErrorCode.CONFIG_INVALID


def test_catalog_file_rejects_symlinks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target.yaml"
    target.write_text("entities: []\n", encoding="utf-8")
    (tmp_path / "patterns.yaml").symlink_to(target)
    values = _config()
    values["entity_processing"]["stages"][0]["config"]["pattern_catalog"] = (
        "patterns.yaml"
    )
    monkeypatch.chdir(tmp_path)

    with pytest.raises(PrivacyGuardError) as exception_info:
        _registry().validate_config(values)

    assert exception_info.value.code is ErrorCode.CONFIG_INVALID


def test_catalog_file_rejects_a_symlink_swap_during_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog_root = tmp_path / "catalog-root"
    catalog_root.mkdir()
    catalog_path = catalog_root / "patterns.yaml"
    values = _config()
    catalog = values["entity_processing"]["stages"][0]["config"]["pattern_catalog"]
    catalog_path.write_text(yaml.safe_dump(catalog), encoding="utf-8")
    outside_path = tmp_path / "outside.yaml"
    outside_path.write_text(yaml.safe_dump(catalog), encoding="utf-8")
    values["entity_processing"]["stages"][0]["config"]["pattern_catalog"] = (
        "patterns.yaml"
    )
    monkeypatch.chdir(catalog_root)

    original_open = os.open
    swapped = False

    def swap_before_final_open(
        path: str | bytes | Path,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal swapped
        if path == "patterns.yaml" and dir_fd is not None and not swapped:
            swapped = True
            catalog_path.unlink()
            catalog_path.symlink_to(outside_path)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(regex_module.os, "open", swap_before_final_open)

    with pytest.raises(PrivacyGuardError) as exception_info:
        _registry().validate_config(values)

    assert swapped is True
    assert exception_info.value.code is ErrorCode.CONFIG_INVALID


def test_catalog_file_rejects_a_fifo_without_blocking(tmp_path: Path) -> None:
    os.mkfifo(tmp_path / "patterns.yaml")
    probe = """
from privacy_guard.engines.regex import _load_pattern_catalog_file

try:
    _load_pattern_catalog_file("patterns.yaml")
except ValueError:
    pass
else:
    raise AssertionError("FIFO catalog was accepted")
"""

    subprocess.run(
        [sys.executable, "-c", probe],
        cwd=tmp_path,
        check=True,
        timeout=5,
    )


@pytest.mark.parametrize(
    "contents",
    [
        "entities:\n  - name: first\n    name: duplicate\n    patterns: []\n",
        (
            "entities:\n"
            "  - &shared\n"
            "    name: first\n"
            "    patterns:\n"
            "      - pattern: x\n"
            "        confidence: high\n"
            "  - *shared\n"
        ),
        "entities: !!python/object/apply:builtins.list []\n",
    ],
)
def test_catalog_file_rejects_unsafe_yaml(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    contents: str,
) -> None:
    (tmp_path / "patterns.yaml").write_text(contents, encoding="utf-8")
    values = _config()
    values["entity_processing"]["stages"][0]["config"]["pattern_catalog"] = (
        "patterns.yaml"
    )
    monkeypatch.chdir(tmp_path)

    with pytest.raises(PrivacyGuardError) as exception_info:
        _registry().validate_config(values)

    assert exception_info.value.code is ErrorCode.CONFIG_INVALID


def test_catalog_file_rejects_invalid_utf8(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "patterns.yaml").write_bytes(b"\xff")
    values = _config()
    values["entity_processing"]["stages"][0]["config"]["pattern_catalog"] = (
        "patterns.yaml"
    )
    monkeypatch.chdir(tmp_path)

    with pytest.raises(PrivacyGuardError) as exception_info:
        _registry().validate_config(values)

    assert exception_info.value.code is ErrorCode.CONFIG_INVALID


def test_catalog_file_rejects_oversized_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(regex_module, "MAX_REGEX_CATALOG_FILE_BYTES", 1)
    (tmp_path / "patterns.yaml").write_text("entities: []\n", encoding="utf-8")
    values = _config()
    values["entity_processing"]["stages"][0]["config"]["pattern_catalog"] = (
        "patterns.yaml"
    )
    monkeypatch.chdir(tmp_path)

    with pytest.raises(PrivacyGuardError) as exception_info:
        _registry().validate_config(values)

    assert exception_info.value.code is ErrorCode.CONFIG_INVALID


def test_replace_requires_a_replacement_recipe_on_every_stage() -> None:
    with pytest.raises(PrivacyGuardError) as exception_info:
        _registry().validate_config(_config(action="replace"))

    assert exception_info.value.code is ErrorCode.CONFIG_INVALID


@pytest.mark.parametrize("action", ["detect", "block"])
def test_dormant_replacement_recipe_is_valid_for_detection_only_actions(
    action: str,
) -> None:
    config = _registry().validate_config(
        _config(
            action=action,
            replacement={"strategy": "template", "template": "[redacted]"},
        )
    )
    engine_config = config.entity_processing.stages[0].config

    assert isinstance(engine_config, RegexEngineConfig)
    assert engine_config.replacement is not None


@pytest.mark.parametrize(
    "mutation",
    [
        lambda values: values.update({"body_format": "json"}),
        lambda values: values.update({"on_finding": {"action": "observe"}}),
        lambda values: values["on_detection"].update({"action": "observe"}),
        lambda values: values["on_detection"].update({"action": "redact"}),
        lambda values: values["entity_processing"]["stages"][0]["config"].update(
            {"kind": "regex"}
        ),
        lambda values: values["entity_processing"]["stages"][0]["config"].update(
            {"preset": "pii"}
        ),
    ],
)
def test_removed_or_unknown_policy_fields_are_rejected(
    mutation: Callable[[dict[str, object]], None],
) -> None:
    values = _config()
    mutation(values)

    with pytest.raises(PrivacyGuardError):
        _registry().validate_config(values)


def test_stage_list_is_non_empty_and_explicit_names_are_unique() -> None:
    empty = _config()
    empty["entity_processing"]["stages"] = []
    duplicate = _config(stage_name="same")
    duplicate["entity_processing"]["stages"].append(
        deepcopy(duplicate["entity_processing"]["stages"][0])
    )

    with pytest.raises(PrivacyGuardError):
        _registry().validate_config(empty)
    with pytest.raises(PrivacyGuardError):
        _registry().validate_config(duplicate)


def test_explicit_stage_name_cannot_collide_with_a_derived_name() -> None:
    values = _config(stage_name="regex[2]")
    values["entity_processing"]["stages"].append(
        deepcopy(_config()["entity_processing"]["stages"][0])
    )

    with pytest.raises(PrivacyGuardError):
        _registry().validate_config(values)


def test_regex_pattern_names_are_optional_but_supplied_names_are_unique() -> None:
    values = _config()
    patterns = values["entity_processing"]["stages"][0]["config"]["pattern_catalog"][
        "entities"
    ][0]["patterns"]
    patterns.extend(
        [
            {"pattern": "second", "confidence": "low"},
            {"name": "named", "pattern": "third", "confidence": "medium"},
        ]
    )
    config = _registry().validate_config(values)

    regex_config = config.entity_processing.stages[0].config
    assert isinstance(regex_config, RegexEngineConfig)
    parsed_patterns = regex_config.pattern_catalog.entities[0].patterns
    assert [pattern.name for pattern in parsed_patterns] == [None, None, "named"]

    patterns.append({"name": "named", "pattern": "duplicate", "confidence": "high"})
    with pytest.raises(PrivacyGuardError):
        _registry().validate_config(values)


def test_canonical_fingerprint_covers_concrete_expanded_config() -> None:
    registry = _registry()
    first = registry.validate_config(_config())
    equivalent = registry.validate_config(deepcopy(_config()))
    changed_values = _config()
    changed_values["entity_processing"]["stages"][0]["config"]["pattern_catalog"][
        "entities"
    ][0]["patterns"][0]["confidence"] = "low"
    changed = registry.validate_config(changed_values)

    assert configuration_fingerprint_and_size(
        first
    ) == configuration_fingerprint_and_size(equivalent)
    assert configuration_fingerprint_and_size(
        first
    ) != configuration_fingerprint_and_size(changed)


def test_models_are_frozen_and_hide_engine_configuration_from_repr() -> None:
    config = _registry().validate_config(_config())
    pattern = "sensitive-pattern-value"

    with pytest.raises(ValidationError):
        setattr(config.on_detection, "action", PolicyAction.BLOCK)
    assert pattern not in repr(config)
