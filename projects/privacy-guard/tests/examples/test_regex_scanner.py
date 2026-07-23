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
    readme = (EXAMPLE_DIRECTORY / "README.md").read_text()

    assert "middleware: privacy-guard-regex-scanner" in policy
    assert 'name = "privacy-guard-regex-scanner"' in gateway
    assert 'grpc_endpoint = "http://127.0.0.1:50051"' in gateway
    assert "action: redact" in policy
    assert "entity_types: [email, customer-id]" in policy
    assert "cd projects/privacy-guard/examples/regex-scanner" in readme
    assert "privacy-guard regex" in readme
    assert "--config regex-scanner.yaml" in readme
    assert "--listen 127.0.0.1:50051" in readme
    assert 'openshell-gateway --config "$PWD/gateway.toml"' in readme
    assert "privacy-guard-regex-lab" in readme
    assert "uv run --project" not in readme
