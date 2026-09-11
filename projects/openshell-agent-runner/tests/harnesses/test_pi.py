# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import shutil
from pathlib import Path

import yaml

from openshell_agent_runner.config import load_profile
from openshell_agent_runner.harnesses.pi.resources import (
    image_directory,
    prepare_resources,
)
from openshell_agent_runner.harnesses.resources import PreparedResources

REPOSITORY = Path(__file__).resolve().parents[4]


def test_pi_image_contract_is_pinned_and_least_privilege() -> None:
    dockerfile = (image_directory() / "Dockerfile").read_text()
    assert "ARG PI_VERSION=0.84.2" in dockerfile
    assert "ARG AJV_VERSION=8.20.0" in dockerfile
    assert "ARG TYPEBOX_VERSION=1.3.16" in dockerfile
    assert "ENV NODE_PATH=/usr/local/lib/node_modules" in dockerfile
    assert "iproute2" in dockerfile
    assert "git" in dockerfile
    assert "WORKDIR /sandbox" in dockerfile
    assert "USER node" in dockerfile


def test_schema_task_receives_generic_submission_protocol() -> None:
    resolved = load_profile(
        REPOSITORY / ".github/openshell-agents/profiles/ci-reviewer"
    )
    prepared = prepare_resources(
        resolved,
        "review-tool",
        {
            "review_skill": "review-tool",
            "guidelines_path": "/workspace/trusted-guidelines.md",
            "focus": "Review the note.",
            "context": "A new Dev Note.",
            "oar.input_path": "/workspace/input/document.md",
            "oar.input_name": "note.md",
        },
    )
    try:
        assert isinstance(prepared, PreparedResources)
        index = prepared.arguments.index("--tools")
        assert prepared.arguments[index + 1] == "read,grep,find,ls,bash,submit_result"
        assert "/sandbox/oar-runtime/extensions/oar-submit-result.ts" in (
            prepared.arguments
        )
        assert "/sandbox/oar-runtime/extensions/oar-validate-tools.ts" in (
            prepared.arguments
        )
        tools_upload = next(
            item
            for item in prepared.uploads
            if item.endswith(":/sandbox/oar-runtime/tools.json")
        )
        assert json.loads(Path(tools_upload.rpartition(":")[0]).read_text()) == [
            "read",
            "grep",
            "find",
            "ls",
            "bash",
            "submit_result",
        ]
        schema_upload = next(
            item for item in prepared.uploads if "output.schema.json" in item
        )
        schema = json.loads(Path(schema_upload.rpartition(":")[0]).read_text())
        assert schema["title"] == "NewProjectReview"
        assert schema == json.loads(
            (resolved.profile_dir / "schemas/review.json").read_text()
        )
        assert prepared.arguments[:6] == (
            "--provider",
            "openshell",
            "--model",
            resolved.runtime.model,
            "--thinking",
            "medium",
        )
        models_upload = next(
            item
            for item in prepared.uploads
            if item.endswith(":/sandbox/oar-runtime/models.json")
        )
        assert (
            Path(models_upload.rpartition(":")[0]).read_bytes()
            == (resolved.profile_dir / "models.json").read_bytes()
        )
        settings_upload = next(
            item
            for item in prepared.uploads
            if item.endswith(":/sandbox/oar-runtime/settings.json")
        )
        assert (
            Path(settings_upload.rpartition(":")[0]).read_bytes()
            == (resolved.profile_dir / "settings.json").read_bytes()
        )
    finally:
        prepared.close()


def test_packaged_review_tasks_stage_schema_skill_and_render_prompt() -> None:
    profiles = (
        ("code-reviewer", "review-repository", "repository", "review-code"),
        (
            "technical-writing-reviewer",
            "review-document",
            "document.txt",
            "review-technical-writing",
        ),
    )
    for profile_name, task_id, input_name, skill_name in profiles:
        resolved = load_profile(
            REPOSITORY
            / "projects/openshell-agent-runner/src/openshell_agent_runner/profiles"
            / profile_name
        )
        input_path = f"/workspace/input/{input_name}"
        prepared = prepare_resources(
            resolved,
            task_id,
            {
                "focus": "Focus on authentication.",
                "context": "Pre-release review.",
                "oar.input_name": input_name,
                "oar.input_path": input_path,
            },
        )
        try:
            tools_index = prepared.arguments.index("--tools")
            assert "submit_result" in prepared.arguments[tools_index + 1].split(",")
            assert any(
                upload.endswith(":/sandbox/oar-runtime/output.schema.json")
                for upload in prepared.uploads
            )
            skill_argument = next(
                prepared.arguments[index + 1]
                for index, argument in enumerate(prepared.arguments)
                if argument == "--skill"
            )
            assert skill_argument.endswith(f"-{skill_name}")
            assert "/sandbox/oar-runtime/extensions/oar-validate-tools.ts" in (
                prepared.arguments
            )
            prompt_upload = next(
                item
                for item in prepared.uploads
                if item.endswith(":/sandbox/oar-runtime/prompt.md")
            )
            prompt = Path(prompt_upload.rpartition(":")[0]).read_text()
            assert input_path in prompt
            assert "Focus on authentication." in prompt
            assert "Pre-release review." in prompt
            assert skill_name in prompt
            assert "{{" not in prompt
        finally:
            prepared.close()


def test_custom_extension_and_declared_tool_are_staged(tmp_path: Path) -> None:
    source = (
        REPOSITORY
        / "projects/openshell-agent-runner/src/openshell_agent_runner/profiles/technical-writing-reviewer"
    )
    profile = tmp_path / "profile"
    shutil.copytree(source, profile)
    extension_path = profile / "custom-check.ts"
    extension_path.write_text("export default function () {}\n")
    document = yaml.safe_load((profile / "profile.yaml").read_text())
    task = document["tasks"]["review-document"]
    task["tools"].append("custom_check")
    task["extensions"] = [{"path": "custom-check.ts", "tools": ["custom_check"]}]
    (profile / "profile.yaml").write_text(yaml.safe_dump(document, sort_keys=False))

    prepared = prepare_resources(
        load_profile(profile),
        "review-document",
        {
            "focus": "Review the complete document.",
            "context": "No additional context was provided.",
            "oar.input_name": "document.md",
            "oar.input_path": "/workspace/input/document.md",
        },
    )
    try:
        assert "/sandbox/oar-runtime/extensions/00-custom-check.ts" in (
            prepared.arguments
        )
        tools_upload = next(
            item
            for item in prepared.uploads
            if item.endswith(":/sandbox/oar-runtime/tools.json")
        )
        assert "custom_check" in json.loads(
            Path(tools_upload.rpartition(":")[0]).read_text()
        )
    finally:
        prepared.close()


def test_supplied_policies_allow_no_ordinary_network_egress() -> None:
    policies = [
        REPOSITORY / ".github/openshell-agents/profiles/ci-reviewer/policy.yaml",
        *(
            REPOSITORY
            / "projects/openshell-agent-runner/src/openshell_agent_runner/profiles"
            / profile_name
            / "policy.yaml"
            for profile_name in (
                "code-reviewer",
                "technical-writing-reviewer",
            )
        ),
    ]

    for path in policies:
        policy = yaml.safe_load(path.read_text())
        assert policy["network_policies"] == {}
        assert policy["process"] == {"run_as_user": "1000", "run_as_group": "1000"}
        assert "/opt/oar" in policy["filesystem_policy"]["read_only"]
