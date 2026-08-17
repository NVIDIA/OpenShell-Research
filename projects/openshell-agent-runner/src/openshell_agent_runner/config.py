# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Define, load, validate, and resolve agent profile configuration."""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal, Self

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from openshell_agent_runner.errors import ConfigurationError

IDENTIFIER_PATTERN = r"^[a-z][a-z0-9-]{0,62}$"
RESOURCE_IDENTIFIER_PATTERN = r"^[a-z][a-z0-9_-]{0,62}$"
MODEL_IDENTIFIER_PATTERN = r"^[A-Za-z0-9._:/-]{1,256}$"
MAX_ARTIFACT_BYTES = 10 * 1024 * 1024


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PiHarnessConfig(StrictModel):
    type: Literal["pi"]
    model: Annotated[str, Field(pattern=MODEL_IDENTIFIER_PATTERN)]
    context_window: int = Field(default=200_000, ge=1, le=2_000_000)
    max_tokens: int = Field(default=32_000, ge=1, le=256_000)

    @model_validator(mode="after")
    def validate_token_limit(self) -> Self:
        if self.max_tokens > self.context_window:
            raise ValueError("max_tokens must not exceed context_window")
        return self


class SandboxConfig(StrictModel):
    from_: str = Field(alias="from", min_length=1)
    policy: Path
    upload: list[str] = Field(default_factory=list)
    no_git_ignore: bool = False
    env: list[str] = Field(default_factory=list)
    approval_mode: Literal["manual", "auto"] = "auto"
    no_auto_providers: bool = False

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


class DocumentReviewContract(StrictModel):
    reviewer_id: Annotated[str, Field(pattern=RESOURCE_IDENTIFIER_PATTERN)]
    criteria: list[Annotated[str, Field(pattern=RESOURCE_IDENTIFIER_PATTERN)]] = Field(
        min_length=1, max_length=32
    )
    max_findings: int = Field(default=12, ge=0, le=100)

    @field_validator("criteria")
    @classmethod
    def require_unique_criteria(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("document-review criteria must be unique")
        return values


class OutputConfig(StrictModel):
    type: Literal["document_review"]
    contract: DocumentReviewContract
    sandbox_path: str
    max_bytes: int = Field(gt=0, le=MAX_ARTIFACT_BYTES)

    @field_validator("sandbox_path")
    @classmethod
    def validate_sandbox_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError("sandbox_path must be absolute and normalized")
        if path == PurePosixPath("/sandbox/artifacts") or not path.is_relative_to(
            "/sandbox/artifacts"
        ):
            raise ValueError("sandbox_path must be beneath /sandbox/artifacts")
        return str(path)


class TaskConfig(StrictModel):
    prompt: Path
    tools: list[Annotated[str, Field(pattern=RESOURCE_IDENTIFIER_PATTERN)]] = Field(
        default_factory=list
    )
    skills: list[Path] = Field(default_factory=list)
    extensions: list[Path] = Field(default_factory=list)
    output: OutputConfig

    @field_validator("tools", "skills", "extensions")
    @classmethod
    def require_unique_resources(cls, values: list[object]) -> list[object]:
        if len(values) != len(set(values)):
            raise ValueError("resource entries must be unique")
        return values


class ProfileConfig(StrictModel):
    id: Annotated[str, Field(pattern=IDENTIFIER_PATTERN)]
    description: str = Field(min_length=1, max_length=1000)
    harness: PiHarnessConfig
    sandbox: SandboxConfig
    tasks: dict[Annotated[str, Field(pattern=IDENTIFIER_PATTERN)], TaskConfig]

    @field_validator("tasks")
    @classmethod
    def require_tasks(cls, value: dict[str, TaskConfig]) -> dict[str, TaskConfig]:
        if not value:
            raise ValueError("at least one task is required")
        return value


class ResolvedProfile(StrictModel):
    profile_path: Path
    profile_dir: Path
    profile: ProfileConfig


def load_profile(path: Path) -> ResolvedProfile:
    try:
        profile_path = path.resolve(strict=True)
        profile = ProfileConfig.model_validate(_load_yaml(profile_path))
    except ValidationError as error:
        raise ConfigurationError(f"invalid profile {profile_path}: {error}") from error
    except OSError as error:
        raise ConfigurationError(f"missing profile: {path}") from error
    resolved = ResolvedProfile(
        profile_path=profile_path,
        profile_dir=profile_path.parent,
        profile=profile,
    )
    _validate_profile_resources(resolved)
    return resolved


def resolve_task(profile_path: Path, task_id: str) -> ResolvedProfile:
    resolved = load_profile(profile_path)
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
        if ".." in path.parts:
            raise ValueError("upload destinations must not contain '..'")
        if path == PurePosixPath("/sandbox/oar-runtime") or path.is_relative_to(
            "/sandbox/oar-runtime"
        ):
            raise ValueError(
                f"upload destination is reserved for runner resources: {destination}"
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
