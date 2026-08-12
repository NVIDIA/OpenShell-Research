"""Black-box test for the documented Pi admission example."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_example_denies_or_redacts_before_history_and_egress() -> None:
    project_root = Path(__file__).parents[2]
    completed = subprocess.run(
        [sys.executable, "examples/pi-attested-admission/run_example.py"],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    )
    evidence = json.loads(completed.stdout)

    assert evidence["deny"] == {
        "decision": "deny",
        "history_unchanged": True,
        "provider_unchanged": True,
    }
    assert evidence["redact"] == {
        "decision": "replace",
        "history": ["please [REDACTED]"],
        "provider_prompts": ["please [REDACTED]"],
    }
