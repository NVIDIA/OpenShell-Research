# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Build and execute native OpenShell commands."""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from openshell_agent_runner.errors import ExecutionError

if TYPE_CHECKING:
    from openshell_agent_runner.harnesses.resources import PreparedResources
    from openshell_agent_runner.runner import ResolvedRun, RunRequest

RESERVED_LABEL = "oar-run-id"


def create_command(
    resolved: ResolvedRun,
    resources: PreparedResources,
    name: str,
    token: str,
) -> list[str]:
    command = [*resolved.create_command, "--name", name]
    for upload in resources.uploads:
        command.extend(["--upload", upload])
    command.extend(["--label", f"{RESERVED_LABEL}={token}"])
    command.extend(
        ["--", "bash", "/opt/oar/pi/exec.sh", resolved.model, *resources.arguments]
    )
    return command


def download_command(resolved: ResolvedRun, name: str, destination: Path) -> list[str]:
    output = resolved.profile.profile.tasks[resolved.request.task_id].output
    return [
        resolved.request.openshell_bin,
        "sandbox",
        "download",
        name,
        output.sandbox_path,
        str(destination),
        *_native_target_args(resolved.request),
    ]


def get_command(request: RunRequest, name: str) -> list[str]:
    return [
        request.openshell_bin,
        "sandbox",
        "get",
        name,
        *_native_target_args(request),
        "--output",
        "json",
    ]


def delete_command(request: RunRequest, name: str) -> list[str]:
    return [
        request.openshell_bin,
        "sandbox",
        "delete",
        name,
        *_native_target_args(request),
    ]


def run_command(
    command: list[str], timeout: int, *, capture: bool = False
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            check=True,
            text=True,
            capture_output=capture,
            timeout=timeout,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise ExecutionError(
            f"command failed: {shlex.join(command)}: {error}"
        ) from error


def _native_target_args(request: RunRequest) -> list[str]:
    result: list[str] = []
    if request.gateway:
        result.extend(["--gateway", request.gateway])
    result.extend(["--workspace", request.workspace])
    return result
