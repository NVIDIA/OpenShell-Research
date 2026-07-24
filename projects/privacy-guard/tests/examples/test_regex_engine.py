"""End-to-end checks for the built-in RegexEngine example."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import yaml
from google.protobuf import json_format

from privacy_guard.bindings import supervisor_middleware_pb2 as pb2
from privacy_guard.engines import RegexPatternCatalog
from privacy_guard.service.server import create_builtin_registry
from privacy_guard.service.servicer import PrivacyGuardMiddleware

EXAMPLE_DIRECTORY = Path(__file__).parents[2] / "examples" / "regex-engine"


def test_regex_example_runs_through_the_middleware_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(EXAMPLE_DIRECTORY)
    values = yaml.safe_load(
        (EXAMPLE_DIRECTORY / "privacy-guard-config.yaml").read_text()
    )
    assert isinstance(values, dict)
    config = pb2.HttpRequestEvaluation().config
    json_format.ParseDict(values, config)

    async def evaluate() -> None:
        middleware = PrivacyGuardMiddleware(create_builtin_registry())
        try:
            result = await middleware._evaluate_http_request(
                pb2.HttpRequestEvaluation(
                    phase=pb2.SUPERVISOR_MIDDLEWARE_PHASE_PRE_CREDENTIALS,
                    config=config,
                    body=(b"Contact user@example.com about customer CUST-12345678."),
                )
            )
        finally:
            await middleware.close()

        assert result.decision == pb2.DECISION_ALLOW
        assert result.has_body is True
        assert result.body == (b"Contact [email] about customer [customer-id].")
        assert {finding.label for finding in result.findings} == {
            "email (identifiers)",
            "customer-id (identifiers)",
        }

    asyncio.run(evaluate())


def test_builtin_registry_drives_documented_cli_discovery_and_schema() -> None:
    command = str(Path(sys.executable).with_name("privacy-guard"))
    engines = subprocess.run(
        [command, "engines"],
        cwd=EXAMPLE_DIRECTORY,
        check=True,
        capture_output=True,
        text=True,
    )
    schema = subprocess.run(
        [command, "schema"],
        cwd=EXAMPLE_DIRECTORY,
        check=True,
        capture_output=True,
        text=True,
    )

    assert engines.stdout.startswith("regex\tdetect,replace\t")
    serialized_schema = json.loads(schema.stdout)
    assert "RegexEngineConfig" in serialized_schema["$defs"]
    assert "RegexPatternCatalog" in serialized_schema["$defs"]
    assert "RegexReplacement" in serialized_schema["$defs"]


def test_regex_walkthrough_uses_current_policy_and_gateway_schema() -> None:
    policy = yaml.safe_load((EXAMPLE_DIRECTORY / "policy.yaml").read_text())
    config = yaml.safe_load(
        (EXAMPLE_DIRECTORY / "privacy-guard-config.yaml").read_text()
    )
    catalog = yaml.safe_load((EXAMPLE_DIRECTORY / "patterns.yaml").read_text())
    gateway = tomllib.loads((EXAMPLE_DIRECTORY / "gateway.toml").read_text())
    readme = (EXAMPLE_DIRECTORY / "README.md").read_text()

    assert isinstance(policy, dict)
    assert isinstance(config, dict)
    assert isinstance(catalog, dict)
    middleware = gateway["openshell"]["supervisor"]["middleware"]
    assert middleware == [
        {
            "name": "privacy-guard-regex",
            "grpc_endpoint": "http://REPLACE_WITH_HOST_IP:50051",
            "max_body_bytes": 4_194_304,
            "timeout": "5s",
        }
    ]
    middleware_config = policy["network_middlewares"]["privacy_guard_replace"]
    assert middleware_config["middleware"] == "privacy-guard-regex"
    assert middleware_config["config"] == config
    assert config["on_detection"]["action"] == "replace"
    stage_config = config["entity_processing"]["stages"][0]["config"]
    assert stage_config["engine"] == "regex"
    assert stage_config["pattern_catalog"] == "patterns.yaml"
    RegexPatternCatalog.model_validate(catalog)
    assert "uv run privacy-guard serve --listen 0.0.0.0:50051" in readme
    assert "openshell gateway select openshell" in readme
    assert "openshell gateway add" not in readme
    assert "OpenShell `v0.0.90`" in readme
    assert "transformed:true" in readme
