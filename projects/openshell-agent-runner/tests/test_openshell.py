# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import subprocess
import sys

import pytest

from openshell_agent_runner.errors import ExecutionError, ExecutionTimeoutError
from openshell_agent_runner.openshell import NativeTarget, doctor, run


def test_native_commands_do_not_consume_the_callers_input_stream() -> None:
    program = """
import json
import sys

from openshell_agent_runner.openshell import run

child = run(
    [sys.executable, "-c", "import sys; print(sys.stdin.read(), end='')"],
    timeout=5,
    capture=True,
)
print(json.dumps({"child_input": child.stdout, "remaining_input": sys.stdin.read()}))
"""
    pending_tasks = "research-spike\nuse-case-example\n"
    completed = subprocess.run(
        [sys.executable, "-c", program],
        input=pending_tasks,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )

    assert json.loads(completed.stdout) == {
        "child_input": "",
        "remaining_input": pending_tasks,
    }


def test_timeout_has_a_distinct_error_and_redacts_the_model(monkeypatch) -> None:
    def time_out(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", time_out)

    with pytest.raises(ExecutionTimeoutError) as raised:
        run(["openshell", "sandbox", "exec", "--model", "secret-model"], 30)

    message = str(raised.value)
    assert "timed out after 30 seconds" in message
    assert "secret-model" not in message
    assert "<model>" in message


def test_doctor_runs_only_read_only_checks(monkeypatch) -> None:
    commands: list[list[str]] = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        output = "openshell 0.0.111\n" if "--version" in command else "ready\n"
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
        return subprocess.CompletedProcess(command, 0, "openshell 0.0.110\n", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(ExecutionError, match="0.0.111 or newer"):
        doctor(NativeTarget())
