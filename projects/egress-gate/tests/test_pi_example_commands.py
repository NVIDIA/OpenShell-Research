from __future__ import annotations

import os
import subprocess
from pathlib import Path


def test_pi_example_can_print_every_command_without_running_it(
    tmp_path: Path,
) -> None:
    project_dir = Path(__file__).parents[1]
    script = project_dir / "examples/pi-attested-admission/demo.sh"
    pi_repo = tmp_path / "pi"
    package_dir = pi_repo / "packages/coding-agent"
    package_dir.mkdir(parents=True)
    (package_dir / "package.json").write_text('{"version":"1.2.3"}')
    openshell_repo = tmp_path / "OpenShell"
    pack_dir = tmp_path / "pack"
    runtime_dir = tmp_path / "runtime"
    environment = os.environ | {
        "PI_REPO": str(pi_repo),
        "OPENSHELL_REPO": str(openshell_repo),
        "EGRESS_GATE_HOST_IP": "192.0.2.10",
        "PI_EGRESS_PACK_DIR": str(pack_dir),
        "PI_EGRESS_RUNTIME_DIR": str(runtime_dir),
    }

    result = subprocess.run(
        ["bash", str(script), "--print", "all"],
        check=True,
        capture_output=True,
        env=environment,
        text=True,
    )

    assert "npm run build" in result.stdout
    assert "add-gateway-registration" in result.stdout
    assert "egress-gate --debug serve" in result.stdout
    assert "mise run gateway" in result.stdout
    assert "provider create" in result.stdout
    assert "sandbox create" in result.stdout
    assert "sandbox exec" in result.stdout
    assert "REDACTED" in result.stdout
    assert "DENY_THIS" in result.stdout
    assert "REDACT_THIS" in result.stdout
    assert "sandbox delete" in result.stdout
    assert result.stderr == ""
    assert not pack_dir.exists()
    assert not runtime_dir.exists()
