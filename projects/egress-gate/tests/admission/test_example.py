"""Black-box smoke test for the documented Pi admission example."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_documented_example_produces_acceptance_evidence(tmp_path: Path) -> None:
    project_root = Path(__file__).parents[2]
    session_file = tmp_path / "session.jsonl"

    completed = subprocess.run(
        [
            sys.executable,
            "examples/pi-attested-admission/run_example.py",
            "--session-file",
            str(session_file),
        ],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    )
    evidence = json.loads(completed.stdout)

    assert evidence["safe_direct"]["decision"] == "allow"
    assert evidence["safe_direct"]["provider_calls"] == 1
    assert evidence["safe_direct"]["receipt_count"] == 1
    assert evidence["direct_denial"]["session_unchanged"] is True
    assert evidence["direct_denial"]["denied_content_absent"] is True
    assert evidence["direct_denial"]["provider_calls"] == 0
    assert evidence["replacement_turn"]["original_absent"] is True
    assert evidence["replacement_turn"]["replacement_present"] is True
    assert evidence["replacement_turn"]["provider_original_absent"] is True
    assert evidence["replacement_turn"]["provider_replacement_present"] is True
    assert evidence["continuation"]["reason_code"] == "receipt_missing"
    assert evidence["provider"]["receipt_headers_seen"] == 0
    assert session_file.is_file()
