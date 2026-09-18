# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Resolve and run one configured task in an OpenShell sandbox."""

from __future__ import annotations

import json
import re
import secrets
import shlex
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import openshell_agent_runner.openshell as openshell
from openshell_agent_runner.artifacts import (
    MAX_ARTIFACT_BYTES,
    atomic_publish,
    validate_artifact,
)
from openshell_agent_runner.config import (
    ResolvedProfile,
    TaskConfig,
    resolve_task,
    validate_environment_assignments,
    validate_upload_mappings,
)
from openshell_agent_runner.errors import ConfigurationError, ExecutionError
from openshell_agent_runner.harnesses.pi.resources import (
    image_directory,
    prepare_resources,
)
from openshell_agent_runner.prompt_templates import PROMPT_VARIABLE_NAME_PATTERN


@dataclass(frozen=True)
class RunRequest:
    profile_directory: Path
    task_id: str
    output: Path
    input_path: Path | None = None
    prompt_variables: Sequence[str] = ()
    uploads: Sequence[str] = ()
    environments: Sequence[str] = ()
    gateway: str | None = None
    workspace: str = "default"
    timeout_seconds: int = 1200
    keep_sandbox: bool = False
    openshell_bin: str = "openshell"


@dataclass(frozen=True)
class ResolvedInput:
    source: Path
    sandbox_path: str
    name: str


@dataclass(frozen=True)
class ResolvedRun:
    request: RunRequest
    profile: ResolvedProfile
    input: ResolvedInput | None
    uploads: tuple[str, ...]
    environments: tuple[str, ...]
    prompt_variables: tuple[tuple[str, str], ...]
    create_command: tuple[str, ...]


def resolve_run(request: RunRequest) -> ResolvedRun:
    profile = resolve_task(request.profile_directory, request.task_id)
    task = profile.profile.tasks[request.task_id]
    resolved_input, input_upload, input_environment = _resolve_required_input(
        request, task.required_input
    )
    prompt_variables = _resolve_prompt_variables(request, task, resolved_input)
    uploads = _validate_uploads(
        [
            *profile.profile.sandbox.upload,
            *([input_upload] if input_upload else []),
            *request.uploads,
        ]
    )
    environments = _validate_environments(
        [
            *([input_environment] if input_environment else []),
            *profile.profile.sandbox.env,
            *request.environments,
        ]
    )
    sandbox = profile.profile.sandbox
    command = [request.openshell_bin, "sandbox", "create"]
    if request.gateway:
        command.extend(["--gateway", request.gateway])
    command.extend(
        [
            "--workspace",
            request.workspace,
            "--from",
            str(image_directory()),
            "--policy",
            str(profile.profile_dir / sandbox.policy),
        ]
    )
    for environment in environments:
        command.extend(["--env", environment])
    command.extend(["--no-auto-providers", "--no-tty", "--approval-mode", "auto"])
    return ResolvedRun(
        request=request,
        profile=profile,
        input=resolved_input,
        uploads=uploads,
        environments=environments,
        prompt_variables=prompt_variables,
        create_command=tuple(command),
    )


def render_dry_run(request: RunRequest) -> str:
    """Render the exact nominal command sequence without executing subprocesses."""
    resolved = resolve_run(request)
    name, token = _identity()
    resources = prepare_resources(
        resolved.profile,
        request.task_id,
        dict(resolved.prompt_variables),
    )
    try:
        with tempfile.TemporaryDirectory(prefix="oar-output-") as directory:
            downloaded = Path(directory) / "output.download"
            commands = [
                (
                    "create",
                    openshell.sandbox_create(resolved, name, token),
                ),
            ]
            commands.extend(
                ("upload", openshell.sandbox_upload(request, name, upload))
                for upload in (*resolved.uploads, *resources.uploads)
            )
            commands.extend(
                [
                    ("execute", openshell.sandbox_exec(resolved, resources, name)),
                    (
                        "download",
                        openshell.sandbox_download(resolved, name, downloaded),
                    ),
                ]
            )
            if not request.keep_sandbox:
                commands.extend(
                    [
                        (
                            "verify ownership",
                            openshell.sandbox_get(request, name),
                        ),
                        (
                            "delete",
                            openshell.sandbox_delete(request, name),
                        ),
                    ]
                )
            lines = [
                "Dry run: no commands were executed.",
                f"Profile: {resolved.profile.profile.id}",
                f"Task: {request.task_id}",
                f"Sandbox: {name}",
                "OpenShell commands:",
                *(f"[{label}] {shlex.join(command)}" for label, command in commands),
                "Host actions:",
                _validation_preview(resolved, downloaded),
                f"[publish] atomically replace {request.output}",
            ]
            if request.keep_sandbox:
                lines.append("[cleanup] skipped because --keep-sandbox is set")
            else:
                lines.append(
                    "[cleanup] ownership verification and deletion also run after "
                    "failures when the sandbox can be inspected"
                )
            return "\n".join(lines) + "\n"
    finally:
        resources.close()


def run_agent(request: RunRequest) -> str:
    resolved = resolve_run(request)
    name, token = _identity()
    resources = prepare_resources(
        resolved.profile,
        request.task_id,
        dict(resolved.prompt_variables),
    )
    create = openshell.sandbox_create(resolved, name, token)
    primary_error: BaseException | None = None
    try:
        openshell.run(create, request.timeout_seconds)
        for upload in (*resolved.uploads, *resources.uploads):
            openshell.run(openshell.sandbox_upload(request, name, upload), 120)
        openshell.run(
            openshell.sandbox_exec(resolved, resources, name),
            request.timeout_seconds,
        )
        task = resolved.profile.profile.tasks[request.task_id]
        with tempfile.TemporaryDirectory(prefix="oar-output-") as directory:
            downloaded = Path(directory) / "output.download"
            downloaded.touch(mode=0o600)
            openshell.run(
                openshell.sandbox_download(resolved, name, downloaded),
                120,
                max_file_bytes=MAX_ARTIFACT_BYTES,
            )
            schema_path = (
                resolved.profile.profile_dir / task.output_schema
                if task.output_schema is not None
                else None
            )
            validate_artifact(downloaded, schema_path)
            atomic_publish(downloaded, request.output)
        return name
    except BaseException as error:
        primary_error = error
        raise
    finally:
        resources.close()
        if request.keep_sandbox:
            print(f"oar: sandbox name (--keep-sandbox): {name}", file=sys.stderr)
        else:
            try:
                _verify_ownership(request, name, token)
                openshell.run(openshell.sandbox_delete(request, name), 60)
            except ExecutionError as cleanup_error:
                if primary_error is None:
                    raise
                print(
                    f"oar: cleanup failed after primary error: {cleanup_error}",
                    file=sys.stderr,
                )


def _validate_uploads(values: Sequence[str]) -> tuple[str, ...]:
    try:
        return validate_upload_mappings(values)
    except ValueError as error:
        raise ConfigurationError(str(error)) from error


def _validate_environments(values: Sequence[str]) -> tuple[str, ...]:
    try:
        return validate_environment_assignments(values)
    except ValueError as error:
        raise ConfigurationError(str(error)) from error


def _resolve_required_input(
    request: RunRequest, required_input: str | None
) -> tuple[ResolvedInput | None, str | None, str | None]:
    if required_input is None:
        if request.input_path is not None:
            raise ConfigurationError(
                f"task {request.task_id!r} does not accept --input"
            )
        return None, None, None
    input_label = required_input.upper()
    if request.input_path is None:
        raise ConfigurationError(
            f"task {request.task_id!r} requires --input {input_label}"
        )
    input_name = request.input_path.absolute().name
    try:
        input_path = request.input_path.resolve(strict=True)
    except OSError as error:
        raise ConfigurationError(
            f"input {required_input} does not exist: {request.input_path}"
        ) from error
    if required_input == "document":
        if not input_path.is_file():
            raise ConfigurationError(f"input document must be a file: {input_path}")
        suffix = input_path.suffix
        if not (
            1 < len(suffix) <= 17 and suffix.startswith(".") and suffix[1:].isalnum()
        ):
            suffix = ""
        sandbox_input = f"{_INPUT_DIRECTORY}/document{suffix}"
        resolved_input = ResolvedInput(
            source=input_path,
            sandbox_path=sandbox_input,
            name=input_name,
        )
        return (
            resolved_input,
            f"{input_path}:{sandbox_input}",
            _DOCUMENT_INPUT_ENVIRONMENT,
        )
    if not input_path.is_dir():
        raise ConfigurationError(f"input repository must be a directory: {input_path}")
    repository_root = f"{_INPUT_DIRECTORY}/{input_path.name}"
    resolved_input = ResolvedInput(
        source=input_path,
        sandbox_path=repository_root,
        name=input_name,
    )
    return (
        resolved_input,
        f"{input_path}:{_INPUT_DIRECTORY}",
        f"REPOSITORY_ROOT={repository_root}",
    )


def _resolve_prompt_variables(
    request: RunRequest,
    task: TaskConfig,
    resolved_input: ResolvedInput | None,
) -> tuple[tuple[str, str], ...]:
    supplied: dict[str, str] = {}
    for assignment in request.prompt_variables:
        name, separator, value = assignment.partition("=")
        if not separator or not value:
            raise ConfigurationError(
                "prompt variables must use non-empty NAME=VALUE syntax"
            )
        if name.startswith("oar."):
            raise ConfigurationError(
                f"prompt variable uses reserved oar namespace: {name!r}"
            )
        if not re.fullmatch(PROMPT_VARIABLE_NAME_PATTERN, name):
            raise ConfigurationError(f"invalid prompt variable name: {name!r}")
        if name in supplied:
            raise ConfigurationError(f"duplicate prompt variable: {name!r}")
        if name not in task.prompt_variables:
            raise ConfigurationError(f"undeclared prompt variable: {name!r}")
        supplied[name] = value

    values: dict[str, str] = {}
    missing: list[str] = []
    for name, config in task.prompt_variables.items():
        value = supplied.get(name, config.default)
        if value is None:
            missing.append(name)
        else:
            values[name] = value
    if missing:
        raise ConfigurationError(
            f"missing required prompt variables: {sorted(missing)}"
        )
    if resolved_input is not None:
        values.update(
            {
                "oar.input_name": resolved_input.name,
                "oar.input_path": resolved_input.sandbox_path,
            }
        )
    return tuple(values.items())


def _validation_preview(resolved: ResolvedRun, downloaded: Path) -> str:
    task = resolved.profile.profile.tasks[resolved.request.task_id]
    if task.output_schema is None:
        return f"[validate] {downloaded} is present, non-empty, and bounded"
    schema = resolved.profile.profile_dir / task.output_schema
    return f"[validate] {downloaded} as JSON against {schema}"


def _identity() -> tuple[str, str]:
    token = secrets.token_hex(8)[:15]
    return f"oar-{token}", token


def _verify_ownership(request: RunRequest, name: str, token: str) -> None:
    command = openshell.sandbox_get(request, name)
    result = openshell.run(command, 30, capture=True)
    try:
        document = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ExecutionError(
            f"cleanup ownership response was invalid for {name}"
        ) from error
    labels = document.get("labels") if isinstance(document, dict) else None
    owned = (
        isinstance(document, dict)
        and document.get("name") == name
        and isinstance(labels, dict)
        and labels.get(openshell.RESERVED_LABEL) == token
    )
    if not owned:
        raise ExecutionError(
            f"refusing to delete sandbox with mismatched ownership: {name}"
        )


_DOCUMENT_INPUT_ENVIRONMENT = "REPOSITORY_ROOT=/workspace/input"
_INPUT_DIRECTORY = "/workspace/input"
