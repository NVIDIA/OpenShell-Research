# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from collections.abc import Sequence
from pathlib import Path

import pytest

from openshell_agent_runner.errors import ConfigurationError
from openshell_agent_runner.runner import RunRequest, resolve_run

REPOSITORY = Path(__file__).resolve().parents[3]
PROFILE = REPOSITORY / ".github/openshell-agents/profiles/dev-note-reviewer"


def request(
    *,
    uploads: Sequence[str] = (),
    environments: Sequence[str] = (),
    gateway: str | None = None,
) -> RunRequest:
    return RunRequest(
        profile_directory=PROFILE,
        task_id="editorial",
        output=Path("/tmp/review.json"),
        uploads=uploads,
        environments=environments,
        gateway=gateway,
    )


def test_native_upload_and_environment_are_forwarded_exactly() -> None:
    resolved = resolve_run(
        request(
            uploads=(".:/workspace/source",),
            environments=("REVIEW_TARGET_PATH=note.md",),
            gateway="openshell",
        )
    )
    assert resolved.uploads == (".:/workspace/source",)
    assert ("--upload", ".:/workspace/source") not in tuple(
        zip(resolved.create_command, resolved.create_command[1:], strict=False)
    )
    assert "provider" not in resolved.create_command
    assert "inference" not in resolved.create_command
    assert "--no-tty" in resolved.create_command
    assert "--no-git-ignore" not in resolved.create_command
    assert ("--approval-mode", "auto") in tuple(
        zip(resolved.create_command, resolved.create_command[1:], strict=False)
    )


def test_conflicting_and_reserved_uploads_are_rejected() -> None:
    for uploads, message in (
        (("evil:/sandbox/artifacts/result",), "reserved for runner resources"),
        (("evil:/sandbox/oar-runtime/schemas",), "reserved for runner resources"),
        (("evil:/sandbox",), "reserved for runner resources"),
        (("evil:/",), "reserved for runner resources"),
        (("evil://sandbox/oar-runtime/schemas",), "canonical absolute paths"),
        (
            ("evil:/workspace/../sandbox/oar-runtime/schemas",),
            "must not contain '..'",
        ),
    ):
        with pytest.raises(ConfigurationError, match=message):
            resolve_run(request(uploads=uploads))


def test_uploads_can_merge_into_the_same_destination() -> None:
    uploads = (".:/workspace/source", ".git:/workspace/source")

    assert resolve_run(request(uploads=uploads)).uploads == uploads


def test_environment_names_are_forwarded_to_native_openshell() -> None:
    resolved = resolve_run(request(environments=("KEYBOARD_LAYOUT=us",)))

    assert "KEYBOARD_LAYOUT=us" in resolved.environments


@pytest.mark.parametrize(
    ("environment", "message"),
    [
        ("BAD.NAME=value", "invalid OpenShell environment name"),
        ("BAD-NAME=value", "invalid OpenShell environment name"),
        ("OPENSHELL_GATEWAY=local", "reserved OPENSHELL_ prefix"),
    ],
)
def test_environment_names_match_openshell_contract(
    environment: str, message: str
) -> None:
    with pytest.raises(ConfigurationError, match=message):
        resolve_run(request(environments=(environment,)))
