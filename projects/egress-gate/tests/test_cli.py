"""Command-line tests for Egress Gate discovery and policy schema surfaces."""

from __future__ import annotations

import sys
from types import ModuleType

import pytest
from typer.testing import CliRunner

from egress_gate.cli import _load_registry, app
from egress_gate.errors import GateRegistryError
from egress_gate.gates import GateRegistry, create_builtin_registry


def test_cli_gates_describes_the_request_level_builtin() -> None:
    result = CliRunner().invoke(app, ["gates"])

    assert result.exit_code == 0
    assert result.stdout.startswith("regex-body\tsensitive_entity\t")
    assert "request-rules\trequest_rule_match\t" in result.stdout
    assert "engines" not in result.stdout


def test_cli_configuration_schema_exposes_pipeline_only() -> None:
    result = CliRunner().invoke(app, ["configuration-schema"])

    assert result.exit_code == 0
    assert '"pipeline"' in result.stdout
    assert '"default_decision"' in result.stdout
    assert "entity_processing" not in result.stdout
    assert "on_detection" not in result.stdout


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
