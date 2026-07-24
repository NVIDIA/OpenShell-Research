"""End-to-end checks for the custom engine application example."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

EXAMPLE_DIRECTORY = Path(__file__).parents[2] / "examples" / "custom-engine"


def test_custom_engine_runs_through_the_middleware_boundary() -> None:
    probe = r"""
import asyncio

from google.protobuf import json_format

from custom_engine import create_registry
from privacy_guard.bindings import supervisor_middleware_pb2 as pb2
from privacy_guard.service.servicer import PrivacyGuardMiddleware

values = {
    "entity_processing": {
        "stages": [
            {
                "name": "project-names",
                "config": {
                    "engine": "keyword-tool",
                    "entity": "confidential-project",
                    "keyword": "Project Cobalt",
                    "replacement": {
                        "strategy": "token",
                        "token": "[confidential-project]",
                    },
                },
            }
        ]
    },
    "on_detection": {"action": "replace"},
}
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
    command = [
        sys.executable,
        "-m",
        "privacy_guard.service.server",
        "--registry-factory",
        "custom_engine:create_registry",
    ]

    engines = subprocess.run(
        [*command, "engines"],
        cwd=EXAMPLE_DIRECTORY,
        check=True,
        capture_output=True,
        text=True,
    )
    schema = subprocess.run(
        [*command, "schema"],
        cwd=EXAMPLE_DIRECTORY,
        check=True,
        capture_output=True,
        text=True,
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
    policy = (EXAMPLE_DIRECTORY / "policy.yaml").read_text()
    config = (EXAMPLE_DIRECTORY / "privacy-guard-config.yaml").read_text()
    gateway = (EXAMPLE_DIRECTORY / "gateway.toml").read_text()
    readme = (EXAMPLE_DIRECTORY / "README.md").read_text()

    assert "middleware: privacy-guard-custom-engine" in policy
    assert 'name = "privacy-guard-custom-engine"' in gateway
    assert "engine: keyword-tool" in policy
    assert "engine: keyword-tool" in config
    assert "action: replace" in policy
    assert "--registry-factory custom_engine:create_registry" in readme
    assert "cd projects/privacy-guard/examples/custom-engine" in readme
    assert "transformed:true" in readme
