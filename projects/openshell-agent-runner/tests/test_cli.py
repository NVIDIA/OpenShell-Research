# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path

from typer.main import get_group
from typer.testing import CliRunner

import openshell_agent_runner.cli as cli
from openshell_agent_runner.cli import app

REPOSITORY = Path(__file__).resolve().parents[3]
PACKAGED_PROFILE = REPOSITORY / "projects/openshell-agent-runner/profiles/reviewer"


def test_root_help_exposes_only_supported_commands() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    for command, description in (
        ("validate", "Validate a profile and all referenced local resources."),
        ("run", "Launch or preview an ephemeral agent for one profile task."),
        ("doctor", "Check OpenShell readiness without changing its state."),
    ):
        assert command in result.stdout
        assert description in result.stdout
    for removed in ("plan", "schema", "config", "profiles", "tasks"):
        assert f"│ {removed}" not in result.stdout
    assert "--install-completion" not in result.stdout
    assert "--show-completion" not in result.stdout


def test_run_help_has_only_the_supported_override_surface() -> None:
    result = CliRunner().invoke(app, ["run", "--help"])

    assert result.exit_code == 0
    assert "PROFILE_DIRECTORY" in result.stdout
    run_command = get_group(app).commands["run"]
    options = {
        option
        for parameter in run_command.params
        for option in parameter.opts
        if option.startswith("--")
    }
    assert options == {
        "--task",
        "--output",
        "--input",
        "--upload",
        "--env",
        "--gateway",
        "--workspace",
        "--timeout-seconds",
        "--keep-sandbox",
        "--dry-run",
    }


def test_doctor_separates_native_output_with_blank_lines(monkeypatch) -> None:
    monkeypatch.setattr(
        cli,
        "run_doctor",
        lambda _target: [
            ("version", "openshell 0.0.106"),
            ("status", "Server Status\n\n  Status: Connected"),
            ("inference", "Inference:\n\n  Provider: example"),
        ],
    )

    result = CliRunner().invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert result.stdout == (
        "openshell 0.0.106\n\n"
        "Server Status\n\n"
        "  Status: Connected\n\n"
        "Inference:\n\n"
        "  Provider: example\n"
    )


def test_run_help_describes_selected_profile_task() -> None:
    result = CliRunner().invoke(
        app,
        ["run", str(PACKAGED_PROFILE), "--task", "review", "--help"],
    )

    assert result.exit_code == 0
    assert "reviewer:review" in result.stdout
    assert (
        "Review an input document and return a useful written result." in result.stdout
    )
    assert "--input DOCUMENT" in result.stdout
    assert "Required argument:" in result.stdout
    assert "Host document to review." in result.stdout
    assert "Additional configured uploads:" in result.stdout
    assert "Configured environment:" in result.stdout
    assert "None. Add values with --env KEY=VALUE." in result.stdout
    assert "The agent's final response." in result.stdout
    assert "Usage: oar run [OPTIONS]" not in result.stdout
    assert "Options" not in result.stdout


def test_run_help_colors_selected_profile_task() -> None:
    result = CliRunner().invoke(
        app,
        ["run", str(PACKAGED_PROFILE), "--task", "review", "--help"],
        color=True,
    )

    assert result.exit_code == 0
    assert "\x1b[36m\x1b[1mreviewer:review\x1b[0m" in result.stdout
    assert "\x1b[33m\x1b[1mUsage:\x1b[0m" in result.stdout
    assert "\x1b[32m  oar run " in result.stdout


def test_run_help_rejects_unknown_profile_task() -> None:
    result = CliRunner().invoke(
        app,
        ["run", str(PACKAGED_PROFILE), "--task", "inspect", "--help"],
    )

    assert result.exit_code == 2
    assert "unknown task 'inspect' for profile 'reviewer'" in result.stderr
    assert "Launch or preview an ephemeral agent" not in result.stdout
    assert "Options" not in result.stdout


def test_run_dry_run_does_not_publish_output(tmp_path: Path) -> None:
    output = tmp_path / "review.json"
    document = tmp_path / "document.md"
    document.write_text("# Document\n")
    result = CliRunner().invoke(
        app,
        [
            "run",
            str(PACKAGED_PROFILE),
            "--task",
            "review",
            "--output",
            str(output),
            "--input",
            str(document),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Dry run: no commands were executed." in result.stdout
    assert "[create]" in result.stdout
    assert "[download]" in result.stdout
    assert "[verify ownership]" in result.stdout
    assert "[delete]" in result.stdout
    assert f"{document.resolve()}:/workspace/input/document.md" in result.stdout
    assert "--env REPOSITORY_ROOT=/workspace/input" in result.stdout
    assert not output.exists()


def test_document_task_requires_input() -> None:
    result = CliRunner().invoke(
        app,
        [
            "run",
            str(PACKAGED_PROFILE),
            "--task",
            "review",
            "--output",
            "review.json",
            "--dry-run",
        ],
    )

    assert result.exit_code == 2
    assert "requires --input DOCUMENT" in result.stderr


def test_validate_reports_invalid_encoding_as_cli_input_error(tmp_path: Path) -> None:
    profile = tmp_path / "profile.yaml"
    profile.write_bytes(b"\xff\xfe")

    result = CliRunner().invoke(app, ["validate", str(tmp_path)])

    assert result.exit_code == 2
    assert "cannot read configuration" in result.stderr
