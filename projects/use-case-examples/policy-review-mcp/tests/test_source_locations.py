# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path

import pytest

from policy_review_mcp.contracts import FieldAnnotation
from policy_review_mcp.policy import PolicyInputError, parse_policy, validate_annotations

FIXTURES = Path(__file__).parents[1] / "demo/fixtures"


def test_duplicate_keys_are_rejected() -> None:
    with pytest.raises(PolicyInputError, match="duplicate YAML key"):
        parse_policy("version: 1\nfilesystem_policy: {}\nfilesystem_policy: {}\n")


def test_supported_targets_have_parser_derived_locations() -> None:
    policy = parse_policy((FIXTURES / "candidate-broad.yaml").read_text())
    network = next(
        target
        for target in policy.targets
        if target.kind == "network_policies.endpoints.rules.allow"
    )
    assert network.locations[0].pointer == "/network_policies/github/endpoints/0/rules/0"
    assert network.locations[0].line > 1
    assert "/process" in {item["pointer"] for item in policy.unassessed}


def test_starting_policy_derives_changes_and_rejects_inconsistent_labels() -> None:
    starting = parse_policy("version: 1\nfilesystem_policy:\n  read_only: [/workspace]\n")
    candidate = parse_policy("version: 1\nfilesystem_policy:\n  read_only: [/workspace/src]\n")
    annotation = FieldAnnotation(
        pointer="/filesystem_policy/read_only/0", change="updated", editable=False
    )
    validated = validate_annotations([annotation], candidate, starting)
    assert validated[0]["change"] == "updated"
    assert validated[0]["editable"] is False
    with pytest.raises(PolicyInputError, match="derived updated"):
        validate_annotations(
            [annotation.model_copy(update={"change": "fixed"})], candidate, starting
        )


def test_removed_annotation_uses_starting_source() -> None:
    starting = parse_policy(
        "version: 1\nfilesystem_policy:\n  read_write: [/workspace]\n",
        source_name="starting",
    )
    candidate = parse_policy("version: 1\nfilesystem_policy:\n  read_write: []\n")
    validated = validate_annotations(
        [FieldAnnotation(pointer="/filesystem_policy/read_write/0", change="removed")],
        candidate,
        starting,
    )
    assert validated[0]["location"]["source"] == "starting"


def test_unknown_nested_filesystem_field_marks_scope_unassessed() -> None:
    policy = parse_policy(
        "version: 1\nfilesystem_policy:\n  read_only: [/workspace]\n  follow_symlinks: false\n"
    )
    assert policy.targets == ()
    assert {item["pointer"] for item in policy.unassessed} == {
        "/filesystem_policy/follow_symlinks",
        "/filesystem_policy",
    }


@pytest.mark.parametrize("value", ["null", "true", "[]"])
def test_non_mapping_filesystem_policy_is_unassessed(value: str) -> None:
    policy = parse_policy(f"version: 1\nfilesystem_policy: {value}\n")
    assert policy.targets == ()
    assert policy.unassessed == ({"pointer": "/filesystem_policy", "reason": "unsupported_shape"},)


def test_yaml_aliases_are_rejected_before_location_expansion() -> None:
    source = "version: 1\nshared: &shared\n  - leaf\nexpanded:\n  - *shared\n  - *shared\n"
    with pytest.raises(PolicyInputError, match="YAML aliases are unsupported"):
        parse_policy(source)


def test_non_string_keys_are_not_silently_converted_in_native_context() -> None:
    with pytest.raises(PolicyInputError, match="keys must be strings"):
        parse_policy("version: 1\nunknown: {1: first, '1': second}\n")


def test_absent_filesystem_marks_implicit_workdir_access_unassessed() -> None:
    parsed = parse_policy("version: 1\n")
    assert parsed.unassessed == (
        {"pointer": "/filesystem_policy/include_workdir", "reason": "implicit_runtime_workdir"},
    )
