# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import subprocess

import pytest

from openshell_agent_runner.errors import ExecutionError
from openshell_agent_runner.openshell import NativeTarget, doctor


def test_doctor_runs_only_read_only_checks(monkeypatch) -> None:
    commands: list[list[str]] = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        output = "openshell 0.0.106\n" if "--version" in command else "ready\n"
        return subprocess.CompletedProcess(command, 0, output, "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    checks = doctor(NativeTarget(gateway="local", workspace="review"))

    assert [name for name, _ in checks] == ["version", "status", "inference"]
    assert commands == [
        ["openshell", "--version", "--gateway", "local", "--workspace", "review"],
        ["openshell", "status", "--gateway", "local", "--workspace", "review"],
        [
            "openshell",
            "inference",
            "get",
            "--gateway",
            "local",
            "--workspace",
            "review",
        ],
    ]


def test_doctor_rejects_unsupported_openshell(monkeypatch) -> None:
    def fake_run(command, **_kwargs):
        return subprocess.CompletedProcess(command, 0, "openshell 0.0.105\n", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(ExecutionError, match="0.0.106 or newer"):
        doctor(NativeTarget())
