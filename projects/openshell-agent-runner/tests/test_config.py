# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
from pathlib import Path

import pytest

from openshell_agent_runner.config import load_profile
from openshell_agent_runner.errors import ConfigurationError

REPOSITORY = Path(__file__).resolve().parents[3]
PROFILE = REPOSITORY / ".github/openshell-agents/profiles/dev-note-reviewer"
PACKAGED_PROFILE = (
    REPOSITORY
    / "projects/openshell-agent-runner/src/openshell_agent_runner/profiles/reviewer"
)


def test_repository_profile_validates() -> None:
    resolved = load_profile(PROFILE)
    assert resolved.profile.id == "dev-note-reviewer"
    assert list(resolved.profile.tasks) == ["editorial", "technical"]


def test_packaged_profile_validates() -> None:
    resolved = load_profile(PACKAGED_PROFILE)
    assert resolved.profile.id == "reviewer"
    assert resolved.runtime.model == "MODEL_ID"
    assert resolved.runtime.thinking == "high"
    assert list(resolved.profile.tasks) == ["review-document", "review-repository"]
    assert resolved.profile.tasks["review-document"].required_input == "document"
    assert resolved.profile.tasks["review-repository"].required_input == "repository"


def test_unknown_required_input_is_rejected(tmp_path: Path) -> None:
    _write_profile(tmp_path, task="required_input: archive")

    with pytest.raises(ConfigurationError, match="required_input"):
        load_profile(tmp_path)


def test_prompt_variables_must_be_declared_and_used(tmp_path: Path) -> None:
    _write_profile(
        tmp_path,
        task="""prompt_variables:
      focus:
        description: Review focus.""",
    )

    with pytest.raises(ConfigurationError, match="unused.*focus"):
        load_profile(tmp_path)

    (tmp_path / "prompt.md").write_text("Review {{ unknown }}.\n")
    with pytest.raises(ConfigurationError, match="unknown.*unknown"):
        load_profile(tmp_path)


def test_input_prompt_builtins_require_a_task_input(tmp_path: Path) -> None:
    _write_profile(tmp_path)
    (tmp_path / "prompt.md").write_text("Review {{ oar.input_path }}.\n")

    with pytest.raises(ConfigurationError, match="unknown.*oar.input_path"):
        load_profile(tmp_path)


def test_profile_argument_must_be_a_directory(tmp_path: Path) -> None:
    (tmp_path / "profile.yaml").write_text("id: test\n")

    with pytest.raises(ConfigurationError, match="profile must be a directory"):
        load_profile(tmp_path / "profile.yaml")


def test_profile_directory_requires_profile_yaml(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="missing profile configuration"):
        load_profile(tmp_path)


def test_unknown_profile_key_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "profile.yaml").write_text("id: test\nunexpected: true\n")

    with pytest.raises(ConfigurationError, match="unexpected"):
        load_profile(tmp_path)


def test_profile_resource_escape_is_rejected(tmp_path: Path) -> None:
    (tmp_path.parent / "outside-policy.yaml").write_text("version: 1\n")
    _write_profile(tmp_path, policy="../outside-policy.yaml")

    with pytest.raises(ConfigurationError, match="escapes"):
        load_profile(tmp_path)


@pytest.mark.parametrize(
    ("sandbox", "message"),
    [
        (
            "upload: [one:/workspace/../sandbox/oar-runtime/file]",
            "must not contain '..'",
        ),
        (
            "upload: [one:/sandbox/artifacts/result]",
            "reserved for runner resources",
        ),
        ("upload: [one:/sandbox]", "reserved for runner resources"),
        ("upload: [one:/]", "reserved for runner resources"),
        (
            "upload: [one://sandbox/oar-runtime/file]",
            "canonical absolute paths",
        ),
        ("env: [BAD-NAME=value]", "invalid OpenShell environment name"),
        ("env: [OPENSHELL_GATEWAY=local]", "reserved OPENSHELL_ prefix"),
        ("env: [MODE=one, MODE=two]", "conflicting environment values"),
    ],
)
def test_invalid_static_sandbox_assignments_are_rejected(
    tmp_path: Path, sandbox: str, message: str
) -> None:
    _write_profile(tmp_path, sandbox=sandbox)

    with pytest.raises(ConfigurationError, match=message):
        load_profile(tmp_path)


def test_profile_resource_types_are_checked(tmp_path: Path) -> None:
    _write_profile(tmp_path)
    (tmp_path / "policy.yaml").unlink()
    (tmp_path / "policy.yaml").mkdir()

    with pytest.raises(ConfigurationError, match="sandbox policy must be a file"):
        load_profile(tmp_path)


def test_skill_directory_requires_skill_markdown(tmp_path: Path) -> None:
    _write_profile(tmp_path, task="skills: [skill]")
    (tmp_path / "skill").mkdir()

    with pytest.raises(ConfigurationError, match="missing SKILL.md"):
        load_profile(tmp_path)


def test_skill_tree_rejects_symlinks(tmp_path: Path) -> None:
    _write_profile(tmp_path, task="skills: [skill]")
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# Skill\n")
    outside = tmp_path / "outside.txt"
    outside.write_text("private\n")
    (skill / "leak.txt").symlink_to(outside)

    with pytest.raises(ConfigurationError, match="contains a symlink"):
        load_profile(tmp_path)


def test_unknown_tool_is_rejected(tmp_path: Path) -> None:
    _write_profile(tmp_path, task="tools: [not_a_real_tool]")

    with pytest.raises(ConfigurationError, match="unknown tools.*not_a_real_tool"):
        load_profile(tmp_path)


def test_custom_tool_requires_extension_declaration(tmp_path: Path) -> None:
    _write_profile(tmp_path, task="tools: [custom_check]")

    with pytest.raises(ConfigurationError, match="declare each custom tool"):
        load_profile(tmp_path)


def test_custom_tool_with_existing_extension_validates(tmp_path: Path) -> None:
    _write_profile(
        tmp_path,
        task="""tools: [custom_check]
    extensions:
      - path: extension.ts
        tools: [custom_check]""",
    )
    (tmp_path / "extension.ts").write_text("export default function () {}\n")

    resolved = load_profile(tmp_path)

    extension = resolved.profile.tasks["check"].extensions[0]
    assert extension.path == Path("extension.ts")
    assert extension.tools == ["custom_check"]


def test_custom_tool_extension_file_must_exist(tmp_path: Path) -> None:
    _write_profile(
        tmp_path,
        task="""tools: [custom_check]
    extensions:
      - path: missing.ts
        tools: [custom_check]""",
    )

    with pytest.raises(ConfigurationError, match="missing extension for task check"):
        load_profile(tmp_path)


@pytest.mark.parametrize(
    ("models", "message"),
    [
        ("not json", "invalid Pi models file"),
        ('{"providers":{"other":{"models":[]}}}', "provider named 'openshell'"),
        ('{"providers":{"openshell":{"models":[]}}}', "exactly one model"),
        (
            '{"providers":{"openshell":{"models":[{"id":"bad model"}]}}}',
            "valid string id",
        ),
    ],
)
def test_profile_requires_supported_pi_models_file(
    tmp_path: Path, models: str, message: str
) -> None:
    _write_profile(tmp_path)
    (tmp_path / "models.json").write_text(models)

    with pytest.raises(ConfigurationError, match=message):
        load_profile(tmp_path)


@pytest.mark.parametrize(
    ("settings", "message"),
    [
        (
            '{"defaultProvider":"openshell","defaultModel":"other",'
            '"defaultThinkingLevel":"high"}',
            "must identify the model",
        ),
        (
            '{"defaultProvider":"openshell","defaultModel":"test",'
            '"defaultThinkingLevel":"high","theme":"custom"}',
            "unexpected.*theme",
        ),
        (
            '{"defaultProvider":"openshell","defaultModel":"test"}',
            "missing.*defaultThinkingLevel",
        ),
    ],
)
def test_profile_requires_exact_pi_runtime_settings(
    tmp_path: Path, settings: str, message: str
) -> None:
    _write_profile(tmp_path)
    (tmp_path / "settings.json").write_text(settings)

    with pytest.raises(ConfigurationError, match=message):
        load_profile(tmp_path)


def test_invalid_output_schema_is_rejected(tmp_path: Path) -> None:
    _write_profile(tmp_path, task="output_schema: output.schema.json")
    (tmp_path / "output.schema.json").write_text('{"type":"not-a-type"}')

    with pytest.raises(ConfigurationError, match="invalid output schema"):
        load_profile(tmp_path)


def test_output_schema_accepts_standard_format_annotations(tmp_path: Path) -> None:
    _write_profile(tmp_path, task="output_schema: output.schema.json")
    (tmp_path / "output.schema.json").write_text(
        '{"type":"string","format":"date-time","x-oar-note":true}'
    )

    load_profile(tmp_path)


@pytest.mark.parametrize("keyword", ["pattern", "patternProperties"])
def test_output_schema_rejects_regex_keywords(tmp_path: Path, keyword: str) -> None:
    _write_profile(tmp_path, task="output_schema: output.schema.json")
    value: object = (
        {"(?i)abc": {"type": "string"}} if keyword == "patternProperties" else "(?i)abc"
    )
    (tmp_path / "output.schema.json").write_text(
        json.dumps({"type": "object", keyword: value})
    )

    with pytest.raises(ConfigurationError, match="regular-expression keywords"):
        load_profile(tmp_path)


def test_output_schema_allows_property_names_that_match_schema_keywords(
    tmp_path: Path,
) -> None:
    _write_profile(tmp_path, task="output_schema: output.schema.json")
    (tmp_path / "output.schema.json").write_text(
        json.dumps(
            {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "$ref": {"type": "string"},
                },
            }
        )
    )

    load_profile(tmp_path)


@pytest.mark.parametrize("keyword", ["const", "examples", "x-oar-note"])
def test_output_schema_does_not_treat_instance_data_as_a_schema(
    tmp_path: Path, keyword: str
) -> None:
    _write_profile(tmp_path, task="output_schema: output.schema.json")
    value: object = (
        [{"pattern": "value"}] if keyword == "examples" else {"pattern": "value"}
    )
    (tmp_path / "output.schema.json").write_text(json.dumps({keyword: value}))

    load_profile(tmp_path)


@pytest.mark.parametrize("keyword", ["$ref", "$dynamicRef", "$recursiveRef"])
def test_output_schema_rejects_reference_keywords(tmp_path: Path, keyword: str) -> None:
    _write_profile(tmp_path, task="output_schema: output.schema.json")
    (tmp_path / "output.schema.json").write_text(
        json.dumps({"$defs": {"value": {"type": "string"}}, keyword: "#/$defs/value"})
    )

    with pytest.raises(ConfigurationError, match="reference keywords"):
        load_profile(tmp_path)


def test_invalid_profile_encoding_is_configuration_error(tmp_path: Path) -> None:
    (tmp_path / "profile.yaml").write_bytes(b"\xff\xfe")

    with pytest.raises(ConfigurationError, match="cannot read configuration"):
        load_profile(tmp_path)


def _write_profile(
    directory: Path,
    *,
    policy: str = "policy.yaml",
    sandbox: str = "",
    task: str = "",
) -> None:
    (directory / "policy.yaml").write_text("version: 1\n")
    (directory / "prompt.md").write_text("review\n")
    (directory / "models.json").write_text(
        '{"providers":{"openshell":{"models":[{"id":"test"}]}}}'
    )
    (directory / "settings.json").write_text(
        '{"defaultProvider":"openshell","defaultModel":"test",'
        '"defaultThinkingLevel":"high"}'
    )
    sandbox_line = f"  {sandbox}\n" if sandbox else ""
    task_line = f"    {task}\n" if task else ""
    (directory / "profile.yaml").write_text(
        f"""id: test
description: Test.
sandbox:
  policy: {policy}
{sandbox_line}tasks:
  check:
    prompt: prompt.md
{task_line}
"""
    )
