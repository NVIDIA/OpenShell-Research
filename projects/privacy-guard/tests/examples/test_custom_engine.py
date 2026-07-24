"""End-to-end checks for the custom engine application example."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import yaml

EXAMPLE_DIRECTORY = Path(__file__).parents[2] / "examples" / "custom-engine"


def test_custom_engine_runs_through_the_middleware_boundary() -> None:
    probe = r"""
import asyncio
from pathlib import Path

from google.protobuf import json_format
import yaml

from privacy_guard.bindings import supervisor_middleware_pb2 as pb2
from privacy_guard.service.servicer import PrivacyGuardMiddleware
from privacy_guard_app import create_registry

values = yaml.safe_load(Path("privacy-guard-config.yaml").read_text())
assert isinstance(values, dict)
config = pb2.HttpRequestEvaluation().config
json_format.ParseDict(values, config)


async def evaluate() -> None:
    middleware = PrivacyGuardMiddleware(create_registry())
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
    assert result.has_body is True
    assert result.body == b"Discuss [confidential-project] safely."
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
        str(Path(sys.executable).with_name("privacy-guard")),
        "--registry-factory",
        "privacy_guard_app:create_registry",
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
        [*command, "schema"],
        cwd=EXAMPLE_DIRECTORY,
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert engines.stdout.startswith("keyword-tool\tdetect,replace\t")
    assert "regex" not in engines.stdout
    serialized_schema = json.loads(schema.stdout)
    assert "KeywordEngineConfig" in serialized_schema["$defs"]
    keyword_properties = serialized_schema["$defs"]["KeywordEngineConfig"]["properties"]
    assert set(keyword_properties) == {
        "replacement",
        "engine",
        "entity",
        "keyword",
    }


def test_openshell_walkthrough_uses_the_custom_registry_and_current_policy() -> None:
    policy = yaml.safe_load((EXAMPLE_DIRECTORY / "policy.yaml").read_text())
    config = yaml.safe_load(
        (EXAMPLE_DIRECTORY / "privacy-guard-config.yaml").read_text()
    )
    gateway = tomllib.loads((EXAMPLE_DIRECTORY / "gateway.toml").read_text())
    readme = (EXAMPLE_DIRECTORY / "README.md").read_text()

    assert isinstance(policy, dict)
    assert isinstance(config, dict)
    middleware_config = policy["network_middlewares"]["privacy_guard_replace"]
    assert middleware_config["middleware"] == "privacy-guard-custom-engine"
    assert middleware_config["config"] == config
    middleware = gateway["openshell"]["supervisor"]["middleware"]
    assert middleware == [
        {
            "name": "privacy-guard-custom-engine",
            "grpc_endpoint": "http://REPLACE_WITH_HOST_IP:50051",
            "max_body_bytes": 4_194_304,
            "timeout": "5s",
        }
    ]
    stage_config = config["entity_processing"]["stages"][0]["config"]
    assert stage_config["engine"] == "keyword-tool"
    assert config["on_detection"]["action"] == "replace"
    assert "--registry-factory privacy_guard_app:create_registry" in readme
    assert "cd projects/privacy-guard/examples/custom-engine" in readme
    assert 'export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"' in readme
    assert "openshell gateway select openshell" in readme
    assert "openshell gateway add" not in readme
    assert "OpenShell `v0.0.90`" in readme
    assert "transformed:true" in readme
