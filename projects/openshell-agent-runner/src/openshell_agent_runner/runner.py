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

import openshell_agent_runner.openshell as openshell
from openshell_agent_runner.artifacts import (
    MAX_ARTIFACT_BYTES,
    atomic_publish,
    validate_artifact,
)
from openshell_agent_runner.config import (
    ResolvedProfile,
    resolve_task,
    validate_environment_assignments,
    validate_upload_mappings,
)
from openshell_agent_runner.errors import ConfigurationError, ExecutionError
from openshell_agent_runner.harnesses.pi.resources import (
    image_directory,
    prepare_resources,
)


@dataclass(frozen=True)
class RunRequest:
    profile_directory: Path
    task_id: str
    output: Path
    input_document: Path | None = None
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
    uploads: tuple[str, ...]
    environments: tuple[str, ...]
    create_command: tuple[str, ...]


def resolve_run(request: RunRequest) -> ResolvedRun:
    profile = resolve_task(request.profile_directory, request.task_id)
    task = profile.profile.tasks[request.task_id]
    document_upload = _resolve_document_upload(request, task.required_input)
    uploads = _validate_uploads(
        [
            *profile.profile.sandbox.upload,
            *([document_upload] if document_upload else []),
            *request.uploads,
        ]
    )
    environments = _validate_environments(
        [
            *([_DOCUMENT_INPUT_ENVIRONMENT] if document_upload else []),
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
        uploads=uploads,
        environments=environments,
        create_command=tuple(command),
    )


def render_dry_run(request: RunRequest) -> str:
    """Render the exact nominal command sequence without executing subprocesses."""
    resolved = resolve_run(request)
    name, token = _identity()
    resources = prepare_resources(resolved.profile, request.task_id)
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
    resources = prepare_resources(resolved.profile, request.task_id)
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


def _resolve_document_upload(
    request: RunRequest, required_input: str | None
) -> str | None:
    if required_input is None:
        if request.input_document is not None:
            raise ConfigurationError(
                f"task {request.task_id!r} does not accept --input"
            )
        return None
    if request.input_document is None:
        raise ConfigurationError(f"task {request.task_id!r} requires --input DOCUMENT")
    try:
        document = request.input_document.resolve(strict=True)
    except OSError as error:
        raise ConfigurationError(
            f"input document does not exist: {request.input_document}"
        ) from error
    if not document.is_file():
        raise ConfigurationError(f"input document must be a file: {document}")
    return f"{document}:{_DOCUMENT_INPUT_PATH}"


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


_DOCUMENT_INPUT_PATH = "/workspace/input/document.md"
_DOCUMENT_INPUT_ENVIRONMENT = "REPOSITORY_ROOT=/workspace/input"
