# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
from importlib.resources import files
from pathlib import Path

import pytest

from openshell_agent_runner.config import load_profile
from openshell_agent_runner.errors import ConfigurationError
from openshell_agent_runner.profile_init import (
    PACKAGED_PROFILES,
    ThinkingLevel,
    initialize_profiles,
)


def test_packaged_profile_catalog_lists_every_profile_resource() -> None:
    resources = files("openshell_agent_runner.profiles")
    profile_names = tuple(
        sorted(
            item.name
            for item in resources.iterdir()
            if item.is_dir() and not item.name.startswith("_")
        )
    )

    assert PACKAGED_PROFILES == profile_names


def test_omitting_profile_initializes_every_packaged_profile(tmp_path: Path) -> None:
    destination = tmp_path / "profiles"

    created = initialize_profiles(
        destination,
        (),
        "provider/model",
        ThinkingLevel.MEDIUM,
    )

    assert tuple(path.name for path in created) == PACKAGED_PROFILES
    for profile_name in PACKAGED_PROFILES:
        profile = destination / profile_name
        resolved = load_profile(profile)
        assert resolved.runtime.model == "provider/model"
        assert resolved.runtime.thinking == "medium"
        assert (profile / "schemas/review.json").is_file()
        models = json.loads((profile / "models.json").read_text())
        model = models["providers"]["openshell"]["models"][0]
        assert model == {"id": "provider/model", "reasoning": True}


def test_selected_profile_is_initialized(tmp_path: Path) -> None:
    destination = tmp_path / "profiles"

    created = initialize_profiles(
        destination,
        ("code-reviewer",),
        "provider/model",
        ThinkingLevel.HIGH,
    )

    assert created == (destination / "code-reviewer",)


def test_thinking_off_disables_model_reasoning(tmp_path: Path) -> None:
    destination = tmp_path / "profiles"

    initialize_profiles(
        destination,
        ("technical-writing-reviewer",),
        "provider/model",
        ThinkingLevel.OFF,
    )

    models = json.loads(
        (destination / "technical-writing-reviewer/models.json").read_text()
    )
    assert models["providers"]["openshell"]["models"][0]["reasoning"] is False


@pytest.mark.parametrize(
    ("profiles", "model", "message"),
    [
        (("missing",), "provider/model", "unknown packaged profile"),
        (("code-reviewer", "code-reviewer"), "provider/model", "must be unique"),
        (("code-reviewer",), "bad model", "valid model identifier"),
    ],
)
def test_invalid_initialization_is_rejected_without_creating_profiles(
    tmp_path: Path, profiles: tuple[str, ...], model: str, message: str
) -> None:
    destination = tmp_path / "profiles"

    with pytest.raises(ConfigurationError, match=message):
        initialize_profiles(destination, profiles, model, ThinkingLevel.HIGH)

    assert not destination.exists()


def test_existing_profile_is_not_modified(tmp_path: Path) -> None:
    destination = tmp_path / "profiles"
    existing = destination / "code-reviewer"
    existing.mkdir(parents=True)
    marker = existing / "keep.txt"
    marker.write_text("keep")

    with pytest.raises(ConfigurationError, match="already exists"):
        initialize_profiles(
            destination,
            ("code-reviewer",),
            "provider/model",
            ThinkingLevel.HIGH,
        )

    assert marker.read_text() == "keep"
