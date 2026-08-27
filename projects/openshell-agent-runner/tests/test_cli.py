# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path

from typer.main import get_group
from typer.testing import CliRunner

import openshell_agent_runner.cli as cli
from openshell_agent_runner.cli import app

REPOSITORY = Path(__file__).resolve().parents[3]
PACKAGED_PROFILE = (
    REPOSITORY
    / "projects/openshell-agent-runner/src/openshell_agent_runner/profiles/reviewer"
)


def test_root_help_exposes_only_supported_commands() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    for command, description in (
        ("init", "Create editable profiles from resources packaged with OAR."),
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


def test_init_help_has_only_the_supported_options() -> None:
    result = CliRunner().invoke(app, ["init", "--help"])

    assert result.exit_code == 0
    assert "PROFILE_ROOT" in result.stdout
    init_command = get_group(app).commands["init"]
    options = {
        option
        for parameter in init_command.params
        for option in parameter.opts
        if option.startswith("--")
    }
    assert options == {"--model", "--profile", "--thinking"}


def test_init_command_creates_a_valid_profile(tmp_path: Path) -> None:
    destination = tmp_path / "profiles"
    result = CliRunner().invoke(
        app,
        [
            "init",
            str(destination),
            "--profile",
            "reviewer",
            "--model",
            "provider/model",
            "--thinking",
            "medium",
        ],
    )

    assert result.exit_code == 0, result.output
    assert f"  {destination / 'reviewer'}" in result.stdout
    validation = CliRunner().invoke(app, ["validate", str(destination / "reviewer")])
    assert validation.exit_code == 0, validation.output


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
        "--prompt-var",
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
            ("version", "openshell 0.0.111"),
            ("status", "Server Status\n\n  Status: Connected"),
            ("inference", "Inference:\n\n  Provider: example"),
        ],
    )

    result = CliRunner().invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert result.stdout == (
        "openshell 0.0.111\n\n"
        "Server Status\n\n"
        "  Status: Connected\n\n"
        "Inference:\n\n"
        "  Provider: example\n"
    )


def test_run_help_describes_selected_profile_task() -> None:
    result = CliRunner().invoke(
        app,
        ["run", str(PACKAGED_PROFILE), "--task", "review-document", "--help"],
    )

    assert result.exit_code == 0
    assert "reviewer:review-document" in result.stdout
    assert (
        "Review an input document and return a useful written result." in result.stdout
    )
    assert "--input DOCUMENT" in result.stdout
    assert "Required argument:" in result.stdout
    assert "Host document to review." in result.stdout
    assert "--prompt-var focus=VALUE" in result.stdout
    assert "--prompt-var context=VALUE" in result.stdout
    assert "Default: Review the complete document." in result.stdout
    assert "Additional configured uploads:" in result.stdout
    assert "Configured environment:" in result.stdout
    assert "None. Add values with --env KEY=VALUE." in result.stdout
    assert "The agent's final response." in result.stdout
    assert "Usage: oar run [OPTIONS]" not in result.stdout
    assert "Options" not in result.stdout


def test_run_help_describes_repository_input() -> None:
    result = CliRunner().invoke(
        app,
        ["run", str(PACKAGED_PROFILE), "--task", "review-repository", "--help"],
    )

    assert result.exit_code == 0
    assert "reviewer:review-repository" in result.stdout
    assert "Review an input code repository" in result.stdout
    assert "--input REPOSITORY" in result.stdout
    assert "Host code repository to review." in result.stdout
    assert "--prompt-var focus=VALUE" in result.stdout
    assert "--prompt-var context=VALUE" in result.stdout
    assert "Default: Review the entire repository." in result.stdout


def test_run_help_colors_selected_profile_task() -> None:
    result = CliRunner().invoke(
        app,
        ["run", str(PACKAGED_PROFILE), "--task", "review-document", "--help"],
        color=True,
    )

    assert result.exit_code == 0
    assert "\x1b[36m\x1b[1mreviewer:review-document\x1b[0m" in result.stdout
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
            "review-document",
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
    assert str(document.resolve()) in result.stdout
    assert "/workspace/input/document.md" in result.stdout
    assert "--env REPOSITORY_ROOT=/workspace/input" in result.stdout
    assert not output.exists()


def test_document_task_requires_input() -> None:
    result = CliRunner().invoke(
        app,
        [
            "run",
            str(PACKAGED_PROFILE),
            "--task",
            "review-document",
            "--output",
            "review.json",
            "--dry-run",
        ],
    )

    assert result.exit_code == 2
    assert "requires --input DOCUMENT" in result.stderr


def test_repository_task_uploads_directory_and_sets_working_directory(
    tmp_path: Path,
) -> None:
    output = tmp_path / "review.md"
    repository = tmp_path / "source-repository"
    repository.mkdir()
    result = CliRunner().invoke(
        app,
        [
            "run",
            str(PACKAGED_PROFILE),
            "--task",
            "review-repository",
            "--output",
            str(output),
            "--input",
            str(repository),
            "--prompt-var",
            "focus=src/auth and tests/auth",
            "--prompt-var",
            "context=Pre-release review",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.output
    assert f"{repository.resolve()} /workspace/input" in result.stdout
    assert "--env REPOSITORY_ROOT=/workspace/input/source-repository" in result.stdout
    assert not output.exists()


def test_repository_task_requires_input() -> None:
    result = CliRunner().invoke(
        app,
        [
            "run",
            str(PACKAGED_PROFILE),
            "--task",
            "review-repository",
            "--output",
            "review.md",
            "--dry-run",
        ],
    )

    assert result.exit_code == 2
    assert "requires --input REPOSITORY" in result.stderr


def test_removed_review_task_is_unknown() -> None:
    result = CliRunner().invoke(
        app,
        ["run", str(PACKAGED_PROFILE), "--task", "review", "--help"],
    )

    assert result.exit_code == 2
    assert "unknown task 'review'" in result.stderr


def test_validate_reports_invalid_encoding_as_cli_input_error(tmp_path: Path) -> None:
    profile = tmp_path / "profile.yaml"
    profile.write_bytes(b"\xff\xfe")

    result = CliRunner().invoke(app, ["validate", str(tmp_path)])

    assert result.exit_code == 2
    assert "cannot read configuration" in result.stderr
