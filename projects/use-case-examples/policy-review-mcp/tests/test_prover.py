import os
import stat
from pathlib import Path

import pytest

from policy_review_mcp.prover import ProverConfig, check_policy_boundary


def _fake_prover(tmp_path: Path, result: str = "within_boundary", exit_code: int = 0) -> Path:
    executable = tmp_path / "openshell-prover"
    counterexample = None
    if result == "exceeds_boundary":
        counterexample = {"domain": "filesystem", "access": "write", "path": "/workspace"}
    payload = {
        "schema_version": 1,
        "prover_version": "0.0.test",
        "check": "boundary",
        "coverage": {"domains": ["filesystem", "network_rest"]},
        "result": result,
        "exit_code": exit_code,
        "inputs": {"candidate": "snapshot", "boundary": "snapshot"},
        "counterexample": counterexample,
        "reason_code": None,
        "reason": None,
    }
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        f"print(json.dumps({payload!r}))\n"
        f"raise SystemExit({exit_code})\n"
    )
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    return executable


def test_prover_invocation_preserves_fingerprints_and_contract(tmp_path: Path) -> None:
    boundary = tmp_path / "boundary.yaml"
    boundary.write_text("version: 1\n")
    config = ProverConfig(str(_fake_prover(tmp_path)), boundary)
    report = check_policy_boundary("version: 1\n", config)
    assert report["status"] == "complete"
    assert report["within_boundary"] is True
    assert len(report["candidate_sha256"]) == 64
    assert len(report["boundary_sha256"]) == 64


def test_inconsistent_exit_code_is_adapter_error(tmp_path: Path) -> None:
    boundary = tmp_path / "boundary.yaml"
    boundary.write_text("version: 1\n")
    config = ProverConfig(
        str(_fake_prover(tmp_path, result="within_boundary", exit_code=1)), boundary
    )
    report = check_policy_boundary("version: 1\n", config)
    assert report["status"] == "adapter_error"
    assert report["reason_code"] == "invalid_output_contract"


@pytest.mark.parametrize(
    ("candidate", "within"),
    [
        ("candidate-broad.yaml", True),
        ("candidate-read.yaml", True),
        ("candidate-comment.yaml", True),
        ("candidate-outside-boundary.yaml", False),
        ("candidate-code-review.yaml", True),
    ],
)
def test_real_openshell_prover_fixtures_when_available(candidate: str, within: bool) -> None:
    executable = os.environ.get("OPENSHELL_PROVER")
    if not executable:
        pytest.skip("set OPENSHELL_PROVER to run pinned CLI integration")
    fixtures = Path(__file__).parents[1] / "demo/fixtures"
    config = ProverConfig(executable, fixtures / "boundary.yaml")
    report = check_policy_boundary((fixtures / candidate).read_text(), config)
    assert report["status"] == "complete"
    assert report["within_boundary"] is within
