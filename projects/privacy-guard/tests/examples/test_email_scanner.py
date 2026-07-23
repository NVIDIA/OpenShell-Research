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
    gitignore = (EXAMPLE_DIRECTORY / ".gitignore").read_text()
    readme = (EXAMPLE_DIRECTORY / "README.md").read_text()

    assert "middleware: privacy-guard-email-scanner" in policy
    assert 'name = "privacy-guard-email-scanner"' in gateway
    assert 'grpc_endpoint = "http://REPLACE_WITH_HOST_IP:50051"' in gateway
    assert "gateway.local.toml" in gitignore
    assert "action: redact" in policy
    assert "entity_types: [email]" in policy
    assert "cd projects/privacy-guard/examples/email-scanner" in readme
    assert "uv run python middleware_server.py" in readme
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
    assert "--name privacy-guard-email" in readme
    assert "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 claude" in readme
    assert "host: claude.ai" in policy
    assert "Middleware connect failed" in readme
    assert '403 "middleware_failed"' in readme
    assert "--tail" not in readme
    assert "uv run --project" not in readme
