"""Command-line tests for Egress Gate discovery and policy schema surfaces."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest
import yaml
from rich.text import Text
from typer.testing import CliRunner

from egress_gate.cli import _load_registry, app
from egress_gate.errors import GateRegistryError
from egress_gate.gates import GateRegistry, create_builtin_registry
from egress_gate.gateway_config import MAX_MIDDLEWARE_REGISTRATION_NAME_BYTES


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
    assert "--timeout" in result.stdout
    assert "--host…" not in result.stdout
    assert "--conf…" not in result.stdout


def test_cli_serve_uses_one_concise_processing_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[float, str]] = []

    class FakeServer:
        def __init__(
            self,
            registry: GateRegistry,
            *,
            timeout_middleware_processing: float,
        ) -> None:
            del registry
            self.timeout_middleware_processing = timeout_middleware_processing

        def serve_sync(self, listen: str) -> None:
            calls.append((self.timeout_middleware_processing, listen))

    monkeypatch.setattr(
        "egress_gate.service.server.EgressGateServer",
        FakeServer,
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    result = CliRunner().invoke(
        app,
        ["serve", "--listen", "127.0.0.1:50055", "--timeout", "4500ms"],
    )

    assert result.exit_code == 0, result.output
    assert calls == [(4.5, "127.0.0.1:50055")]

    help_result = CliRunner().invoke(app, ["serve", "--help"])
    serve_help = " ".join(help_result.stdout.split())
    assert "--timeout <str>" in serve_help
    assert "--timeout-seconds" not in serve_help
    assert "s for seconds or ms for milliseconds" in serve_help
    assert "Minimum 10ms" in serve_help
    assert "RPC timeout" in serve_help

    evaluate_help = CliRunner().invoke(app, ["evaluate", "--help"])
    assert evaluate_help.exit_code == 0, evaluate_help.output
    normalized_evaluate_help = " ".join(evaluate_help.stdout.split())
    assert "s for seconds or ms for milliseconds" in normalized_evaluate_help
    assert "Minimum 10ms" in normalized_evaluate_help


def test_cli_serve_rejects_timeout_at_remembered_gateway_ceiling(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "user-config"))
    config = tmp_path / "gateway.toml"
    registration = CliRunner().invoke(
        app,
        [
            "add-gateway-registration",
            "--host-ip",
            "192.0.2.10",
            "--config",
            str(config),
        ],
    )
    assert registration.exit_code == 0, registration.output
    config.write_text(config.read_text().replace('timeout = "30s"', 'timeout = "1s"'))

    result = CliRunner().invoke(app, ["serve", "--timeout", "1s"])

    assert result.exit_code == 2
    assert "must be less than the 1s gateway timeout" in result.stderr
    assert str(config) in result.stderr


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


@pytest.mark.parametrize(
    ("registry_reference", "example_directory", "registration_name"),
    [
        (None, "regex-redaction", "eg-regex"),
        (
            "examples.custom-gate.keyword_gate:registry",
            "custom-gate",
            "egress-function",
        ),
        (
            "examples.class-based-gate.keyword_gate:registry",
            "class-based-gate",
            "egress-class",
        ),
    ],
)
def test_openshell_example_policies_use_valid_gate_configuration(
    monkeypatch: pytest.MonkeyPatch,
    registry_reference: str | None,
    example_directory: str,
    registration_name: str,
) -> None:
    project_dir = Path(__file__).parents[1]
    policy_path = project_dir / f"examples/{example_directory}/policy.yaml"
    policy = yaml.safe_load(policy_path.read_text())
    middleware = next(iter(policy["network_middlewares"].values()))
    standalone_config = yaml.safe_load(
        (policy_path.parent / "egress-gate-config.yaml").read_text()
    )
    assert middleware["middleware"] == registration_name
    assert len(registration_name) <= MAX_MIDDLEWARE_REGISTRATION_NAME_BYTES
    if example_directory == "regex-redaction":
        monkeypatch.chdir(policy_path.parent)
    _load_registry(registry_reference).validate_config(middleware["config"])

    embedded_config = middleware["config"]
    for gate in embedded_config["gates"]:
        pattern_catalog = gate.get("pattern_catalog")
        if isinstance(pattern_catalog, str):
            gate["pattern_catalog"] = yaml.safe_load(
                (policy_path.parent / pattern_catalog).read_text()
            )

    assert embedded_config == standalone_config


@pytest.mark.parametrize(
    ("example_directory", "name"),
    [
        ("regex-redaction", "eg-regex"),
        ("custom-gate", "egress-function"),
        ("class-based-gate", "egress-class"),
    ],
)
def test_example_workflows_use_one_registration_and_sandbox_name(
    example_directory: str,
    name: str,
) -> None:
    project_dir = Path(__file__).parents[1]
    readme = (project_dir / f"examples/{example_directory}/README.md").read_text()
    normalized_readme = " ".join(readme.replace("\\\n", " ").split())

    assert f"--host-ip YOUR_HOST_IPV4 --name {name} --port 50051" in normalized_readme
    assert f"openshell sandbox create --name {name}" in normalized_readme
    assert f"openshell sandbox delete {name}" in readme
    assert f"remove-gateway-registration --name {name}" in normalized_readme
    assert "stop any running OpenShell gateways" in normalized_readme
    assert (
        "A running gateway does not reload middleware registrations"
        in normalized_readme
    )


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


def test_cli_add_gateway_registration_reports_the_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "user-config"))
    config = tmp_path / "gateway.toml"
    result = CliRunner().invoke(
        app,
        [
            "add-gateway-registration",
            "--host-ip",
            "192.0.2.10",
            "--config",
            str(config),
            "--timeout",
            "45s",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Gateway registration is ready" in result.stdout
    assert "Gateway file" in result.stdout
    assert str(config) in "".join(result.stdout.split())
    assert "Registration" in result.stdout
    assert "egress-gate" in result.stdout
    assert "Endpoint" in result.stdout
    assert "http://192.0.2.10:50051" in result.stdout
    assert "Gateway RPC ceiling" in result.stdout
    assert "45s" in result.stdout
    assert 'timeout = "45s"' in config.read_text()
    assert "Created the gateway configuration file" in result.stdout
    assert "Next: Start Egress Gate" in result.stdout


def test_cli_rejects_gateway_timeout_without_processing_headroom(
    tmp_path: Path,
) -> None:
    config = tmp_path / "gateway.toml"

    result = CliRunner().invoke(
        app,
        [
            "add-gateway-registration",
            "--host-ip",
            "192.0.2.10",
            "--config",
            str(config),
            "--timeout",
            "10ms",
        ],
    )

    assert result.exit_code == 2
    assert "gateway timeout must be greater than 10ms" in result.stderr
    assert not config.exists()


def test_cli_removal_forgets_registration_before_later_serve(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    class FakeServer:
        def __init__(
            self,
            registry: GateRegistry,
            *,
            timeout_middleware_processing: float,
        ) -> None:
            del registry, timeout_middleware_processing

        def serve_sync(self, listen: str) -> None:
            calls.append(listen)

    monkeypatch.setattr("egress_gate.service.server.EgressGateServer", FakeServer)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "user-config"))
    config = tmp_path / "gateway.toml"
    add_result = CliRunner().invoke(
        app,
        [
            "add-gateway-registration",
            "--host-ip",
            "192.0.2.10",
            "--config",
            str(config),
        ],
    )
    assert add_result.exit_code == 0, add_result.output

    remove_result = CliRunner().invoke(
        app,
        [
            "remove-gateway-registration",
            "--name",
            "egress-gate",
            "--config",
            str(config),
        ],
    )
    assert remove_result.exit_code == 0, remove_result.output

    serve_result = CliRunner().invoke(app, ["serve"])

    assert serve_result.exit_code == 0, serve_result.output
    assert calls == ["127.0.0.1:50051"]


def test_cli_lists_gateway_registration_names_for_removal(tmp_path: Path) -> None:
    config = tmp_path / "gateway.toml"
    config.write_text(
        "[openshell]\n"
        "version = 1\n\n"
        "[[openshell.supervisor.middleware]]\n"
        'name = "eg-regex"\n'
        'grpc_endpoint = "http://192.0.2.10:50051"\n'
        'timeout = "30s"\n\n'
        "[[openshell.supervisor.middleware]]\n"
        'name = "other-service"\n'
        'grpc_endpoint = "http://192.0.2.20:9000"\n'
    )

    result = CliRunner().invoke(
        app,
        ["list-gateway-registrations", "--config", str(config)],
    )

    assert result.exit_code == 0, result.output
    assert "OpenShell middleware registrations" in result.stdout
    assert "eg-regex" in result.stdout
    assert "http://192.0.2.10:50051" in result.stdout
    assert "Gateway RPC ceiling" in result.stdout
    assert "30s" in result.stdout
    assert "other-service" in result.stdout
    assert "remove-gateway-registration --name NAME" in result.stdout


def test_cli_lists_no_registrations_when_gateway_config_is_missing(
    tmp_path: Path,
) -> None:
    result = CliRunner().invoke(
        app,
        [
            "list-gateway-registrations",
            "--config",
            str(tmp_path / "missing.toml"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "No middleware registrations found." in result.stdout


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
            "--timeout",
            "9ms",
        ],
        color=True,
    )

    assert result.exit_code == 2
    error_output = Text.from_ansi(result.stderr).plain
    assert "Invalid value for --timeout" in error_output
    assert "at least 10ms" in error_output
