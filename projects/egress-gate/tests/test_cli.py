"""Command-line tests for Egress Gate discovery and policy schema surfaces."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest
from typer.testing import CliRunner

from egress_gate.cli import _load_registry, app
from egress_gate.errors import GateRegistryError
from egress_gate.gates import GateRegistry, create_builtin_registry


def test_cli_gates_describes_the_request_level_builtin() -> None:
    result = CliRunner().invoke(app, ["gates"])

    assert result.exit_code == 0
    assert result.stdout.startswith("regex\tfindings=regex_match\t")
    assert (
        "capabilities=reads_target,reads_headers,reads_body,replaces_body,"
        "produces_findings,may_deny" in result.stdout
    )
    assert "resources=-\tconfig=RegexConfig" in result.stdout


def test_cli_configuration_schema_exposes_pipeline_only() -> None:
    result = CliRunner().invoke(app, ["configuration-schema"])

    assert result.exit_code == 0
    assert '"pipeline"' in result.stdout
    assert '"default_decision"' in result.stdout


def test_registry_factory_loader_requires_a_finalized_gate_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = ModuleType("test_registry_factory")
    module.__dict__["create_registry"] = create_builtin_registry
    module.__dict__["unfinished"] = lambda: GateRegistry()
    monkeypatch.setitem(sys.modules, module.__name__, module)

    assert _load_registry("test_registry_factory:create_registry").is_finalized
    with pytest.raises(Exception, match="unfinalized registry"):
        _load_registry("test_registry_factory:unfinished")


@pytest.mark.parametrize(
    "reference",
    ["missing-separator", "test_registry_factory:missing"],
)
def test_registry_factory_loader_rejects_invalid_references(reference: str) -> None:
    with pytest.raises(Exception):
        _load_registry(reference)


def test_unfinalized_registry_cannot_be_used_by_the_middleware() -> None:
    with pytest.raises(GateRegistryError):
        create_builtin_registry().register(object)


def test_cli_evaluate_runs_the_builtin_policy_corpus() -> None:
    project_dir = Path(__file__).parents[1]
    result = CliRunner().invoke(
        app,
        [
            "evaluate",
            "--policy",
            str(project_dir / "examples/regex-redaction/egress-gate-config.yaml"),
            "--cases",
            str(project_dir / "examples/regex-redaction/cases.yaml"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert 'PASS case="email-is-detected-and-request-is-allowed"' in result.stdout
    assert "SUMMARY total=2 passed=2 failed=0" in result.stdout


def test_cli_evaluate_runs_the_custom_gate_example() -> None:
    project_dir = Path(__file__).parents[1]
    result = CliRunner().invoke(
        app,
        [
            "--registry-factory",
            "examples.custom_gate.keyword_gate:create_registry",
            "evaluate",
            "--policy",
            str(project_dir / "examples/custom_gate/egress-gate-config.yaml"),
            "--cases",
            str(project_dir / "examples/custom_gate/cases.yaml"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert 'PASS case="configured-keyword-is-denied"' in result.stdout
    assert 'PASS case="other-bodies-proceed-to-the-default"' in result.stdout
    assert "SUMMARY total=2 passed=2 failed=0" in result.stdout


def test_installed_executable_loads_a_registry_from_the_working_directory() -> None:
    project_dir = Path(__file__).parents[1]
    executable = Path(sys.executable).with_name("egress-gate")

    result = subprocess.run(
        [
            executable,
            "--registry-factory",
            "examples.custom_gate.keyword_gate:create_registry",
            "gates",
        ],
        cwd=project_dir,
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "\nkeyword-deny\t" in result.stdout


def test_cli_validate_checks_policy_without_preparing_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = Path(__file__).parents[1]

    def unexpected_preparation(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("validation prepared a gate")

    monkeypatch.setattr(GateRegistry, "create_gate", unexpected_preparation)
    result = CliRunner().invoke(
        app,
        [
            "validate",
            "--policy",
            str(project_dir / "examples/regex-redaction/egress-gate-config.yaml"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert result.stdout == "VALID\n"


def test_cli_validate_rejects_invalid_policy(tmp_path: Path) -> None:
    policy = tmp_path / "invalid.yaml"
    policy.write_text("unexpected: true\n")

    result = CliRunner().invoke(
        app,
        ["validate", "--policy", str(policy)],
    )

    assert result.exit_code == 1
    assert result.stderr == "VALIDATE_ERROR config_invalid\n"


def test_cli_evaluate_reports_content_safe_mismatch_status(tmp_path: Path) -> None:
    project_dir = Path(__file__).parents[1]
    cases = tmp_path / "cases.yaml"
    original = (project_dir / "examples/regex-redaction/cases.yaml").read_text()
    cases.write_text(original.replace("decision: allow", "decision: deny", 1))

    result = CliRunner().invoke(
        app,
        [
            "evaluate",
            "--policy",
            str(project_dir / "examples/regex-redaction/egress-gate-config.yaml"),
            "--cases",
            str(cases),
        ],
    )

    assert result.exit_code == 1
    assert (
        'FAIL case="email-is-detected-and-request-is-allowed" field=decision'
        in result.stdout
    )
    assert "SUMMARY total=2 passed=1 failed=1" in result.stdout
    assert "{}" not in result.stdout


@pytest.mark.parametrize(
    "invalid_yaml",
    [
        "version: 1\ncases: &cases []\n",
        "version: 1\ncases:\n  - name: one\n    name: two\n",
    ],
)
def test_cli_evaluate_rejects_non_strict_corpus_yaml(
    tmp_path: Path,
    invalid_yaml: str,
) -> None:
    project_dir = Path(__file__).parents[1]
    cases = tmp_path / "cases.yaml"
    cases.write_text(invalid_yaml)

    result = CliRunner().invoke(
        app,
        [
            "evaluate",
            "--policy",
            str(project_dir / "examples/regex-redaction/egress-gate-config.yaml"),
            "--cases",
            str(cases),
        ],
    )

    assert result.exit_code == 2
    assert "EVALUATE_ERROR invalid_input" in result.stderr
    assert "YAML aliases" not in result.output
