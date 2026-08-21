# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Build, execute, and inspect native OpenShell commands."""

from __future__ import annotations

import re
import shlex
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from openshell_agent_runner.artifacts import ARTIFACT_PATH
from openshell_agent_runner.errors import ExecutionError

if TYPE_CHECKING:
    from openshell_agent_runner.harnesses.resources import PreparedResources
    from openshell_agent_runner.runner import ResolvedRun, RunRequest

MINIMUM_OPEN_SHELL_VERSION = (0, 0, 111)
RESERVED_LABEL = "oar-run-id"
VERSION_PATTERN = re.compile(r"\b(\d+)\.(\d+)\.(\d+)\b")


@dataclass(frozen=True)
class NativeTarget:
    executable: str = "openshell"
    gateway: str | None = None
    workspace: str = "default"

    def global_args(self) -> list[str]:
        values: list[str] = []
        if self.gateway:
            values.extend(["--gateway", self.gateway])
        values.extend(["--workspace", self.workspace])
        return values


def sandbox_create(
    resolved: ResolvedRun,
    resources: PreparedResources,
    name: str,
    token: str,
) -> list[str]:
    command = [*resolved.create_command, "--name", name]
    for upload in resources.uploads:
        command.extend(["--upload", upload])
    command.extend(["--label", f"{RESERVED_LABEL}={token}"])
    command.extend(["--", "bash", "/opt/oar/pi/exec.sh", *resources.arguments])
    return command


def sandbox_download(resolved: ResolvedRun, name: str, destination: Path) -> list[str]:
    return [
        resolved.request.openshell_bin,
        "sandbox",
        "download",
        name,
        ARTIFACT_PATH,
        str(destination),
        *_native_target_args(resolved.request),
    ]


def sandbox_get(request: RunRequest, name: str) -> list[str]:
    return [
        request.openshell_bin,
        "sandbox",
        "get",
        name,
        *_native_target_args(request),
        "--output",
        "json",
    ]


def sandbox_delete(request: RunRequest, name: str) -> list[str]:
    return [
        request.openshell_bin,
        "sandbox",
        "delete",
        name,
        *_native_target_args(request),
    ]


def run(
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


def doctor(target: NativeTarget) -> list[tuple[str, str]]:
    checks = []
    for name, arguments in (
        ("version", ["--version"]),
        ("status", ["status"]),
        ("inference", ["inference", "get"]),
    ):
        completed = _run_read_only(target, arguments)
        result = completed.stdout.strip()
        if name == "version":
            match = VERSION_PATTERN.search(result)
            if match is None:
                raise ExecutionError(f"cannot parse OpenShell version: {result!r}")
            version = tuple(int(part) for part in match.groups())
            if version < MINIMUM_OPEN_SHELL_VERSION:
                minimum = ".".join(str(part) for part in MINIMUM_OPEN_SHELL_VERSION)
                raise ExecutionError(
                    f"OpenShell {minimum} or newer is required; found {match.group(0)}"
                )
        checks.append((name, result))
    return checks


def _native_target_args(request: RunRequest) -> list[str]:
    result: list[str] = []
    if request.gateway:
        result.extend(["--gateway", request.gateway])
    result.extend(["--workspace", request.workspace])
    return result


def _run_read_only(
    target: NativeTarget, arguments: Sequence[str]
) -> subprocess.CompletedProcess[str]:
    command = [target.executable, *arguments, *target.global_args()]
    try:
        return subprocess.run(command, check=True, text=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError) as error:
        raise ExecutionError(
            f"OpenShell check failed: {shlex.join(command)}: {error}"
        ) from error
