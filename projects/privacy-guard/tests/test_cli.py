"""Privacy Guard command-line application tests."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from importlib.metadata import entry_points
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner, Result

from privacy_guard import cli as cli_module
from privacy_guard.cli import app
from privacy_guard.engines.registry import EngineRegistry, create_builtin_registry
from privacy_guard.errors import ErrorCode, PrivacyGuardError
from privacy_guard.logging import reset_logging
from privacy_guard.service.server import PrivacyGuardServer


@pytest.fixture(autouse=True)
def _reset_cli_logging() -> Iterator[None]:
    yield
    reset_logging()


def test_cli_help_exposes_server_and_discovery_commands() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    output = _plain_output(result)
    assert "serve" in output
    assert "configuration-schema" in output
    assert "add-gateway-registration" in output
    assert "remove-gateway-registration" in output
    assert "engines" in output
    assert "--debug" in output
    assert "--debug-log-content" in output
    assert "--registry-factory" in output
    assert "--config" not in output
    assert "--profile" not in output
    assert "--scanner-name" not in output


def test_cli_add_gateway_registration_help_requires_an_explicit_host_ip() -> None:
    result = CliRunner().invoke(
        app,
        ["add-gateway-registration", "--help"],
        terminal_width=240,
    )

    assert result.exit_code == 0
    output = _normalized_output(result)
    assert "--host-ip" in output
    assert "required" in output.lower()
    assert "Non-loopback IPv4" in output
    assert "$OPENSHELL_GATEWAY_CONFIG" in output
    assert "$XDG_CONFIG_HOME/openshell" in output
    assert "1-128 ASCII bytes" in output
    assert "restart the OpenShell gateway" not in output


def test_cli_add_gateway_registration_updates_the_default_xdg_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    result = CliRunner().invoke(
        app,
        ["add-gateway-registration", "--host-ip", "192.168.1.20"],
    )

    assert result.exit_code == 0
    config_path = tmp_path / "openshell" / "gateway.toml"
    assert config_path.exists()
    output = _plain_output(result)
    assert f"Created {config_path}" in output
    assert "Registered privacy-guard at http://192.168.1.20:50051" in output
    assert "start Privacy Guard, then restart the OpenShell gateway" in output


@pytest.mark.parametrize("host_ip", ["host.openshell.internal", "127.0.0.1", "0.0.0.0"])
def test_cli_add_gateway_registration_rejects_unusable_host_ip(host_ip: str) -> None:
    result = CliRunner().invoke(
        app,
        ["add-gateway-registration", "--host-ip", host_ip],
        terminal_width=240,
    )

    assert result.exit_code == 2
    output = _normalized_output(result)
    assert "--host-ip" in output
    assert "IPv4 address" in output


@pytest.mark.parametrize(
    "name",
    [
        "a" * 129,
        "privacy guard",
        "openshell/privacy-guard",
    ],
)
def test_cli_add_gateway_registration_rejects_invalid_registration_name(
    name: str,
) -> None:
    result = CliRunner().invoke(
        app,
        [
            "add-gateway-registration",
            "--host-ip",
            "192.168.1.20",
            "--name",
            name,
        ],
        terminal_width=240,
    )

    assert result.exit_code == 2
    assert "--name" in _normalized_output(result)


def test_cli_add_gateway_registration_reports_invalid_existing_config(
    tmp_path: Path,
) -> None:
    path = tmp_path / "gateway.toml"
    path.write_text("not valid TOML")

    result = CliRunner().invoke(
        app,
        [
            "add-gateway-registration",
            "--host-ip",
            "192.168.1.20",
            "--config",
            str(path),
        ],
    )

    assert result.exit_code == 1
    output = _plain_output(result)
    assert "Could not configure the OpenShell gateway" in output
    assert "not valid TOML" in output
    assert path.read_text() == "not valid TOML"


def test_cli_remove_gateway_registration_removes_the_named_registration(
    tmp_path: Path,
) -> None:
    path = tmp_path / "gateway.toml"
    path.write_text(
        "[openshell]\n"
        "version = 1\n\n"
        "[[openshell.supervisor.middleware]]\n"
        'name = "privacy-guard-regex"\n'
        'grpc_endpoint = "http://192.168.1.20:50051"\n'
    )

    result = CliRunner().invoke(
        app,
        [
            "remove-gateway-registration",
            "--name",
            "privacy-guard-regex",
            "--config",
            str(path),
        ],
    )

    assert result.exit_code == 0
    output = _plain_output(result)
    assert f"Removed privacy-guard-regex from {path}" in output
    assert "restart the OpenShell gateway" in output
    assert "privacy-guard-regex" not in path.read_text()


def test_cli_remove_gateway_registration_requires_a_name() -> None:
    result = CliRunner().invoke(
        app,
        ["remove-gateway-registration"],
        terminal_width=240,
    )

    assert result.exit_code == 2
    output = _normalized_output(result)
    assert "--name" in output
    assert "missing option" in output.lower()


def test_cli_remove_gateway_registration_reports_absent_name(
    tmp_path: Path,
) -> None:
    path = tmp_path / "gateway.toml"
    path.write_text("[openshell]\nversion = 1\n")

    result = CliRunner().invoke(
        app,
        [
            "remove-gateway-registration",
            "--name",
            "privacy-guard-regex",
            "--config",
            str(path),
        ],
    )

    assert result.exit_code == 0
    assert (
        f"No registration named privacy-guard-regex found in {path}"
        in _plain_output(result)
    )


def test_console_script_targets_the_cli_module() -> None:
    console_script = next(
        entry_point
        for entry_point in entry_points(group="console_scripts")
        if entry_point.name == "privacy-guard"
    )

    assert console_script.value == "privacy_guard.cli:app"


def test_cli_serve_help_explains_the_processing_timeout() -> None:
    result = CliRunner().invoke(app, ["serve", "--help"])

    assert result.exit_code == 0
    output = _normalized_output(result)
    assert "--timeout-seconds" in output
    assert "shared by all processing stages" in output
    assert "at most 30" in output


def test_cli_engines_describes_the_installed_engine() -> None:
    result = CliRunner().invoke(app, ["engines"])

    assert result.exit_code == 0
    assert result.output.startswith("regex\tdetect,replace\t")
    description = (
        "Detect every regex match, including matches that share input characters"
    )
    assert description in result.output


def test_cli_configuration_schema_prints_finalized_policy_schema() -> None:
    result = CliRunner().invoke(app, ["configuration-schema"])

    assert result.exit_code == 0
    schema = json.loads(result.output)
    serialized = json.dumps(schema, sort_keys=True)
    assert '"propertyName": "engine"' in serialized
    assert '"regex"' in serialized
    assert '"on_detection"' in serialized


def test_cli_loads_one_finalized_operator_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = create_builtin_registry()
    factory_calls = 0

    def create_registry() -> EngineRegistry:
        nonlocal factory_calls
        factory_calls += 1
        return registry

    monkeypatch.setattr(
        cli_module.importlib,
        "import_module",
        lambda module_name: (
            SimpleNamespace(create_registry=create_registry)
            if module_name == "operator_engines"
            else None
        ),
    )

    result = CliRunner().invoke(
        app,
        ["--registry-factory", "operator_engines:create_registry", "engines"],
    )

    assert result.exit_code == 0
    assert factory_calls == 1
    assert result.output.startswith("regex\tdetect,replace\t")


@pytest.mark.parametrize(
    ("factory_reference", "reason"),
    [
        ("missing-separator", "my_engines:create_registry"),
        ("operator_engines:missing", "Verify the module:factory reference"),
        ("operator_engines:not_callable", "Export a callable"),
        ("operator_engines:failed", "Run the factory directly"),
        ("operator_engines:wrong_type", "Return an EngineRegistry"),
        ("operator_engines:unfinished", "Call finalize()"),
    ],
)
def test_cli_rejects_invalid_registry_factories(
    monkeypatch: pytest.MonkeyPatch,
    factory_reference: str,
    reason: str,
) -> None:
    def fail() -> EngineRegistry:
        raise RuntimeError("sensitive factory failure")

    module = SimpleNamespace(
        not_callable=object(),
        failed=fail,
        wrong_type=lambda: object(),
        unfinished=lambda: EngineRegistry(),
    )
    monkeypatch.setattr(
        cli_module.importlib,
        "import_module",
        lambda _: module,
    )

    result = CliRunner().invoke(
        app,
        ["--registry-factory", factory_reference, "engines"],
        terminal_width=240,
    )

    assert result.exit_code == 2
    assert reason in _normalized_output(result)
    assert "sensitive factory failure" not in _plain_output(result)


def test_cli_explains_registry_module_import_failures_without_leaking_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_import(_: str) -> object:
        raise RuntimeError("sensitive import failure")

    monkeypatch.setattr(cli_module.importlib, "import_module", fail_import)

    result = CliRunner().invoke(
        app,
        ["--registry-factory", "operator_engines:create_registry", "engines"],
        terminal_width=240,
    )

    assert result.exit_code == 2
    output = _normalized_output(result)
    assert "Registry module could not be imported" in output
    assert "import the module directly with content-safe diagnostics" in output
    assert "sensitive import failure" not in _plain_output(result)


def test_cli_serve_adapts_operational_options_to_the_programmatic_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, float, bool]] = []

    def record_serve_sync(self: PrivacyGuardServer, listen: str) -> None:
        calls.append(
            (
                listen,
                self._middleware._policy._timeout_seconds,
                self._middleware._policy._log_request_content,
            )
        )

    monkeypatch.setattr(PrivacyGuardServer, "serve_sync", record_serve_sync)

    result = CliRunner().invoke(
        app,
        [
            "--debug-log-content",
            "serve",
            "--listen",
            "127.0.0.1:50052",
            "--timeout-seconds",
            "4.5",
        ],
    )

    assert result.exit_code == 0
    assert calls == [("127.0.0.1:50052", 4.5, True)]
    assert "privacy_guard_request_content_logging_enabled" in _plain_output(result)


def test_cli_serve_prints_cataloged_startup_errors_without_a_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_safely(_: PrivacyGuardServer, listen: str) -> None:
        del listen
        raise PrivacyGuardError(ErrorCode.SERVER_BIND_FAILED)

    monkeypatch.setattr(PrivacyGuardServer, "serve_sync", fail_safely)

    result = CliRunner().invoke(
        app,
        ["serve", "--listen", "sensitive-listen-address"],
    )

    assert result.exit_code == 1
    assert "[server_bind_failed]" in result.output
    assert "Choose an available listen address and port, then retry" in result.output
    assert "sensitive-listen-address" not in result.output
    assert "Traceback" not in result.output


@pytest.mark.parametrize("timeout_seconds", ["0", "31", "nan"])
def test_cli_rejects_invalid_processing_timeout(timeout_seconds: str) -> None:
    result = CliRunner().invoke(
        app,
        ["serve", "--timeout-seconds", timeout_seconds],
        terminal_width=240,
    )

    assert result.exit_code == 2
    output = _normalized_output(result)
    assert "--timeout-seconds" in output
    assert "greater than 0 and at most 30" in output


def _normalized_output(result: Result) -> str:
    return " ".join(_plain_output(result).replace("│", " ").split())


def _plain_output(result: Result) -> str:
    return _ANSI_STYLE_PATTERN.sub("", result.output)


_ANSI_STYLE_PATTERN = re.compile(r"\x1b\[[0-9;]*m")
