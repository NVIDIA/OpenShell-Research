"""Strict pipeline configuration tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from egress_gate.config import DefaultDecision, EgressGateConfig
from egress_gate.constants import MAX_PIPELINE_GATES
from egress_gate.gates import RegexConfig


def _regex_config() -> dict[str, object]:
    return {
        "kind": "regex",
        "scan": {"kind": "body", "action": {"kind": "detect"}},
        "pattern_catalog": {
            "entities": [
                {
                    "name": "token",
                    "rules": [{"pattern": "secret", "confidence": "high"}],
                }
            ]
        },
    }


def _values(*, default_decision: str = "allow") -> dict[str, object]:
    return {
        "gates": [{"name": "body", **_regex_config()}],
        "default_decision": default_decision,
    }


def test_pipeline_uses_required_default_and_exact_gate_entries() -> None:
    config = EgressGateConfig[RegexConfig].model_validate(_values())

    assert config.default_decision is DefaultDecision.ALLOW
    assert config.gates[0].name == "body"
    assert type(config.gates[0]) is RegexConfig


def test_pipeline_default_deny_is_explicit() -> None:
    config = EgressGateConfig[RegexConfig].model_validate(
        _values(default_decision="deny")
    )
    assert config.default_decision is DefaultDecision.DENY

    missing_default = {
        "gates": [{"name": "body", **_regex_config()}],
    }
    with pytest.raises(ValidationError):
        EgressGateConfig[RegexConfig].model_validate(missing_default)


def test_pipeline_rejects_unknown_fields_and_duplicate_names() -> None:
    unknown = {
        "gates": [{"name": "body", **_regex_config()}],
        "default_decision": "allow",
        "unexpected": True,
    }
    with pytest.raises(ValidationError):
        EgressGateConfig[RegexConfig].model_validate(unknown)

    duplicate = {
        "gates": [
            {"name": "body", **_regex_config()},
            {"name": "body", **_regex_config()},
        ],
        "default_decision": "allow",
    }
    with pytest.raises(ValidationError) as duplicate_error:
        EgressGateConfig[RegexConfig].model_validate(duplicate)
    assert duplicate_error.value.errors()[0]["loc"] == ("gates",)


def test_removed_policy_wrappers_are_rejected() -> None:
    nested_policy = {"pipeline": _values()}
    with pytest.raises(ValidationError):
        EgressGateConfig[RegexConfig].model_validate(nested_policy)

    nested_gate = {
        "gates": [{"name": "body", "config": _regex_config()}],
        "default_decision": "allow",
    }
    with pytest.raises(ValidationError):
        EgressGateConfig[RegexConfig].model_validate(nested_gate)


def test_pipeline_gate_count_has_an_exact_boundary() -> None:
    exact_gates = [
        {"name": f"body-{index}", **_regex_config()}
        for index in range(MAX_PIPELINE_GATES)
    ]
    exact = {
        "gates": exact_gates,
        "default_decision": "allow",
    }
    config = EgressGateConfig[RegexConfig].model_validate(exact)
    assert len(config.gates) == MAX_PIPELINE_GATES

    too_many_gates = [
        *exact_gates,
        {"name": "body-over", **_regex_config()},
    ]
    too_many = {
        "gates": too_many_gates,
        "default_decision": "allow",
    }
    with pytest.raises(ValidationError):
        EgressGateConfig[RegexConfig].model_validate(too_many)


def test_regex_scan_structurally_restricts_header_actions() -> None:
    invalid = _regex_config()
    invalid["scan"] = {
        "kind": "header",
        "names": ["x-note"],
        "action": {"kind": "replace", "template": "[{entity}]"},
    }
    with pytest.raises(ValidationError):
        EgressGateConfig[RegexConfig].model_validate(
            {
                "gates": [{"name": "header", **invalid}],
                "default_decision": "allow",
            }
        )
