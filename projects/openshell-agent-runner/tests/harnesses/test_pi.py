# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
from pathlib import Path

import yaml

from openshell_agent_runner.config import load_profile
from openshell_agent_runner.harnesses.pi.resources import (
    assets_directory,
    prepare_resources,
)
from openshell_agent_runner.harnesses.resources import PreparedResources

REPOSITORY = Path(__file__).resolve().parents[4]


def test_pi_image_contract_is_pinned_and_least_privilege() -> None:
    dockerfile = (assets_directory() / "Dockerfile").read_text()
    assert "ARG PI_VERSION=0.82.1" in dockerfile
    assert "iproute2" in dockerfile
    assert "git" in dockerfile
    assert "WORKDIR /sandbox" in dockerfile
    assert "USER node" in dockerfile


def test_pi_entrypoint_disables_automatic_resources() -> None:
    script = (assets_directory() / "exec.sh").read_text()
    for flag in (
        "--no-session",
        "--no-extensions",
        "--no-skills",
        "--no-prompt-templates",
        "--no-context-files",
        "--offline",
    ):
        assert flag in script
    assert Path(assets_directory() / "exec.sh").is_file()
    assert "agent_workdir=${REPOSITORY_ROOT:-/sandbox}" in script
    assert 'cd "$agent_workdir"' in script
    assert 'export OAR_MODEL_ID="$model_id"' in script
    assert "REPOSITORY_ROOT is not a directory" in script
    assert '[[ ! "$model_id" =~ ^[A-Za-z0-9._:/-]{1,256}$ ]]' in script


def test_declared_tools_are_forwarded_exactly() -> None:
    resolved = load_profile(
        REPOSITORY / ".github/openshell-agents/profiles/dev-note-reviewer/profile.yaml"
    )
    prepared = prepare_resources(resolved, "editorial", resolved.profile.harness.model)
    try:
        assert isinstance(prepared, PreparedResources)
        assert "REPOSITORY_ROOT=/workspace/source" in resolved.profile.sandbox.env
        index = prepared.arguments.index("--tools")
        assert prepared.arguments[index + 1] == "read,grep,find,ls,bash,submit_review"
        schema_upload = next(
            item for item in prepared.uploads if "output.schema.json" in item
        )
        schema = json.loads(Path(schema_upload.rpartition(":")[0]).read_text())
        assert schema["title"] == "DocumentReview"
        assert schema["properties"]["reviewer_id"]["const"] == "editorial"
        assert schema["properties"]["model_id"]["const"] == (
            resolved.profile.harness.model
        )
        scores = schema["properties"]["criterion_scores"]
        assert scores["minItems"] == 7
        assert scores["prefixItems"][0]["allOf"][1]["properties"]["criterion"] == {
            "const": "formulaic_language"
        }
    finally:
        prepared.close()


def test_submission_extension_checks_evidence_only_after_schema_validation() -> None:
    profile_root = REPOSITORY / ".github/openshell-agents/profiles/dev-note-reviewer"
    extension = (profile_root / "extensions/submit-review.ts").read_text()

    assert "schemaDiagnostics.length === 0 ? evidenceErrors(params) : []" in extension
    assert "const outputPath = `${outputDirectory}/review.json`" in extension
    resolved = load_profile(profile_root / "profile.yaml")
    assert (
        resolved.profile.tasks["editorial"].output.sandbox_path
        == "/sandbox/artifacts/review.json"
    )


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
