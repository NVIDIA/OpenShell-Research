"""Privacy Guard command-line application tests."""

from __future__ import annotations

import json
import logging
import re
from importlib.metadata import entry_points
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner, Result

from privacy_guard import cli as cli_module
from privacy_guard.cli import app
from privacy_guard.engines.registry import EngineRegistry, create_builtin_registry
from privacy_guard.service.server import PrivacyGuardServer


def test_cli_help_exposes_server_and_discovery_commands() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    output = _plain_output(result)
    assert "serve" in output
    assert "schema" in output
    assert "engines" in output
    assert "--debug" in output
    assert "--debug-log-content" in output
    assert "--registry-factory" in output
    assert "--config" not in output
    assert "--profile" not in output
    assert "--scanner-name" not in output


def test_console_script_targets_the_cli_module() -> None:
    console_script = next(
        entry_point
        for entry_point in entry_points(group="console_scripts")
        if entry_point.name == "privacy-guard"
    )

    assert console_script.value == "privacy_guard.cli:app"


def test_cli_engines_describes_the_installed_engine() -> None:
    result = CliRunner().invoke(app, ["engines"])

    assert result.exit_code == 0
    assert result.output.startswith("regex\tdetect,replace\t")
    assert "Detect overlapping regex matches" in result.output


def test_cli_schema_prints_the_finalized_discriminated_policy_schema() -> None:
    result = CliRunner().invoke(app, ["schema"])

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
        ("missing-separator", "module:factory"),
        ("operator_engines:missing", "could not be loaded"),
        ("operator_engines:not_callable", "not callable"),
        ("operator_engines:failed", "factory failed"),
        ("operator_engines:wrong_type", "invalid"),
        ("operator_engines:unfinished", "unfinalized"),
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
    )

    assert result.exit_code == 2
    assert reason in _plain_output(result)
    assert "sensitive factory failure" not in _plain_output(result)


def test_cli_serve_adapts_operational_options_to_the_programmatic_server(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls: list[tuple[str, bool]] = []

    def record_run(self: PrivacyGuardServer, listen: str) -> None:
        calls.append((listen, self._middleware._processors._log_request_content))

    monkeypatch.setattr(PrivacyGuardServer, "run", record_run)

    with caplog.at_level(logging.WARNING, logger="privacy_guard.cli"):
        result = CliRunner().invoke(
            app,
            ["--debug-log-content", "serve", "--listen", "127.0.0.1:50052"],
        )

    assert result.exit_code == 0
    assert calls == [("127.0.0.1:50052", True)]
    assert "privacy_guard_request_content_logging_enabled" in caplog.text


def _plain_output(result: Result) -> str:
    return _ANSI_STYLE_PATTERN.sub("", result.output)


_ANSI_STYLE_PATTERN = re.compile(r"\x1b\[[0-9;]*m")
