# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Read-only OpenShell prerequisite checks and command rendering."""

from __future__ import annotations

import re
import shlex
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass

from openshell_agent_runner.errors import ExecutionError

MINIMUM_OPEN_SHELL_VERSION = (0, 0, 106)
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


def run_read_only(
    target: NativeTarget, arguments: Sequence[str]
) -> subprocess.CompletedProcess[str]:
    command = [target.executable, *arguments, *target.global_args()]
    try:
        return subprocess.run(command, check=True, text=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError) as error:
        raise ExecutionError(
            f"OpenShell check failed: {shlex.join(command)}: {error}"
        ) from error


def doctor(target: NativeTarget) -> list[tuple[str, str]]:
    checks = []
    for name, arguments in (
        ("version", ["--version"]),
        ("status", ["status"]),
        ("inference", ["inference", "get"]),
    ):
        completed = run_read_only(target, arguments)
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
