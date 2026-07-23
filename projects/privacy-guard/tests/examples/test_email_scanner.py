from __future__ import annotations

import subprocess
import sys
from pathlib import Path

EXAMPLE_DIRECTORY = Path(__file__).parents[2] / "examples" / "email-scanner"


def test_example_scanner_detects_email_and_server_entry_point_imports() -> None:
    probe = """
from middleware_server import EmailScanner
from privacy_guard.scanners import ScannerConfig

scanner = EmailScanner(
    ScannerConfig(name="example_regex", entity_types=frozenset({"email"}))
)
findings = scanner.scan("safe user@example.com text")
assert len(findings) == 1
assert findings[0].entity == "email"
assert findings[0].start_offset == 5
assert findings[0].end_offset == 21
"""
    subprocess.run([sys.executable, "-c", probe], cwd=EXAMPLE_DIRECTORY, check=True)


def test_example_configuration_targets_its_local_middleware() -> None:
    policy = (EXAMPLE_DIRECTORY / "policy.yaml").read_text()
    gateway = (EXAMPLE_DIRECTORY / "gateway.toml").read_text()
    readme = (EXAMPLE_DIRECTORY / "README.md").read_text()

    assert "middleware: privacy-guard-email-scanner" in policy
    assert 'name = "privacy-guard-email-scanner"' in gateway
    assert 'grpc_endpoint = "http://127.0.0.1:50051"' in gateway
    assert "action: redact" in policy
    assert "entity_types: [email]" in policy
    assert "cd projects/privacy-guard/examples/email-scanner" in readme
    assert "uv run python middleware_server.py" in readme
    assert "--listen 127.0.0.1:50051" in readme
    assert 'openshell-gateway --config "$PWD/gateway.toml"' in readme
    assert "uv run --project" not in readme
