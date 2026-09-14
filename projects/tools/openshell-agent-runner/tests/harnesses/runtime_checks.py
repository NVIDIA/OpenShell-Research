# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Opt-in Docker integration: run with make test-runtime."""

import os
import subprocess
from pathlib import Path

import pytest

from openshell_agent_runner.profile_init import ThinkingLevel, initialize_profiles

PROJECT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "scenario",
    [
        "plain",
        "schema-retry",
        "read-tool",
        "custom-tool",
        "missing-tool",
        "invalid-cwd",
    ],
)
def test_pi_harness_executes_in_isolation(tmp_path: Path, scenario: str) -> None:
    initialize_profiles(tmp_path, ("code-reviewer",), "qa/model", ThinkingLevel.OFF)
    completed = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--entrypoint",
            "python3",
            "--mount",
            f"type=bind,src={PROJECT}/tests/fixtures/pi-harness-session.py,dst=/qa.py,readonly",
            "--mount",
            f"type=bind,src={tmp_path}/code-reviewer,dst=/profile,readonly",
            "--mount",
            f"type=bind,src={PROJECT}/src/openshell_agent_runner/harnesses/pi/runtime/extensions,dst=/extensions,readonly",
            os.environ.get("OAR_PI_IMAGE", "openshell-agent-runner-pi:test"),
            "/qa.py",
            scenario,
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
