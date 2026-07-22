from pathlib import Path

from typer.testing import CliRunner

from openshell_middleware_init import cli
from openshell_middleware_init.generator import InitializationError, InitializationResult

runner = CliRunner()


def test_help_describes_required_choices() -> None:
    result = runner.invoke(cli.app, ["--help"])

    assert result.exit_code == 0
    assert "--language" in result.stdout
    assert "--openshell-version" in result.stdout


def test_cli_reports_success(monkeypatch, tmp_path: Path) -> None:
    destination = tmp_path / "audit"

    def fake_initialize_project(**options):
        assert options["name"] == "audit"
        assert options["language"] == "python"
        assert options["destination"] == destination
        return InitializationResult(
            destination=destination,
            language="python",
            openshell_version="v0.0.86",
            run_command="uv run audit",
        )

    monkeypatch.setattr(cli, "initialize_project", fake_initialize_project)

    result = runner.invoke(
        cli.app,
        [
            "audit",
            "--language",
            "python",
            "--openshell-version",
            "v0.0.86",
            "--output",
            str(destination),
        ],
    )

    assert result.exit_code == 0
    assert "Created python middleware project" in result.stdout
    assert "OpenShell contract: v0.0.86" in result.stdout


def test_cli_reports_initialization_error(monkeypatch, tmp_path: Path) -> None:
    def fake_initialize_project(**options):
        del options
        raise InitializationError("output exists")

    monkeypatch.setattr(cli, "initialize_project", fake_initialize_project)

    result = runner.invoke(
        cli.app,
        [
            "audit",
            "--language",
            "rust",
            "--openshell-version",
            "v0.0.86",
            "--output",
            str(tmp_path / "audit"),
        ],
    )

    assert result.exit_code == 1
    assert "error: output exists" in result.stderr
