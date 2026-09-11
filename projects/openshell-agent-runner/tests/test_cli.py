# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import shutil
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as distribution_version
from pathlib import Path

import pytest
import yaml
from rich.text import Text
from typer.testing import CliRunner

from openshell_agent_runner.cli import app
from openshell_agent_runner.errors import ExecutionTimeoutError

REPOSITORY = Path(__file__).resolve().parents[3]
CODE_REVIEWER = (
    REPOSITORY
    / "projects/openshell-agent-runner/src/openshell_agent_runner/profiles/code-reviewer"
)
TECHNICAL_WRITING_REVIEWER = (
    REPOSITORY
    / "projects/openshell-agent-runner/src/openshell_agent_runner/profiles/technical-writing-reviewer"
)


def test_root_help_lists_commands() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    help_text = Text.from_ansi(result.stdout).plain
    for command in ("init", "validate", "run", "doctor"):
        assert command in help_text
    assert "--version" in help_text


def test_version_reports_installed_distribution_version() -> None:
    result = CliRunner().invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout == f"oar {distribution_version('openshell-agent-runner')}\n"


def test_version_handles_unavailable_distribution_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing_distribution(_distribution_name: str) -> str:
        raise PackageNotFoundError

    monkeypatch.setattr(
        "openshell_agent_runner.cli.distribution_version", missing_distribution
    )

    result = CliRunner().invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout == "oar unknown\n"


@pytest.mark.parametrize(
    ("command", "arguments"),
    [
        ("init", ["PROFILE_ROOT", "--model", "--profile", "--thinking"]),
        ("run", ["PROFILE_DIRECTORY", "--task", "--input", "--output", "--dry-run"]),
    ],
)
def test_help_explains_how_to_start(command: str, arguments: list[str]) -> None:
    result = CliRunner().invoke(app, [command, "--help"])
    assert result.exit_code == 0
    help_text = Text.from_ansi(result.stdout).plain
    for argument in arguments:
        assert argument in help_text


def test_init_command_creates_a_valid_profile(tmp_path: Path) -> None:
    destination = tmp_path / "profiles"
    result = CliRunner().invoke(
        app,
        [
            "init",
            str(destination),
            "--profile",
            "code-reviewer",
            "--model",
            "provider/model",
            "--thinking",
            "medium",
        ],
    )

    assert result.exit_code == 0, result.output
    assert f"  {destination / 'code-reviewer'}" in result.stdout
    validation = CliRunner().invoke(
        app, ["validate", str(destination / "code-reviewer")]
    )
    assert validation.exit_code == 0, validation.output


def test_run_help_describes_selected_profile_task() -> None:
    result = CliRunner().invoke(
        app,
        [
            "run",
            str(TECHNICAL_WRITING_REVIEWER),
            "--task",
            "review-document",
            "--help",
        ],
    )

    assert result.exit_code == 0
    assert "technical-writing-reviewer:review-document" in result.stdout
    assert "--input DOCUMENT" in result.stdout
    assert "--prompt-var focus=VALUE" in result.stdout
    assert "--prompt-var context=VALUE" in result.stdout
    assert "Default: Review the complete document." in result.stdout
    assert "JSON validated against schemas/review.json." in result.stdout


def test_run_help_describes_repository_input() -> None:
    result = CliRunner().invoke(
        app,
        ["run", str(CODE_REVIEWER), "--task", "review-repository", "--help"],
    )

    assert result.exit_code == 0
    assert "code-reviewer:review-repository" in result.stdout
    assert "Review an input code repository" in result.stdout
    assert "--input REPOSITORY" in result.stdout
    assert "Host code repository to review." in result.stdout
    assert "--prompt-var focus=VALUE" in result.stdout
    assert "--prompt-var context=VALUE" in result.stdout
    assert "Default: Review the complete repository." in result.stdout


def test_run_help_rejects_unknown_profile_task() -> None:
    result = CliRunner().invoke(
        app,
        ["run", str(CODE_REVIEWER), "--task", "inspect", "--help"],
    )

    assert result.exit_code == 2
    assert "unknown task 'inspect' for profile 'code-reviewer'" in result.stderr
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
            str(TECHNICAL_WRITING_REVIEWER),
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


def test_run_uses_a_distinct_timeout_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = tmp_path / "document.md"
    document.write_text("# Document\n")

    def time_out(_request) -> None:
        raise ExecutionTimeoutError("command timed out after 30 seconds")

    monkeypatch.setattr("openshell_agent_runner.cli.run_agent", time_out)
    result = CliRunner().invoke(
        app,
        [
            "run",
            str(TECHNICAL_WRITING_REVIEWER),
            "--task",
            "review-document",
            "--output",
            str(tmp_path / "review.json"),
            "--input",
            str(document),
        ],
    )

    assert result.exit_code == 4
    assert "timed out after 30 seconds" in result.stderr


def test_document_task_requires_input() -> None:
    result = CliRunner().invoke(
        app,
        [
            "run",
            str(TECHNICAL_WRITING_REVIEWER),
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
            str(CODE_REVIEWER),
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
            str(CODE_REVIEWER),
            "--task",
            "review-repository",
            "--output",
            "review.md",
            "--dry-run",
        ],
    )

    assert result.exit_code == 2
    assert "requires --input REPOSITORY" in result.stderr


def test_skills_require_read_and_validate_after_correction(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    shutil.copytree(CODE_REVIEWER, profile)
    configuration = yaml.safe_load((profile / "profile.yaml").read_text())
    task = configuration["tasks"]["review-repository"]
    task["tools"] = ["bash"]
    (profile / "profile.yaml").write_text(yaml.safe_dump(configuration))

    result = CliRunner().invoke(app, ["validate", str(profile)])
    assert result.exit_code == 2
    assert "skills must include 'read'" in result.stderr

    task["tools"].append("read")
    (profile / "profile.yaml").write_text(yaml.safe_dump(configuration))
    assert CliRunner().invoke(app, ["validate", str(profile)]).exit_code == 0


def test_validate_reports_invalid_encoding_as_cli_input_error(tmp_path: Path) -> None:
    profile = tmp_path / "profile.yaml"
    profile.write_bytes(b"\xff\xfe")

    result = CliRunner().invoke(app, ["validate", str(tmp_path)])

    assert result.exit_code == 2
    assert "cannot read configuration" in result.stderr
