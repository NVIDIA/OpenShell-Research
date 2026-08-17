# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path

from typer.main import get_group
from typer.testing import CliRunner

from openshell_agent_runner.cli import app

REPOSITORY = Path(__file__).resolve().parents[3]
PACKAGED_PROFILE = (
    REPOSITORY / "projects/openshell-agent-runner/profiles/reviewer/profile.yaml"
)


def test_root_help_exposes_only_supported_commands() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    for command, description in (
        ("validate", "Validate a profile and all referenced local resources."),
        ("run", "Run or preview one profile task and its validated output."),
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
        "--upload",
        "--env",
        "--gateway",
        "--workspace",
        "--timeout-seconds",
        "--keep-sandbox",
        "--dry-run",
    }


def test_run_dry_run_does_not_publish_output(tmp_path: Path) -> None:
    output = tmp_path / "review.json"
    result = CliRunner().invoke(
        app,
        [
            "run",
            str(PACKAGED_PROFILE),
            "--task",
            "inspect",
            "--output",
            str(output),
            "--upload",
            ".:/workspace/input",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Dry run: no commands were executed." in result.stdout
    assert "[create]" in result.stdout
    assert "[download]" in result.stdout
    assert "[verify ownership]" in result.stdout
    assert "[delete]" in result.stdout
    assert not output.exists()


def test_validate_reports_invalid_encoding_as_cli_input_error(tmp_path: Path) -> None:
    profile = tmp_path / "profile.yaml"
    profile.write_bytes(b"\xff\xfe")

    result = CliRunner().invoke(app, ["validate", str(profile)])

    assert result.exit_code == 2
    assert "cannot read configuration" in result.stderr
