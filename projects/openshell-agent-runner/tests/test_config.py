# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path

import pytest

from openshell_agent_runner.config import load_profile
from openshell_agent_runner.errors import ConfigurationError

REPOSITORY = Path(__file__).resolve().parents[3]
PROFILE = REPOSITORY / ".github/openshell-agents/profiles/dev-note-reviewer"
PACKAGED_PROFILE = REPOSITORY / "projects/openshell-agent-runner/profiles/reviewer"


def test_repository_profile_validates() -> None:
    resolved = load_profile(PROFILE)
    assert resolved.profile.id == "dev-note-reviewer"
    assert list(resolved.profile.tasks) == ["editorial", "technical"]


def test_packaged_profile_validates() -> None:
    profile = load_profile(PACKAGED_PROFILE).profile
    assert profile.id == "reviewer"
    assert profile.sandbox.from_ == (
        "projects/openshell-agent-runner/src/openshell_agent_runner/harnesses/pi/assets"
    )


def test_profile_argument_must_be_a_directory(tmp_path: Path) -> None:
    profile = tmp_path / "profile.yaml"
    profile.write_text("id: test\n")

    with pytest.raises(ConfigurationError, match="profile must be a directory"):
        load_profile(profile)


def test_profile_directory_requires_profile_yaml(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="missing profile configuration"):
        load_profile(tmp_path)


def test_unknown_profile_key_is_rejected(tmp_path: Path) -> None:
    profile = tmp_path / "profile.yaml"
    profile.write_text("id: test\nunexpected: true\n")
    with pytest.raises(ConfigurationError, match="unexpected"):
        load_profile(tmp_path)


def test_profile_resource_escape_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-policy.yaml"
    outside.write_text("version: 1\n")
    (tmp_path / "prompt.md").write_text("review\n")
    profile = tmp_path / "profile.yaml"
    profile.write_text(
        """id: test
description: Test.
harness: {type: pi, model: test}
sandbox: {from: test, policy: ../outside-policy.yaml}
tasks:
  check:
    prompt: prompt.md
    output:
      type: document_review
      contract:
        reviewer_id: test
        criteria: [clarity]
      sandbox_path: /sandbox/artifacts/result.json
      max_bytes: 100
"""
    )
    with pytest.raises(ConfigurationError, match="escapes"):
        load_profile(tmp_path)


def test_duplicate_document_review_criteria_are_rejected(tmp_path: Path) -> None:
    (tmp_path / "policy.yaml").write_text("version: 1\n")
    (tmp_path / "prompt.md").write_text("review\n")
    profile = tmp_path / "profile.yaml"
    profile.write_text(
        """id: test
description: Test.
harness: {type: pi, model: test}
sandbox: {from: test, policy: policy.yaml}
tasks:
  check:
    prompt: prompt.md
    output:
      type: document_review
      contract:
        reviewer_id: test
        criteria: [clarity, clarity]
      sandbox_path: /sandbox/artifacts/result.json
      max_bytes: 100
"""
    )
    with pytest.raises(ConfigurationError, match="criteria must be unique"):
        load_profile(tmp_path)


@pytest.mark.parametrize(
    ("sandbox", "message"),
    [
        (
            "upload: [one:/workspace/../sandbox/oar-runtime/file]",
            "must not contain '..'",
        ),
        (
            "upload: [one:/workspace/input, two:/workspace/input]",
            "conflicting upload destination",
        ),
        (
            "env: [MODE=one, MODE=two]",
            "conflicting environment values",
        ),
    ],
)
def test_invalid_static_sandbox_assignments_are_rejected(
    tmp_path: Path, sandbox: str, message: str
) -> None:
    (tmp_path / "policy.yaml").write_text("version: 1\n")
    (tmp_path / "prompt.md").write_text("review\n")
    profile = tmp_path / "profile.yaml"
    profile.write_text(
        f"""id: test
description: Test.
harness: {{type: pi, model: test}}
sandbox:
  from: test
  policy: policy.yaml
  {sandbox}
tasks:
  check:
    prompt: prompt.md
    output:
      type: document_review
      contract:
        reviewer_id: test
        criteria: [clarity]
      sandbox_path: /sandbox/artifacts/result.json
      max_bytes: 100
"""
    )
    with pytest.raises(ConfigurationError, match=message):
        load_profile(tmp_path)


def test_profile_resource_types_are_checked(tmp_path: Path) -> None:
    (tmp_path / "policy.yaml").mkdir()
    (tmp_path / "prompt.md").write_text("review\n")
    profile = tmp_path / "profile.yaml"
    profile.write_text(
        """id: test
description: Test.
harness: {type: pi, model: test}
sandbox: {from: test, policy: policy.yaml}
tasks:
  check:
    prompt: prompt.md
    output:
      type: document_review
      contract:
        reviewer_id: test
        criteria: [clarity]
      sandbox_path: /sandbox/artifacts/result.json
      max_bytes: 100
"""
    )
    with pytest.raises(ConfigurationError, match="sandbox policy must be a file"):
        load_profile(tmp_path)


def test_skill_directory_requires_skill_markdown(tmp_path: Path) -> None:
    (tmp_path / "policy.yaml").write_text("version: 1\n")
    (tmp_path / "prompt.md").write_text("review\n")
    (tmp_path / "skill").mkdir()
    profile = tmp_path / "profile.yaml"
    profile.write_text(
        """id: test
description: Test.
harness: {type: pi, model: test}
sandbox: {from: test, policy: policy.yaml}
tasks:
  check:
    prompt: prompt.md
    skills: [skill]
    output:
      type: document_review
      contract:
        reviewer_id: test
        criteria: [clarity]
      sandbox_path: /sandbox/artifacts/result.json
      max_bytes: 100
"""
    )
    with pytest.raises(ConfigurationError, match="missing SKILL.md"):
        load_profile(tmp_path)


def test_skill_tree_rejects_symlinks(tmp_path: Path) -> None:
    (tmp_path / "policy.yaml").write_text("version: 1\n")
    (tmp_path / "prompt.md").write_text("review\n")
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# Skill\n")
    outside = tmp_path / "outside.txt"
    outside.write_text("private\n")
    (skill / "leak.txt").symlink_to(outside)
    profile = tmp_path / "profile.yaml"
    profile.write_text(
        """id: test
description: Test.
harness: {type: pi, model: test}
sandbox: {from: test, policy: policy.yaml}
tasks:
  check:
    prompt: prompt.md
    skills: [skill]
    output:
      type: document_review
      contract:
        reviewer_id: test
        criteria: [clarity]
      sandbox_path: /sandbox/artifacts/result.json
      max_bytes: 100
"""
    )

    with pytest.raises(ConfigurationError, match="contains a symlink"):
        load_profile(tmp_path)


def test_harness_token_limit_must_fit_context_window(tmp_path: Path) -> None:
    (tmp_path / "policy.yaml").write_text("version: 1\n")
    (tmp_path / "prompt.md").write_text("review\n")
    profile = tmp_path / "profile.yaml"
    profile.write_text(
        """id: test
description: Test.
harness: {type: pi, model: test, context_window: 10, max_tokens: 11}
sandbox: {from: test, policy: policy.yaml}
tasks:
  check:
    prompt: prompt.md
    output:
      type: document_review
      contract:
        reviewer_id: test
        criteria: [clarity]
      sandbox_path: /sandbox/artifacts/result.json
      max_bytes: 100
"""
    )

    with pytest.raises(ConfigurationError, match="max_tokens must not exceed"):
        load_profile(tmp_path)


@pytest.mark.parametrize("model_line", ["", "  model: bad model\n"])
def test_harness_requires_valid_model(tmp_path: Path, model_line: str) -> None:
    (tmp_path / "policy.yaml").write_text("version: 1\n")
    (tmp_path / "prompt.md").write_text("review\n")
    profile = tmp_path / "profile.yaml"
    profile.write_text(
        f"""id: test
description: Test.
harness:
  type: pi
{model_line}sandbox: {{from: test, policy: policy.yaml}}
tasks:
  check:
    prompt: prompt.md
    output:
      type: document_review
      contract:
        reviewer_id: test
        criteria: [clarity]
      sandbox_path: /sandbox/artifacts/result.json
      max_bytes: 100
"""
    )

    with pytest.raises(ConfigurationError, match="harness.model"):
        load_profile(tmp_path)


def test_invalid_profile_encoding_is_configuration_error(tmp_path: Path) -> None:
    profile = tmp_path / "profile.yaml"
    profile.write_bytes(b"\xff\xfe")

    with pytest.raises(ConfigurationError, match="cannot read configuration"):
        load_profile(tmp_path)
