# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Create editable profiles from resources packaged with OAR."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from collections.abc import Sequence
from enum import StrEnum
from importlib.resources import as_file, files
from pathlib import Path

from openshell_agent_runner.config import MODEL_IDENTIFIER_PATTERN, load_profile
from openshell_agent_runner.errors import ConfigurationError

PACKAGED_PROFILES = ("code-reviewer", "technical-writing-reviewer")


class ThinkingLevel(StrEnum):
    OFF = "off"
    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"


def initialize_profiles(
    destination: Path,
    profile_names: Sequence[str],
    model_id: str,
    thinking: ThinkingLevel,
) -> tuple[Path, ...]:
    """Create selected packaged profiles under destination."""
    if not re.fullmatch(MODEL_IDENTIFIER_PATTERN, model_id):
        raise ConfigurationError("--model must be a valid model identifier")

    selected = tuple(profile_names) or PACKAGED_PROFILES
    if len(selected) != len(set(selected)):
        raise ConfigurationError("--profile values must be unique")
    unknown = sorted(set(selected) - set(PACKAGED_PROFILES))
    if unknown:
        available = ", ".join(PACKAGED_PROFILES)
        raise ConfigurationError(
            f"unknown packaged profile {unknown[0]!r}; available profiles: {available}"
        )

    try:
        destination.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ConfigurationError(
            f"cannot create profile directory {destination}: {error}"
        ) from error
    if not destination.is_dir():
        raise ConfigurationError(
            f"profile destination is not a directory: {destination}"
        )

    targets = tuple(destination / name for name in selected)
    collisions = [path for path in targets if path.exists() or path.is_symlink()]
    if collisions:
        raise ConfigurationError(f"profile destination already exists: {collisions[0]}")

    try:
        with tempfile.TemporaryDirectory(
            prefix=".oar-init-", dir=destination
        ) as staging:
            staging_root = Path(staging)
            with as_file(files("openshell_agent_runner.profiles")) as source_root:
                staged_profiles = []
                for name in selected:
                    staged = staging_root / name
                    shutil.copytree(source_root / name, staged)
                    _configure_runtime(staged, model_id, thinking)
                    load_profile(staged)
                    staged_profiles.append(staged)
                for staged, target in zip(staged_profiles, targets, strict=True):
                    staged.rename(target)
    except OSError as error:
        raise ConfigurationError(f"cannot initialize profiles: {error}") from error

    return targets


def _configure_runtime(
    profile_directory: Path, model_id: str, thinking: ThinkingLevel
) -> None:
    models_path = profile_directory / "models.json"
    models = json.loads(models_path.read_text(encoding="utf-8"))
    model = models["providers"]["openshell"]["models"][0]
    model["id"] = model_id
    model["reasoning"] = thinking is not ThinkingLevel.OFF
    models_path.write_text(json.dumps(models, indent=2) + "\n", encoding="utf-8")

    settings_path = profile_directory / "settings.json"
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    settings["defaultModel"] = model_id
    settings["defaultThinkingLevel"] = thinking.value
    settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
