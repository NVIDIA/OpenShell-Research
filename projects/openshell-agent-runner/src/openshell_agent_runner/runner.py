# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Resolve and run one configured task in an OpenShell sandbox."""

from __future__ import annotations

import json
import secrets
import shlex
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import openshell_agent_runner.commands as openshell_commands
from openshell_agent_runner.artifacts import atomic_publish, validate_artifact
from openshell_agent_runner.config import (
    ResolvedProfile,
    resolve_task,
    validate_environment_assignments,
    validate_upload_mappings,
)
from openshell_agent_runner.errors import ConfigurationError, ExecutionError
from openshell_agent_runner.harnesses.pi.resources import prepare_resources


@dataclass(frozen=True)
class RunRequest:
    profile_directory: Path
    task_id: str
    output: Path
    uploads: Sequence[str] = ()
    environments: Sequence[str] = ()
    gateway: str | None = None
    workspace: str = "default"
    timeout_seconds: int = 1200
    keep_sandbox: bool = False
    openshell_bin: str = "openshell"


@dataclass(frozen=True)
class ResolvedRun:
    request: RunRequest
    profile: ResolvedProfile
    model: str
    uploads: tuple[str, ...]
    environments: tuple[str, ...]
    create_command: tuple[str, ...]


def resolve_run(request: RunRequest) -> ResolvedRun:
    profile = resolve_task(request.profile_directory, request.task_id)
    model = profile.profile.harness.model
    uploads = _validate_uploads([*profile.profile.sandbox.upload, *request.uploads])
    environments = _validate_environments(
        [*profile.profile.sandbox.env, *request.environments]
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
            sandbox.from_,
            "--policy",
            str(profile.profile_dir / sandbox.policy),
        ]
    )
    for upload in uploads:
        command.extend(["--upload", upload])
    for environment in environments:
        command.extend(["--env", environment])
    if sandbox.no_git_ignore:
        command.append("--no-git-ignore")
    if sandbox.no_auto_providers:
        command.append("--no-auto-providers")
    command.extend(["--no-tty", "--approval-mode", sandbox.approval_mode])
    return ResolvedRun(
        request=request,
        profile=profile,
        model=model,
        uploads=uploads,
        environments=environments,
        create_command=tuple(command),
    )


def render_dry_run(request: RunRequest) -> str:
    """Render the exact nominal command sequence without executing subprocesses."""
    resolved = resolve_run(request)
    name, token = _identity()
    resources = prepare_resources(resolved.profile, request.task_id, resolved.model)
    try:
        with tempfile.TemporaryDirectory(prefix="oar-output-") as directory:
            downloaded = Path(directory) / "output.download"
            commands = [
                (
                    "create",
                    openshell_commands.create_command(resolved, resources, name, token),
                ),
                (
                    "download",
                    openshell_commands.download_command(resolved, name, downloaded),
                ),
            ]
            if not request.keep_sandbox:
                commands.extend(
                    [
                        (
                            "verify ownership",
                            openshell_commands.get_command(request, name),
                        ),
                        ("delete", openshell_commands.delete_command(request, name)),
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
                (
                    f"[validate] {downloaded} as "
                    f"{resolved.profile.profile.tasks[request.task_id].output.type}"
                ),
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
    resources = prepare_resources(resolved.profile, request.task_id, resolved.model)
    create = openshell_commands.create_command(resolved, resources, name, token)
    primary_error: BaseException | None = None
    try:
        openshell_commands.run_command(create, request.timeout_seconds)
        output = resolved.profile.profile.tasks[request.task_id].output
        with tempfile.TemporaryDirectory(prefix="oar-output-") as directory:
            downloaded = Path(directory) / "output.download"
            openshell_commands.run_command(
                openshell_commands.download_command(resolved, name, downloaded), 120
            )
            validate_artifact(downloaded, output, resolved.model)
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
                openshell_commands.run_command(
                    openshell_commands.delete_command(request, name), 60
                )
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


def _identity() -> tuple[str, str]:
    token = secrets.token_hex(8)[:15]
    return f"oar-{token}", token


def _verify_ownership(request: RunRequest, name: str, token: str) -> None:
    command = openshell_commands.get_command(request, name)
    result = openshell_commands.run_command(command, 30, capture=True)
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
        and labels.get(openshell_commands.RESERVED_LABEL) == token
    )
    if not owned:
        raise ExecutionError(
            f"refusing to delete sandbox with mismatched ownership: {name}"
        )
