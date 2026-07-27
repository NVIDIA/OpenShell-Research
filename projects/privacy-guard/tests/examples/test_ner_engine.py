"""End-to-end checks for the built-in NER engine example."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import yaml
from google.protobuf import json_format

from privacy_guard.bindings import supervisor_middleware_pb2 as pb2
from privacy_guard.engines import NERModelEntity, NERResources
from privacy_guard.engines.registry import create_builtin_registry
from privacy_guard.service.servicer import PrivacyGuardMiddleware
from privacy_guard.timeout import Timeout

EXAMPLE_DIRECTORY = Path(__file__).parents[2] / "examples" / "ner-engine"


class _ExampleNERModel:
    def predict_entities(
        self,
        text: str,
        *,
        labels: tuple[str, ...],
        threshold: float,
        flat_ner: bool,
        timeout: Timeout,
    ) -> tuple[NERModelEntity, ...]:
        del text, labels, threshold, flat_ner, timeout
        return (
            NERModelEntity(
                label="EMAIL",
                start=8,
                end=24,
                score=0.9,
            ),
        )


def test_ner_example_runs_through_the_middleware_boundary() -> None:
    values = yaml.safe_load(
        (EXAMPLE_DIRECTORY / "privacy-guard-config.yaml").read_text()
    )
    assert isinstance(values, dict)
    config = pb2.HttpRequestEvaluation().config
    json_format.ParseDict(values, config)

    async def evaluate() -> None:
        middleware = PrivacyGuardMiddleware(
            create_builtin_registry(
                ner_resources=NERResources(model=_ExampleNERModel())
            )
        )
        try:
            result = await middleware._evaluate_http_request(
                pb2.HttpRequestEvaluation(
                    phase=pb2.SUPERVISOR_MIDDLEWARE_PHASE_PRE_CREDENTIALS,
                    config=config,
                    body=b"Contact user@example.com",
                )
            )
        finally:
            await middleware.close()

        assert result.decision == pb2.DECISION_ALLOW
        assert result.has_body is False
        assert {finding.label for finding in result.findings} == {
            "email (general-entities)"
        }
        assert all(finding.confidence == "" for finding in result.findings)

    asyncio.run(evaluate())


def test_endpoint_registry_drives_cli_discovery_and_schema() -> None:
    command = str(Path(sys.executable).with_name("privacy-guard"))
    environment = os.environ.copy()
    environment.update(
        {
            "PYTHONPATH": str(EXAMPLE_DIRECTORY),
            "PRIVACY_GUARD_NER_ENDPOINT": "http://model-host:8002/v1/extract",
            "PRIVACY_GUARD_NER_MODEL": "operator-model",
        }
    )
    common = [
        command,
        "--registry-factory",
        "endpoint_registry:create_registry",
    ]

    engines = subprocess.run(
        [*common, "engines"],
        cwd=EXAMPLE_DIRECTORY,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    schema = subprocess.run(
        [*common, "configuration-schema"],
        cwd=EXAMPLE_DIRECTORY,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "regex\tdetect,replace\t" in engines.stdout
    assert "ner\tdetect,replace\t" in engines.stdout
    serialized_schema = json.loads(schema.stdout)
    assert "NEREngineConfig" in serialized_schema["$defs"]
    assert "NERReplacement" in serialized_schema["$defs"]


def test_ner_walkthrough_uses_current_policy_and_registry_contract() -> None:
    policy = yaml.safe_load((EXAMPLE_DIRECTORY / "policy.yaml").read_text())
    config = yaml.safe_load(
        (EXAMPLE_DIRECTORY / "privacy-guard-config.yaml").read_text()
    )
    readme = (EXAMPLE_DIRECTORY / "README.md").read_text()

    assert isinstance(policy, dict)
    assert isinstance(config, dict)
    middleware_config = policy["network_middlewares"]["privacy_guard_ner"]
    assert middleware_config["middleware"] == "privacy-guard-ner"
    assert middleware_config["config"] == config
    stage_config = config["entity_processing"]["stages"][0]["config"]
    assert stage_config["engine"] == "ner"
    assert stage_config["threshold"] == 0.5
    assert stage_config["labels"] == ["person", "email", "phone_number"]
    assert "endpoint_registry:create_registry configuration-schema" in readme
    assert "local_registry:create_registry serve" in readme
    assert 'uv pip install "gliner==0.2.27"' in readme
    assert "/v1/chat/completions" in readme
