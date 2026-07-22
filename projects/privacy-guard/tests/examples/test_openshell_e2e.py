from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

EXAMPLE_DIRECTORY = Path(__file__).parents[2] / "examples" / "openshell-e2e"


def test_capture_server_persists_exact_post_body(tmp_path: Path) -> None:
    output_path = tmp_path / "captured.json"
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    process = subprocess.Popen(
        [
            sys.executable,
            str(EXAMPLE_DIRECTORY / "capture_server.py"),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--output",
            str(output_path),
        ]
    )
    request_body = b'{"message":"exact bytes"}'

    try:
        for _ in range(40):
            try:
                request = urllib.request.Request(
                    f"http://127.0.0.1:{port}/capture",
                    data=request_body,
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=1) as response:
                    assert response.read() == b'{"captured":true}'
                break
            except OSError:
                time.sleep(0.05)
        else:
            raise AssertionError("capture server did not become ready")

        assert output_path.read_bytes() == request_body
        assert process.wait(timeout=2) == 0
    finally:
        process.terminate()
        process.wait(timeout=2)


def test_harness_shell_is_valid_and_policy_targets_privacy_guard() -> None:
    subprocess.run(
        ["bash", "-n", str(EXAMPLE_DIRECTORY / "run.sh")],
        check=True,
    )
    policy = (EXAMPLE_DIRECTORY / "policy.yaml").read_text()

    assert "middleware: privacy-guard-e2e" in policy
    assert "on_finding:" in policy
    assert "action: redact" in policy
    assert "entity_types: [email]" in policy
    harness = (EXAMPLE_DIRECTORY / "run.sh").read_text()
    assert 'python "${SCRIPT_DIR}/middleware_server.py"' in harness
    assert "base@sha256:" in harness
    assert "base:latest" not in harness


@pytest.mark.skipif(
    os.environ.get("PRIVACY_GUARD_RUN_OPEN_SHELL_E2E") != "1",
    reason="state-mutating OpenShell harness is explicitly opt-in",
)
def test_opt_in_full_openshell_harness() -> None:
    subprocess.run([str(EXAMPLE_DIRECTORY / "run.sh")], check=True)
