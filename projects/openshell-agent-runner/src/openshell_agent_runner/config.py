# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Define, load, validate, and resolve agent profile configuration."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal

import yaml
from jsonschema import Draft202012Validator, SchemaError
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)

from openshell_agent_runner.errors import ConfigurationError

IDENTIFIER_PATTERN = r"^[a-z][a-z0-9-]{0,62}$"
RESOURCE_IDENTIFIER_PATTERN = r"^[a-z][a-z0-9_-]{0,62}$"
MODEL_IDENTIFIER_PATTERN = r"^[A-Za-z0-9._:/-]{1,256}$"
MODELS_FILENAME = "models.json"
PROFILE_FILENAME = "profile.yaml"
SETTINGS_FILENAME = "settings.json"
_PI_RUNTIME_SETTING_KEYS = {
    "defaultProvider",
    "defaultModel",
    "defaultThinkingLevel",
}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SandboxConfig(StrictModel):
    policy: Path
    upload: list[str] = Field(default_factory=list)
    env: list[str] = Field(default_factory=list)

    @field_validator("upload")
    @classmethod
    def validate_uploads(cls, values: list[str]) -> list[str]:
        validate_upload_mappings(values)
        return values

    @field_validator("env")
    @classmethod
    def validate_environment(cls, values: list[str]) -> list[str]:
        validate_environment_assignments(values)
        return values


class TaskConfig(StrictModel):
    description: str | None = Field(default=None, min_length=1, max_length=1000)
    required_input: Literal["document"] | None = None
    prompt: Path
    output_schema: Path | None = None
    tools: list[Annotated[str, Field(pattern=RESOURCE_IDENTIFIER_PATTERN)]] = Field(
        default_factory=list
    )
    skills: list[Path] = Field(default_factory=list)
    extensions: list[Path] = Field(default_factory=list)

    @field_validator("tools", "skills", "extensions")
    @classmethod
    def require_unique_resources(cls, values: list[object]) -> list[object]:
        if len(values) != len(set(values)):
            raise ValueError("resource entries must be unique")
        return values


class ProfileConfig(StrictModel):
    id: Annotated[str, Field(pattern=IDENTIFIER_PATTERN)]
    description: str = Field(min_length=1, max_length=1000)
    sandbox: SandboxConfig
    tasks: dict[Annotated[str, Field(pattern=IDENTIFIER_PATTERN)], TaskConfig]

    @field_validator("tasks")
    @classmethod
    def require_tasks(cls, value: dict[str, TaskConfig]) -> dict[str, TaskConfig]:
        if not value:
            raise ValueError("at least one task is required")
        return value


class PiRuntimeSettings(StrictModel):
    provider: Literal["openshell"]
    model: Annotated[str, Field(pattern=MODEL_IDENTIFIER_PATTERN)]
    thinking: Literal["off", "minimal", "low", "medium", "high", "xhigh", "max"]


class ResolvedProfile(StrictModel):
    profile_path: Path
    profile_dir: Path
    profile: ProfileConfig
    runtime: PiRuntimeSettings


def load_profile(directory: Path) -> ResolvedProfile:
    try:
        profile_dir = directory.resolve(strict=True)
    except OSError as error:
        raise ConfigurationError(f"missing profile directory: {directory}") from error
    if not profile_dir.is_dir():
        raise ConfigurationError(
            f"profile must be a directory containing {PROFILE_FILENAME}: {directory}"
        )
    candidate = profile_dir / PROFILE_FILENAME
    try:
        profile_path = candidate.resolve(strict=True)
    except OSError as error:
        raise ConfigurationError(
            f"missing profile configuration: {candidate}"
        ) from error
    if not profile_path.is_relative_to(profile_dir) or not profile_path.is_file():
        raise ConfigurationError(
            f"profile configuration must be a file inside {profile_dir}: {candidate}"
        )
    try:
        profile = ProfileConfig.model_validate(_load_yaml(profile_path))
    except ValidationError as error:
        raise ConfigurationError(f"invalid profile {profile_path}: {error}") from error
    model_path = _inside(profile_dir, profile_dir / MODELS_FILENAME, "Pi models file")
    settings_path = _inside(
        profile_dir, profile_dir / SETTINGS_FILENAME, "Pi settings file"
    )
    model_id = _load_pi_model_id(model_path)
    resolved = ResolvedProfile(
        profile_path=profile_path,
        profile_dir=profile_dir,
        profile=profile,
        runtime=_load_pi_runtime_settings(settings_path, model_id),
    )
    _validate_profile_resources(resolved)
    return resolved


def resolve_task(profile_directory: Path, task_id: str) -> ResolvedProfile:
    resolved = load_profile(profile_directory)
    if task_id not in resolved.profile.tasks:
        raise ConfigurationError(
            f"unknown task {task_id!r} for profile {resolved.profile.id!r}"
        )
    return resolved


def validate_upload_mappings(values: Sequence[str]) -> tuple[str, ...]:
    if len(values) != len(set(values)):
        raise ValueError("duplicate upload mapping")
    destinations: dict[str, str] = {}
    for value in values:
        source, separator, destination = value.rpartition(":")
        if not separator or not source or not destination.startswith("/"):
            raise ValueError("uploads must use SOURCE:/ABSOLUTE/DESTINATION")
        path = PurePosixPath(destination)
        if destination.startswith("//") or str(path) != destination:
            raise ValueError("upload destinations must use canonical absolute paths")
        if ".." in path.parts:
            raise ValueError("upload destinations must not contain '..'")
        for reserved in (
            PurePosixPath("/sandbox/artifacts"),
            PurePosixPath("/sandbox/oar-runtime"),
        ):
            if path == reserved or path.is_relative_to(reserved):
                raise ValueError(
                    "upload destination is reserved for runner resources: "
                    f"{destination}"
                )
        normalized = str(path)
        previous = destinations.get(normalized)
        if previous is not None and previous != source:
            raise ValueError(f"conflicting upload destination: {destination}")
        destinations[normalized] = source
    return tuple(values)


def validate_environment_assignments(values: Sequence[str]) -> tuple[str, ...]:
    if len(values) != len(set(values)):
        raise ValueError("duplicate environment assignment")
    assignments: dict[str, str] = {}
    for value in values:
        key, separator, assigned = value.partition("=")
        if (
            not separator
            or not assigned
            or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", key)
        ):
            raise ValueError("environment must use non-empty KEY=VALUE syntax")
        previous = assignments.get(key)
        if previous is not None and previous != assigned:
            raise ValueError(f"conflicting environment values for key {key!r}")
        assignments[key] = assigned
    return tuple(values)


def _load_yaml(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8") as stream:
            return yaml.safe_load(stream)
    except (OSError, UnicodeError) as error:
        raise ConfigurationError(
            f"cannot read configuration {path}: {error}"
        ) from error
    except yaml.YAMLError as error:
        raise ConfigurationError(f"invalid YAML in {path}: {error}") from error


def _load_pi_model_id(path: Path) -> str:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ConfigurationError(f"invalid Pi models file {path}: {error}") from error
    providers = document.get("providers") if isinstance(document, dict) else None
    if not isinstance(providers, dict) or set(providers) != {"openshell"}:
        raise ConfigurationError(
            "Pi models file must contain exactly one provider named 'openshell'"
        )
    provider = providers["openshell"]
    models = provider.get("models") if isinstance(provider, dict) else None
    if not isinstance(models, list) or len(models) != 1:
        raise ConfigurationError(
            "Pi models file must contain exactly one model under 'openshell'"
        )
    model = models[0]
    model_id = model.get("id") if isinstance(model, dict) else None
    if not isinstance(model_id, str) or not re.fullmatch(
        MODEL_IDENTIFIER_PATTERN, model_id
    ):
        raise ConfigurationError("Pi model must have a valid string id")
    return model_id


def _load_pi_runtime_settings(path: Path, model_id: str) -> PiRuntimeSettings:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ConfigurationError(f"invalid Pi settings file {path}: {error}") from error
    if not isinstance(document, dict):
        raise ConfigurationError(f"Pi settings file must contain an object: {path}")
    unexpected = set(document) - _PI_RUNTIME_SETTING_KEYS
    missing = _PI_RUNTIME_SETTING_KEYS - set(document)
    if unexpected or missing:
        diagnostics = []
        if missing:
            diagnostics.append(f"missing {sorted(missing)}")
        if unexpected:
            diagnostics.append(f"unexpected {sorted(unexpected)}")
        raise ConfigurationError(
            f"Pi settings file must contain only runtime selection keys: "
            f"{', '.join(diagnostics)}"
        )
    try:
        runtime = PiRuntimeSettings.model_validate(
            {
                "provider": document.get("defaultProvider"),
                "model": document.get("defaultModel"),
                "thinking": document.get("defaultThinkingLevel"),
            }
        )
    except ValidationError as error:
        raise ConfigurationError(
            f"invalid Pi runtime settings in {path}: {error}"
        ) from error
    if runtime.model != model_id:
        raise ConfigurationError(
            "Pi settings defaultModel must identify the model in models.json"
        )
    return runtime


def _inside(
    owner: Path, candidate: Path, description: str, *, directory: bool = False
) -> Path:
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise ConfigurationError(f"missing {description}: {candidate}") from error
    owner_resolved = owner.resolve(strict=True)
    if not resolved.is_relative_to(owner_resolved):
        raise ConfigurationError(f"{description} escapes {owner_resolved}: {candidate}")
    expected = "directory" if directory else "file"
    if (directory and not resolved.is_dir()) or (
        not directory and not resolved.is_file()
    ):
        raise ConfigurationError(f"{description} must be a {expected}: {candidate}")
    return resolved


def _validate_profile_resources(resolved: ResolvedProfile) -> None:
    directory = resolved.profile_dir
    _inside(directory, directory / resolved.profile.sandbox.policy, "sandbox policy")
    for task_id, task in resolved.profile.tasks.items():
        _inside(directory, directory / task.prompt, f"prompt for task {task_id}")
        if task.output_schema is not None:
            schema = _inside(
                directory,
                directory / task.output_schema,
                f"output schema for task {task_id}",
            )
            _validate_output_schema(schema)
        for skill in task.skills:
            skill_directory = _inside(
                directory,
                directory / skill,
                f"skill for task {task_id}",
                directory=True,
            )
            _inside(
                skill_directory,
                skill_directory / "SKILL.md",
                f"SKILL.md for task {task_id}",
            )
            for descendant in skill_directory.rglob("*"):
                if descendant.is_symlink():
                    raise ConfigurationError(
                        f"skill for task {task_id} contains a symlink: {descendant}"
                    )
        for extension in task.extensions:
            _inside(directory, directory / extension, f"extension for task {task_id}")


def _validate_output_schema(path: Path) -> None:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(document)
    except (OSError, UnicodeError, json.JSONDecodeError, SchemaError) as error:
        raise ConfigurationError(f"invalid output schema {path}: {error}") from error
    _validate_schema_references(document, path)


def _validate_schema_references(document: Any, path: Path) -> None:
    if isinstance(document, dict):
        for key, value in document.items():
            if key in {"$ref", "$dynamicRef", "$recursiveRef"} and (
                not isinstance(value, str) or not value.startswith("#")
            ):
                raise ConfigurationError(
                    f"output schema references must stay inside {path}: {value!r}"
                )
            _validate_schema_references(value, path)
    elif isinstance(document, list):
        for value in document:
            _validate_schema_references(value, path)
