# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
from pathlib import Path

import yaml

from openshell_agent_runner.artifacts import ARTIFACT_PATH
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


def test_pi_entrypoint_disables_automatic_resources() -> None:
    script = (image_directory() / "exec.sh").read_text()
    for flag in (
        "--no-session",
        "--no-extensions",
        "--no-skills",
        "--no-prompt-templates",
        "--no-context-files",
        "--offline",
    ):
        assert flag in script
    assert Path(image_directory() / "exec.sh").is_file()
    assert "agent_workdir=${REPOSITORY_ROOT:-/sandbox}" in script
    assert 'cd "$agent_workdir"' in script
    assert 'export OAR_MODEL_ID="$model_id"' in script
    assert "REPOSITORY_ROOT is not a directory" in script
    assert '[[ ! "$model_id" =~ ^[A-Za-z0-9._:/-]{1,256}$ ]]' in script
    assert '"${arguments[$index]}" == "--model"' in script


def test_schema_task_receives_generic_submission_protocol() -> None:
    resolved = load_profile(
        REPOSITORY / ".github/openshell-agents/profiles/dev-note-reviewer"
    )
    prepared = prepare_resources(resolved, "editorial")
    try:
        assert isinstance(prepared, PreparedResources)
        assert "REPOSITORY_ROOT=/workspace/source" in resolved.profile.sandbox.env
        index = prepared.arguments.index("--tools")
        assert prepared.arguments[index + 1] == "read,grep,find,ls,bash,submit_result"
        assert "/sandbox/oar-runtime/extensions/oar-submit-result.ts" in (
            prepared.arguments
        )
        schema_upload = next(
            item for item in prepared.uploads if "output.schema.json" in item
        )
        schema = json.loads(Path(schema_upload.rpartition(":")[0]).read_text())
        assert schema["title"] == "DevNoteReview"
        assert schema == json.loads(
            (resolved.profile_dir / "schemas/review.json").read_text()
        )
        assert prepared.arguments[:6] == (
            "--provider",
            "openshell",
            "--model",
            resolved.runtime.model,
            "--thinking",
            "high",
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


def test_plain_task_uses_final_response_without_submission_tool() -> None:
    resolved = load_profile(
        REPOSITORY / "projects/openshell-agent-runner/profiles/reviewer"
    )
    prepared = prepare_resources(resolved, "review")
    try:
        assert "submit_result" not in prepared.arguments
        assert not any("output.schema.json" in upload for upload in prepared.uploads)
    finally:
        prepared.close()


def test_generic_submission_extension_validates_and_saves_result() -> None:
    extension = (
        REPOSITORY
        / "projects/openshell-agent-runner/src/openshell_agent_runner/harnesses/pi/runtime/extensions/submit-result.ts"
    ).read_text()

    assert 'import Ajv2020 from "ajv/dist/2020.js"' in extension
    assert 'import { Type } from "typebox"' in extension
    assert "strict: false" in extension
    assert "validateFormats: false" in extension
    assert "Type.Object({ result: Type.Unsafe(schema) })" in extension
    assert "async execute(_toolCallId, { result })" in extension
    assert 'name: "submit_result"' in extension
    assert "const outputPath = `${outputDirectory}/result`" in extension
    assert ARTIFACT_PATH == "/sandbox/artifacts/result"


def test_supplied_policies_allow_no_ordinary_network_egress() -> None:
    policies = [
        REPOSITORY / ".github/openshell-agents/profiles/dev-note-reviewer/policy.yaml",
        REPOSITORY / "projects/openshell-agent-runner/profiles/reviewer/policy.yaml",
    ]

    for path in policies:
        policy = yaml.safe_load(path.read_text())
        assert policy["network_policies"] == {}
        assert policy["process"] == {"run_as_user": "1000", "run_as_group": "1000"}
        assert "/opt/oar" in policy["filesystem_policy"]["read_only"]
