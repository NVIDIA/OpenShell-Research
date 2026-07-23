from pathlib import Path

from typer.testing import CliRunner

from middleware_kit import cli
from middleware_kit.generator import ProjectError, ProjectResult

runner = CliRunner()


def test_help_describes_required_choices() -> None:
    result = runner.invoke(cli.app, ["--help"])

    assert result.exit_code == 0
    assert "create" in result.stdout
    assert "update" in result.stdout

    create_help = runner.invoke(cli.app, ["create", "--help"])

    assert create_help.exit_code == 0
    assert "--language" in create_help.stdout
    assert "--openshell-version" in create_help.stdout


def test_cli_reports_success(monkeypatch, tmp_path: Path) -> None:
    destination = tmp_path / "audit"

    def fake_create_project(**options):
        assert options["name"] == "audit"
        assert options["language"] == "python"
        assert options["destination"] == destination
        return ProjectResult(
            destination=destination,
            language="python",
            openshell_version="v0.0.86",
            run_command="uv run audit",
        )

    monkeypatch.setattr(cli, "create_project", fake_create_project)

    result = runner.invoke(
        cli.app,
        [
            "create",
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


def test_cli_reports_project_error(monkeypatch, tmp_path: Path) -> None:
    def fake_create_project(**options):
        del options
        raise ProjectError("output exists")

    monkeypatch.setattr(cli, "create_project", fake_create_project)

    result = runner.invoke(
        cli.app,
        [
            "create",
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


def test_cli_reports_update_success(monkeypatch, tmp_path: Path) -> None:
    destination = tmp_path / "audit"

    def fake_update_project(**options):
        assert options == {
            "project_dir": destination,
            "requested_version": "v1.2.3",
        }
        return ProjectResult(
            destination=destination,
            language="rust",
            openshell_version="v1.2.3",
            run_command="",
        )

    monkeypatch.setattr(cli, "update_project", fake_update_project)

    result = runner.invoke(
        cli.app,
        [
            "update",
            str(destination),
            "--openshell-version",
            "v1.2.3",
        ],
    )

    assert result.exit_code == 0
    assert "Updated rust middleware project" in result.stdout
    assert "OpenShell contract: v1.2.3" in result.stdout


def test_cli_reports_update_error(monkeypatch, tmp_path: Path) -> None:
    def fake_update_project(**options):
        del options
        raise ProjectError("not generated")

    monkeypatch.setattr(cli, "update_project", fake_update_project)

    result = runner.invoke(cli.app, ["update", str(tmp_path)])

    assert result.exit_code == 1
    assert "error: not generated" in result.stderr
