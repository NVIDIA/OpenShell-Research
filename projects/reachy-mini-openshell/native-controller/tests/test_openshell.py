"""Tests for fixed native OpenShell lifecycle commands."""

# ruff: noqa: D103

import os
import subprocess
from pathlib import Path

from reachy_mini_openshell_controller.openshell import OpenShellController
from reachy_mini_openshell_controller.settings import ControllerSettings


def executable(tmp_path: Path) -> Path:
    path = tmp_path / "openshell"
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_start_agent_uses_only_fixed_lifecycle_commands(tmp_path: Path, monkeypatch) -> None:
    openshell = executable(tmp_path)
    calls: list[list[str]] = []

    def run(command: list[str], **kwargs):
        calls.append(command)
        if command[1:4] == ["sandbox", "get", "reachy-agent"]:
            return subprocess.CompletedProcess(command, 0, "Phase: Ready\n", "")
        if command[1:5] == ["service", "get", "reachy-agent", "audio"]:
            return subprocess.CompletedProcess(command, 1, "", "service endpoint not found")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    controller = OpenShellController(ControllerSettings(openshell_executable=os.fspath(openshell)))

    controller.start_agent()

    assert [call[1:] for call in calls] == [
        ["sandbox", "get", "reachy-agent"],
        [
            "sandbox",
            "exec",
            "--name",
            "reachy-agent",
            "--no-tty",
            "--",
            "/opt/venv/bin/reachy-agent-control",
            "start",
        ],
        ["service", "get", "reachy-agent", "audio"],
        ["service", "expose", "reachy-agent", "8765", "audio"],
    ]


def test_verify_ready_accepts_ansi_formatted_output(tmp_path: Path, monkeypatch) -> None:
    openshell = executable(tmp_path)

    def run(command: list[str], **kwargs):
        return subprocess.CompletedProcess(command, 0, "\x1b[2m  Phase:\x1b[0m \x1b[32mReady\x1b[0m\n", "")

    monkeypatch.setattr(subprocess, "run", run)
    controller = OpenShellController(ControllerSettings(openshell_executable=os.fspath(openshell)))

    controller.verify_ready()


def test_stop_agent_does_not_delete_sandbox(tmp_path: Path, monkeypatch) -> None:
    openshell = executable(tmp_path)
    calls: list[list[str]] = []

    def run(command: list[str], **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    controller = OpenShellController(ControllerSettings(openshell_executable=os.fspath(openshell)))

    controller.stop_agent()

    assert calls[0][1:] == [
        "sandbox",
        "exec",
        "--name",
        "reachy-agent",
        "--no-tty",
        "--",
        "/opt/venv/bin/reachy-agent-control",
        "stop",
    ]
    assert "delete" not in calls[0]
