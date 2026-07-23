import json
import re
from pathlib import Path

import yaml

from privacy_guard.constants import PATTERN_NAME_METADATA_KEY
from privacy_guard.scanners import RegexScanner

EXAMPLE_DIRECTORY = Path(__file__).parents[2] / "examples" / "regex-scanner"


def test_example_scanner_detects_email_and_customer_id() -> None:
    scanner = RegexScanner.from_yaml(EXAMPLE_DIRECTORY / "regex-scanner.yaml")

    findings = scanner.scan("Email user@example.com, customer CUST-12345678")

    assert [finding.entity for finding in findings] == ["email", "customer-id"]
    pattern_names: list[str] = []
    for finding in findings:
        assert finding.metadata is not None
        pattern_names.append(finding.metadata[PATTERN_NAME_METADATA_KEY])
    assert pattern_names == [
        "common-email",
        "prefixed-customer-id",
    ]


def test_pi_templates_render_for_an_openai_compatible_endpoint() -> None:
    model_template = (EXAMPLE_DIRECTORY / "pi-models.template.json").read_text()
    policy_template = (EXAMPLE_DIRECTORY / "policy.pi.template.yaml").read_text()

    models = json.loads(
        model_template.replace(
            "REPLACE_WITH_MODEL_ENDPOINT", "https://api.example.com/v1"
        ).replace("REPLACE_WITH_MODEL_ID", "example/model")
    )
    policy = yaml.safe_load(
        policy_template.replace("REPLACE_WITH_MODEL_HOST", "api.example.com")
    )

    assert models["providers"]["custom"]["baseUrl"] == "https://api.example.com/v1"
    assert models["providers"]["custom"]["models"][0]["id"] == "example/model"
    assert policy["network_policies"]["pi"]["endpoints"][0]["host"] == "api.example.com"
    assert policy["network_middlewares"]["privacy_guard_redaction"]["endpoints"][
        "include"
    ] == ["api.example.com"]


def test_example_configuration_and_walkthrough_are_aligned() -> None:
    policy = (EXAMPLE_DIRECTORY / "policy.yaml").read_text()
    gateway = (EXAMPLE_DIRECTORY / "gateway.toml").read_text()
    gitignore = (EXAMPLE_DIRECTORY / ".gitignore").read_text()
    readme = (EXAMPLE_DIRECTORY / "README.md").read_text()
    pi_policy = (EXAMPLE_DIRECTORY / "policy.pi.template.yaml").read_text()
    pi_models = json.loads((EXAMPLE_DIRECTORY / "pi-models.template.json").read_text())
    pi_provider = pi_models["providers"]["custom"]

    assert "middleware: privacy-guard-regex-scanner" in policy
    assert 'name = "privacy-guard-regex-scanner"' in gateway
    assert 'grpc_endpoint = "http://REPLACE_WITH_HOST_IP:50051"' in gateway
    assert "gateway.local.toml" in gitignore
    assert "action: redact" in policy
    assert "entity_types: [email, customer-id]" in policy
    assert "cd projects/privacy-guard/examples/regex-scanner" in readme
    assert "privacy-guard regex" in readme
    assert "--config regex-scanner.yaml" in readme
    assert "YOUR_HOST_IP=" in readme
    assert (
        'sed "s/REPLACE_WITH_HOST_IP/$YOUR_HOST_IP/" gateway.toml > gateway.local.toml'
    ) in readme
    assert "grep grpc_endpoint gateway.local.toml" in readme
    assert "--listen 0.0.0.0:50051" in readme
    assert 'openshell-gateway --config "$PWD/gateway.local.toml"' in readme
    assert "openshell gateway add" in readme
    assert "https://127.0.0.1:17670" in readme
    assert "--name openshell" in readme
    assert "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 claude" in readme
    assert "--from pi" in readme
    assert "PI_MODEL_ENDPOINT=\nPI_MODEL_ID=\nexport PI_MODEL_API_KEY=" in readme
    assert "PI_MODEL_HOST=${PI_MODEL_ENDPOINT#*://}" in readme
    assert "--provider privacy-guard-model" in readme
    assert "--credential PI_MODEL_API_KEY" in readme
    assert "--no-git-ignore" in readme
    assert "--upload pi-models.local.json:/sandbox/.pi/agent/models.json" in readme
    assert "--provider custom" in readme
    assert '--model "$PI_MODEL_ID"' in readme
    assert "interactive TUI" in readme
    assert "/usr/bin/pi" in pi_policy
    assert "/usr/local/bin/pi" not in pi_policy
    assert "host: pi.dev" in pi_policy
    assert "host: REPLACE_WITH_MODEL_HOST" in pi_policy
    assert "REPLACE_WITH_MODEL_HOST" not in policy
    assert pi_provider["baseUrl"] == "REPLACE_WITH_MODEL_ENDPOINT"
    assert pi_provider["api"] == "openai-completions"
    assert pi_provider["apiKey"] == "$PI_MODEL_API_KEY"
    assert pi_provider["models"] == [
        {
            "id": "REPLACE_WITH_MODEL_ID",
            "name": "REPLACE_WITH_MODEL_ID",
        }
    ]
    assert "pi-models.local.json" in gitignore
    assert "policy.local.yaml" in gitignore
    assert "auth.json" in gitignore
    assert "Middleware connect failed" in readme
    assert '403 "middleware_failed"' in readme
    assert "--tail" not in readme
    assert "privacy-guard-regex" in readme
    sandbox_names = re.findall(
        r"openshell sandbox create \\\n\s+--name ([a-z0-9-]+)", readme
    )
    assert sandbox_names
    assert all(name.startswith("privacy-guard-") for name in sandbox_names)
    assert all(len(name) <= 19 for name in sandbox_names)
    assert "uv run --project" not in readme
