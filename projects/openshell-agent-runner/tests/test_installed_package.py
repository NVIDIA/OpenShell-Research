# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Contracts for the wheel installed by CI."""

from importlib.metadata import distribution
from importlib.resources import files


def test_distribution_exposes_both_cli_entry_points() -> None:
    package = distribution("openshell-agent-runner")

    assert package.version
    scripts = {
        entry_point.name: entry_point.value
        for entry_point in package.entry_points
        if entry_point.group == "console_scripts"
    }
    assert scripts == {
        "oar": "openshell_agent_runner.cli:app",
        "openshell-agent-runner": "openshell_agent_runner.cli:app",
    }


def test_distribution_contains_runtime_and_profile_resources() -> None:
    package = files("openshell_agent_runner")
    required_resources = (
        "harnesses/pi/runtime/image/Dockerfile",
        "harnesses/pi/runtime/image/exec.sh",
        "harnesses/pi/runtime/extensions/submit-result.ts",
        "harnesses/pi/runtime/extensions/validate-tools.ts",
        "profiles/code-reviewer/profile.yaml",
        "profiles/technical-writing-reviewer/profile.yaml",
    )

    for relative_path in required_resources:
        assert package.joinpath(relative_path).is_file(), relative_path
