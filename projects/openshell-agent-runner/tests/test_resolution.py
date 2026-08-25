# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import shutil
from collections.abc import Sequence
from pathlib import Path

import pytest
import yaml

from openshell_agent_runner.errors import ConfigurationError
from openshell_agent_runner.runner import RunRequest, resolve_run

REPOSITORY = Path(__file__).resolve().parents[3]
PROFILE = REPOSITORY / ".github/openshell-agents/profiles/dev-note-reviewer"
CODE_REVIEWER = (
    REPOSITORY
    / "projects/openshell-agent-runner/src/openshell_agent_runner/profiles/code-reviewer"
)
TECHNICAL_WRITING_REVIEWER = (
    REPOSITORY
    / "projects/openshell-agent-runner/src/openshell_agent_runner/profiles/technical-writing-reviewer"
)


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


def review_request(
    task_id: str,
    input_path: Path | None,
    *,
    environments: Sequence[str] = (),
    prompt_variables: Sequence[str] = (),
) -> RunRequest:
    return RunRequest(
        profile_directory=(
            CODE_REVIEWER
            if task_id == "review-repository"
            else TECHNICAL_WRITING_REVIEWER
        ),
        task_id=task_id,
        output=Path("/tmp/review.md"),
        input_path=input_path,
        environments=environments,
        prompt_variables=prompt_variables,
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
    ("filename", "sandbox_filename"),
    [
        ("proposal.md", "document.md"),
        ("notes.txt", "document.txt"),
        ("config.json", "document.json"),
        ("README", "document"),
    ],
)
def test_document_input_preserves_ordinary_file_extension(
    tmp_path: Path, filename: str, sandbox_filename: str
) -> None:
    document = tmp_path / filename
    document.write_text("Review me.\n")

    resolved = resolve_run(review_request("review-document", document))

    assert resolved.uploads == (
        f"{document.resolve()}:/workspace/input/{sandbox_filename}",
    )
    assert resolved.environments == ("REPOSITORY_ROOT=/workspace/input",)
    assert resolved.input is not None
    assert resolved.input.source == document.resolve()
    assert resolved.input.sandbox_path == f"/workspace/input/{sandbox_filename}"
    assert resolved.input.name == filename
    assert dict(resolved.prompt_variables)["oar.input_path"] == (
        f"/workspace/input/{sandbox_filename}"
    )
    assert dict(resolved.prompt_variables)["oar.input_name"] == filename


def test_repository_input_is_uploaded_and_used_as_working_directory(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "example-project"
    repository.mkdir()

    resolved = resolve_run(review_request("review-repository", repository))

    assert resolved.uploads == (f"{repository.resolve()}:/workspace/input",)
    assert resolved.environments == (
        "REPOSITORY_ROOT=/workspace/input/example-project",
    )
    assert resolved.input is not None
    assert resolved.input.source == repository.resolve()
    assert resolved.input.sandbox_path == "/workspace/input/example-project"
    assert resolved.input.name == "example-project"
    assert dict(resolved.prompt_variables)["oar.input_path"] == (
        "/workspace/input/example-project"
    )


def test_input_name_preserves_the_caller_visible_symlink_name(tmp_path: Path) -> None:
    repository = tmp_path / "actual-project"
    repository.mkdir()
    input_path = tmp_path / "review-me"
    input_path.symlink_to(repository, target_is_directory=True)

    resolved = resolve_run(review_request("review-repository", input_path))

    assert resolved.input is not None
    assert resolved.input.source == repository.resolve()
    assert resolved.input.sandbox_path == "/workspace/input/actual-project"
    assert resolved.input.name == "review-me"
    assert dict(resolved.prompt_variables)["oar.input_name"] == "review-me"


def test_multiple_prompt_variables_override_task_defaults(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()

    resolved = resolve_run(
        review_request(
            "review-repository",
            repository,
            prompt_variables=(
                "focus=src/auth and tests/auth",
                "context=Pre-release security review",
            ),
        )
    )

    values = dict(resolved.prompt_variables)
    assert values["focus"] == "src/auth and tests/auth"
    assert values["context"] == "Pre-release security review"


@pytest.mark.parametrize(
    ("prompt_variables", "message"),
    [
        (("focus=one", "focus=two"), "duplicate prompt variable"),
        (("unknown=value",), "undeclared prompt variable"),
        (("oar.input_path=value",), "reserved oar namespace"),
        (("bad-name=value",), "invalid prompt variable name"),
        (("focus=",), "non-empty NAME=VALUE"),
    ],
)
def test_invalid_prompt_variable_assignments_are_rejected(
    tmp_path: Path, prompt_variables: tuple[str, ...], message: str
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()

    with pytest.raises(ConfigurationError, match=message):
        resolve_run(
            review_request(
                "review-repository",
                repository,
                prompt_variables=prompt_variables,
            )
        )


def test_prompt_variable_without_default_is_required(tmp_path: Path) -> None:
    profile = tmp_path / "code-reviewer"
    shutil.copytree(CODE_REVIEWER, profile)
    document = yaml.safe_load((profile / "profile.yaml").read_text())
    del document["tasks"]["review-repository"]["prompt_variables"]["context"]["default"]
    (profile / "profile.yaml").write_text(yaml.safe_dump(document, sort_keys=False))
    repository = tmp_path / "repository"
    repository.mkdir()

    with pytest.raises(ConfigurationError, match="missing required.*context"):
        resolve_run(
            RunRequest(
                profile_directory=profile,
                task_id="review-repository",
                output=Path("/tmp/review.md"),
                input_path=repository,
            )
        )


@pytest.mark.parametrize(
    ("task_id", "input_kind", "message"),
    [
        ("review-document", "directory", "input document must be a file"),
        ("review-repository", "file", "input repository must be a directory"),
    ],
)
def test_required_input_type_is_enforced(
    tmp_path: Path, task_id: str, input_kind: str, message: str
) -> None:
    input_path = tmp_path / "input"
    if input_kind == "directory":
        input_path.mkdir()
    else:
        input_path.write_text("content\n")

    with pytest.raises(ConfigurationError, match=message):
        resolve_run(review_request(task_id, input_path))


@pytest.mark.parametrize(
    ("task_id", "input_label"),
    [
        ("review-document", "DOCUMENT"),
        ("review-repository", "REPOSITORY"),
    ],
)
def test_required_input_must_be_provided(task_id: str, input_label: str) -> None:
    with pytest.raises(ConfigurationError, match=f"requires --input {input_label}"):
        resolve_run(review_request(task_id, None))


def test_task_without_required_input_rejects_input(tmp_path: Path) -> None:
    document = tmp_path / "document.md"
    document.write_text("content\n")

    with pytest.raises(ConfigurationError, match="does not accept --input"):
        resolve_run(
            RunRequest(
                profile_directory=PROFILE,
                task_id="editorial",
                output=Path("/tmp/review.json"),
                input_path=document,
            )
        )


def test_required_input_repository_root_cannot_be_overridden(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    request_with_override = review_request(
        "review-repository",
        repository,
        environments=("REPOSITORY_ROOT=/workspace/other",),
    )

    with pytest.raises(ConfigurationError, match="conflicting environment values"):
        resolve_run(request_with_override)


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
