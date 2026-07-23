from pathlib import Path

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


def test_example_configuration_and_walkthrough_are_aligned() -> None:
    policy = (EXAMPLE_DIRECTORY / "policy.yaml").read_text()
    gateway = (EXAMPLE_DIRECTORY / "gateway.toml").read_text()
    gitignore = (EXAMPLE_DIRECTORY / ".gitignore").read_text()
    readme = (EXAMPLE_DIRECTORY / "README.md").read_text()

    assert "middleware: privacy-guard-regex-scanner" in policy
    assert 'name = "privacy-guard-regex-scanner"' in gateway
    assert 'grpc_endpoint = "http://REPLACE_WITH_HOST_IP:50051"' in gateway
    assert "gateway.local.toml" in gitignore
    assert (EXAMPLE_DIRECTORY / "generate_gateway_config.py").is_file()
    assert "action: redact" in policy
    assert "entity_types: [email, customer-id]" in policy
    assert "cd projects/privacy-guard/examples/regex-scanner" in readme
    assert "privacy-guard regex" in readme
    assert "--config regex-scanner.yaml" in readme
    assert "uv run python generate_gateway_config.py" in readme
    assert "--listen 0.0.0.0:50051" in readme
    assert 'openshell-gateway --config "$PWD/gateway.local.toml"' in readme
    assert "openshell gateway add" in readme
    assert "https://127.0.0.1:17670" in readme
    assert "--name openshell" in readme
    assert "privacy-guard-regex-lab" in readme
    assert "uv run --project" not in readme
