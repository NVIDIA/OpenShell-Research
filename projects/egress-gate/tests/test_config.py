"""Strict pipeline configuration tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from egress_gate.config import (
    ConfiguredGate,
    DefaultDecision,
    EgressGateConfig,
)
from egress_gate.constants import MAX_PIPELINE_GATES
from egress_gate.gates import RegexBodyConfig


def _regex_config() -> dict[str, object]:
    return {
        "gate": "regex-body",
        "pattern_catalog": {
            "entities": [
                {
                    "name": "token",
                    "rules": [{"pattern": "secret", "confidence": "high"}],
                }
            ]
        },
        "mode": "detect",
    }


def _values(*, default_decision: str = "allow") -> dict[str, object]:
    pipeline: dict[str, object] = {
        "gates": [{"name": "body", "config": _regex_config()}],
        "default_decision": default_decision,
    }
    return {"pipeline": pipeline}


def test_pipeline_uses_required_default_and_exact_gate_entries() -> None:
    config = EgressGateConfig[RegexBodyConfig].model_validate(_values())

    assert config.pipeline.default_decision is DefaultDecision.ALLOW
    assert config.pipeline.gates[0].name == "body"
    assert type(config.pipeline.gates[0].config) is RegexBodyConfig
    assert ConfiguredGate.model_fields["config"].is_required()


def test_pipeline_default_deny_is_explicit() -> None:
    config = EgressGateConfig[RegexBodyConfig].model_validate(
        _values(default_decision="deny")
    )
    assert config.pipeline.default_decision is DefaultDecision.DENY

    missing_default = {
        "pipeline": {
            "gates": [{"name": "body", "config": _regex_config()}],
        }
    }
    with pytest.raises(ValidationError):
        EgressGateConfig[RegexBodyConfig].model_validate(missing_default)


def test_pipeline_rejects_unknown_fields_and_duplicate_names() -> None:
    unknown = {
        "pipeline": {
            "gates": [{"name": "body", "config": _regex_config()}],
            "default_decision": "allow",
            "unexpected": True,
        }
    }
    with pytest.raises(ValidationError):
        EgressGateConfig[RegexBodyConfig].model_validate(unknown)

    duplicate = {
        "pipeline": {
            "gates": [
                {"name": "body", "config": _regex_config()},
                {"name": "body", "config": _regex_config()},
            ],
            "default_decision": "allow",
        }
    }
    with pytest.raises(ValidationError):
        EgressGateConfig[RegexBodyConfig].model_validate(duplicate)


def test_pipeline_gate_count_has_an_exact_boundary() -> None:
    exact_gates = [
        {"name": f"body-{index}", "config": _regex_config()}
        for index in range(MAX_PIPELINE_GATES)
    ]
    exact = {
        "pipeline": {
            "gates": exact_gates,
            "default_decision": "allow",
        }
    }
    config = EgressGateConfig[RegexBodyConfig].model_validate(exact)
    assert len(config.pipeline.gates) == MAX_PIPELINE_GATES

    too_many_gates = [
        *exact_gates,
        {"name": "body-over", "config": _regex_config()},
    ]
    too_many = {
        "pipeline": {
            "gates": too_many_gates,
            "default_decision": "allow",
        }
    }
    with pytest.raises(ValidationError):
        EgressGateConfig[RegexBodyConfig].model_validate(too_many)


def test_regex_mode_requires_replacement_only_when_replacing() -> None:
    replace = _regex_config()
    replace["mode"] = "replace"
    with pytest.raises(ValidationError):
        EgressGateConfig[RegexBodyConfig].model_validate(
            {
                "pipeline": {
                    "gates": [{"name": "body", "config": replace}],
                    "default_decision": "allow",
                }
            }
        )
