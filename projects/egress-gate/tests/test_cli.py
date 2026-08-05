"""Command-line tests for Egress Gate discovery and policy schema surfaces."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest
from rich.text import Text
from typer.testing import CliRunner

from egress_gate.cli import _load_registry, app
from egress_gate.errors import GateRegistryError
from egress_gate.gates import GateRegistry, create_builtin_registry


def test_cli_does_not_offer_request_content_logging() -> None:
    result = CliRunner().invoke(app, ["--help"], color=True)

    assert result.exit_code == 0
    help_output = Text.from_ansi(result.stdout).plain
    assert "--debug" in help_output
    assert "--debug-log-content" not in help_output


def test_cli_bare_command_is_successful_help() -> None:
    result = CliRunner().invoke(app, [])

    assert result.exit_code == 0
    assert "Usage: egress-gate" in result.stdout
    assert "Register Egress Gate with OpenShell." in result.stdout
    assert "Inspect installed gates and policy schema." in result.stdout


def test_cli_reports_the_installed_version() -> None:
    result = CliRunner().invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout == "egress-gate 0.1.0\n"


def test_cli_narrow_help_preserves_complete_option_names() -> None:
    result = CliRunner().invoke(
        app,
        ["add-gateway-registration", "--help"],
        env={"COLUMNS": "40"},
    )

    assert result.exit_code == 0
    assert "--host-ip" in result.stdout
    assert "--config" in result.stdout
    assert "--host…" not in result.stdout
    assert "--conf…" not in result.stdout


def test_cli_gates_describes_the_request_level_builtin() -> None:
    result = CliRunner().invoke(app, ["gates", "list"])

    assert result.exit_code == 0
    assert "Installed gates" in result.stdout
    assert "regex" in result.stdout
    assert "regex_match" in result.stdout
    assert "Request access" in result.stdout
    assert "target, headers, body" in result.stdout
    assert "Possible results" in result.stdout
    assert "body replacement, findings, deny decision" in result.stdout
    assert "RegexConfig" in result.stdout
    assert "Python resources" not in result.stdout


def test_cli_configuration_schema_exposes_flat_policy() -> None:
    result = CliRunner().invoke(app, ["gates", "schema"])

    assert result.exit_code == 0
    schema = json.loads(result.stdout)
    assert schema["title"] == "EgressGateConfig"
    assert set(schema["properties"]) == {"gates", "default_decision"}
    assert schema["properties"]["gates"]["minItems"] == 1
    assert schema["properties"]["gates"]["maxItems"] == 10


def test_registry_loader_accepts_a_singleton_or_factory_and_seals_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = ModuleType("test_registry_source")
    singleton = create_builtin_registry()
    module.__dict__["registry"] = singleton
    module.__dict__["create_registry"] = create_builtin_registry
    module.__dict__["empty"] = GateRegistry()
    monkeypatch.setitem(sys.modules, module.__name__, module)

    assert _load_registry("test_registry_source:registry") is singleton
    factory_registry = _load_registry("test_registry_source:create_registry")
    with pytest.raises(GateRegistryError, match="registry is in use"):
        singleton.register(object)
    with pytest.raises(GateRegistryError, match="registry is in use"):
        factory_registry.register(object)
    with pytest.raises(Exception, match="at least one valid gate"):
        _load_registry("test_registry_source:empty")


@pytest.mark.parametrize(
    "reference",
    ["missing-separator", "test_registry_source:missing"],
)
def test_registry_loader_rejects_invalid_references(reference: str) -> None:
    with pytest.raises(Exception):
        _load_registry(reference)


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
    assert "Policy evaluation" in result.stdout
    assert "PASS" in result.stdout
    assert "email-is-detected-and-request-is-allowed" in result.stdout
    assert "2 passed · 0 failed · 2 total" in result.stdout


@pytest.mark.parametrize(
    ("registry_reference", "example_directory"),
    [
        ("examples.custom-gate.keyword_gate:registry", "custom-gate"),
        ("examples.class-based-gate.keyword_gate:registry", "class-based-gate"),
    ],
)
def test_cli_evaluate_runs_the_custom_gate_examples(
    registry_reference: str,
    example_directory: str,
) -> None:
    project_dir = Path(__file__).parents[1]
    result = CliRunner().invoke(
        app,
        [
            "--registry",
            registry_reference,
            "evaluate",
            "--policy",
            str(project_dir / f"examples/{example_directory}/egress-gate-config.yaml"),
            "--cases",
            str(project_dir / f"examples/{example_directory}/cases.yaml"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "configured-keyword-is-denied" in result.stdout
    assert "other-bodies-proceed-to-the-default" in result.stdout
    assert "2 passed · 0 failed · 2 total" in result.stdout


def test_installed_executable_loads_a_registry_from_the_working_directory() -> None:
    project_dir = Path(__file__).parents[1]
    executable = Path(sys.executable).with_name("egress-gate")

    result = subprocess.run(
        [
            executable,
            "--registry",
            "examples.custom-gate.keyword_gate:registry",
            "gates",
            "list",
        ],
        cwd=project_dir,
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "Installed gates" in result.stdout
    assert "keyword-deny" in result.stdout


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
    assert result.stdout == "✓ Policy is valid\n"


def test_cli_validate_rejects_invalid_policy(tmp_path: Path) -> None:
    policy = tmp_path / "invalid.yaml"
    policy.write_text("unexpected: true\n")

    result = CliRunner().invoke(
        app,
        ["validate", "--policy", str(policy)],
    )

    assert result.exit_code == 1
    assert "Policy validation failed [config_invalid]" in result.stderr
    assert "Policy field gates: required field is missing" in result.stderr
    assert "egress-gate gates schema" in result.stderr


def test_cli_validate_reports_a_safe_structural_path(tmp_path: Path) -> None:
    sentinel = "scna-sensitive-sentinel"
    policy = tmp_path / "invalid.yaml"
    policy.write_text(
        """gates:
  - name: one
    kind: regex
    scna-sensitive-sentinel: {}
    pattern_catalog: {}
default_decision: allow
"""
    )

    result = CliRunner().invoke(app, ["validate", "--policy", str(policy)])

    assert result.exit_code == 1
    assert "Policy field gates[0].scan: required field is missing" in result.stderr
    assert sentinel not in result.output


def test_cli_evaluate_catalogs_regex_preparation_failures(tmp_path: Path) -> None:
    project_dir = Path(__file__).parents[1]
    policy = tmp_path / "named-group.yaml"
    policy.write_text(
        """gates:
  - name: identifiers
    kind: regex
    scan:
      kind: body
      action: {kind: detect}
    pattern_catalog:
      entities:
        - name: token
          rules:
            - pattern: '(?P<sensitive_name>secret)'
              confidence: high
default_decision: allow
"""
    )

    result = CliRunner().invoke(
        app,
        [
            "evaluate",
            "--policy",
            str(policy),
            "--cases",
            str(project_dir / "examples/regex-redaction/cases.yaml"),
        ],
    )

    assert result.exit_code == 2
    assert "Evaluation failed [config_preparation_failed]" in result.stderr
    assert "remove named groups" in result.stderr
    assert "sensitive_name" not in result.output
    assert "custom gate and application-owned resource setup" not in result.output


def test_cli_evaluate_names_a_failing_case_and_keeps_completed_results(
    tmp_path: Path,
) -> None:
    project_dir = Path(__file__).parents[1]
    original = (project_dir / "examples/regex-redaction/cases.yaml").read_text()
    cases = tmp_path / "invalid-utf8.yaml"
    cases.write_text(
        original.replace(
            'encoding: utf8\n        value: "ordinary text"',
            'encoding: base64\n        value: "/w=="',
        )
    )

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
    assert "Completed before failure" in result.stdout
    assert "email-is-detected-and-request-is-allowed" in result.stdout
    assert "Evaluation failed for case ordinary-body-is-allowed" in result.stderr
    assert "[body_encoding_invalid]" in result.stderr
    assert '"/w=="' not in result.output


def test_cli_add_gateway_registration_reports_the_result(tmp_path: Path) -> None:
    config = tmp_path / "gateway.toml"
    result = CliRunner().invoke(
        app,
        [
            "add-gateway-registration",
            "--host-ip",
            "192.0.2.10",
            "--config",
            str(config),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Gateway registration is ready" in result.stdout
    assert "Gateway file" in result.stdout
    assert str(config) in "".join(result.stdout.split())
    assert "Registration  egress-gate" in result.stdout
    assert "Endpoint      http://192.0.2.10:50051" in result.stdout
    assert "Created the gateway configuration file" in result.stdout
    assert "Next: Start Egress Gate" in result.stdout


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
    assert "FAIL" in result.stdout
    assert "email-is-detected" in result.stdout
    assert "decision:" in result.stdout
    assert '"deny"' in result.stdout
    assert '"allow"' in result.stdout
    assert "1 passed · 1 failed · 2 total" in result.stdout
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
    assert "Evaluation could not start [invalid_cases_file]" in result.stderr
    assert "valid version 1 YAML test suite" in result.stderr
    assert "YAML aliases" not in result.output


def test_cli_evaluate_explains_an_invalid_timeout() -> None:
    project_dir = Path(__file__).parents[1]
    result = CliRunner().invoke(
        app,
        [
            "evaluate",
            "--policy",
            str(project_dir / "examples/regex-redaction/egress-gate-config.yaml"),
            "--cases",
            str(project_dir / "examples/regex-redaction/cases.yaml"),
            "--timeout-seconds",
            "0",
        ],
        color=True,
    )

    assert result.exit_code == 2
    error_output = Text.from_ansi(result.stderr).plain
    assert "Invalid value for --timeout-seconds" in error_output
    assert "greater than 0" in error_output
