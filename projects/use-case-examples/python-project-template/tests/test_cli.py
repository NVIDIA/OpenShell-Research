# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the example command-line interface."""

from click.testing import CliRunner

from python_project_template.cli import greeting, main


def test_greeting() -> None:
    """Build a greeting from the supplied name."""
    assert greeting("OpenShell") == "Hello, OpenShell!"


def test_main() -> None:
    """Expose the greeting through the console entry point."""
    result = CliRunner().invoke(main, ["--name", "OpenShell"])

    assert result.exit_code == 0
    assert result.output == "Hello, OpenShell!\n"
