"""End-to-end checks for the custom engine application example."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

EXAMPLE_DIRECTORY = Path(__file__).parents[2] / "examples" / "custom-engine"


def test_custom_engine_runs_through_the_middleware_boundary() -> None:
    probe = r"""
import asyncio
from pathlib import Path

from google.protobuf import json_format
import yaml

from egress_gate.bindings import supervisor_middleware_pb2 as pb2
from egress_gate.service.servicer import EgressGateMiddleware
from custom_engine import create_registry

values = yaml.safe_load(Path("egress-gate-config.yaml").read_text())
assert isinstance(values, dict)
config = pb2.HttpRequestEvaluation().config
json_format.ParseDict(values, config)


async def evaluate() -> None:
    middleware = EgressGateMiddleware(create_registry())
    try:
        result = await middleware._evaluate_http_request(
            pb2.HttpRequestEvaluation(
                phase=pb2.SUPERVISOR_MIDDLEWARE_PHASE_PRE_CREDENTIALS,
                config=config,
                body=b"Discuss Project Cobalt safely.",
            )
        )
    finally:
        await middleware.close()

    assert result.decision == pb2.DECISION_ALLOW
    assert result.has_body is False
    assert result.body == b""
    assert len(result.findings) == 1
    assert result.findings[0].label == (
        "confidential-project (project-names)"
    )


asyncio.run(evaluate())
"""

    subprocess.run(
        [sys.executable, "-c", probe],
        cwd=EXAMPLE_DIRECTORY,
        check=True,
    )


def test_custom_registry_drives_cli_discovery_and_schema() -> None:
    environment = os.environ.copy()
    python_path = str(EXAMPLE_DIRECTORY)
    existing_python_path = environment.get("PYTHONPATH")
    if existing_python_path:
        python_path = os.pathsep.join((python_path, existing_python_path))
    environment["PYTHONPATH"] = python_path
    command = [
        str(Path(sys.executable).with_name("egress-gate")),
        "--registry-factory",
        "custom_engine:create_registry",
    ]

    engines = subprocess.run(
        [*command, "engines"],
        cwd=EXAMPLE_DIRECTORY,
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    schema = subprocess.run(
        [*command, "configuration-schema"],
        cwd=EXAMPLE_DIRECTORY,
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert engines.stdout.startswith("regex\tdetect,replace\t")
    assert "keyword-tool\tdetect\t" in engines.stdout
    serialized_schema = json.loads(schema.stdout)
    assert "RegexEngineConfig" in serialized_schema["$defs"]
    assert "KeywordEngineConfig" in serialized_schema["$defs"]
    keyword_properties = serialized_schema["$defs"]["KeywordEngineConfig"]["properties"]
    assert set(keyword_properties) == {
        "engine",
        "entity",
        "keyword",
    }


def test_openshell_walkthrough_uses_the_custom_registry_and_current_policy() -> None:
    policy = yaml.safe_load((EXAMPLE_DIRECTORY / "policy.yaml").read_text())
    config = yaml.safe_load((EXAMPLE_DIRECTORY / "egress-gate-config.yaml").read_text())
    readme = (EXAMPLE_DIRECTORY / "README.md").read_text()
    implementation = (EXAMPLE_DIRECTORY / "custom_engine.py").read_text()

    assert isinstance(policy, dict)
    assert isinstance(config, dict)
    assert not (EXAMPLE_DIRECTORY / "egress_gate_app.py").exists()
    assert "EngineRegistry(include_builtin_engines=True)" in implementation
    assert "def create_registry() -> EngineRegistry:" in implementation
    middleware_config = policy["network_middlewares"]["egress_gate_detect"]
    assert middleware_config["middleware"] == "egress-gate-custom-engine"
    assert middleware_config["config"] == config
    stage_config = config["entity_processing"]["stages"][0]["config"]
    assert stage_config["engine"] == "keyword-tool"
    assert config["on_detection"]["action"] == "detect"
    assert "--registry-factory custom_engine:create_registry" in readme
    assert "cd projects/egress-gate/examples/custom-engine" in readme
    assert "uv sync --locked" not in readme
    assert "uv run --locked egress-gate" in readme
    assert 'export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"' in readme
    assert "uv run egress-gate add-gateway-registration" in readme
    assert "uv run egress-gate remove-gateway-registration" in readme
    assert "--host-ip YOUR_HOST_IPV4" in readme
    assert "--name egress-gate-custom-engine" in readme
    assert "--config" not in readme
    assert "brew services stop openshell" in readme
    assert "brew services start openshell" in readme
    assert "systemctl --user stop openshell-gateway" in readme
    assert "systemctl --user start openshell-gateway" in readme
    assert "openshell-gateway --config" not in readme
    assert 'sed "s/REPLACE_WITH_HOST_IP/' not in readme
    assert not (EXAMPLE_DIRECTORY / "gateway.toml").exists()
    assert "openshell gateway add" not in readme
    assert "OpenShell `v0.0.90`" in readme
    assert "transformed:false" in readme
