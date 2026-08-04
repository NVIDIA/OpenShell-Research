"""Direct contract tests for the example-owned semantic gate."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from egress_gate.gates import GateRegistry, create_builtin_registry
from egress_gate.request import HttpRequest, HttpTarget, RequestContext
from egress_gate.result import EgressDecision, GateControl
from egress_gate.timeout import Timeout

_EXAMPLE_PATH = (
    Path(__file__).parents[2]
    / "examples"
    / "custom-semantic-gate"
    / "custom_semantic_gate.py"
)
_SPEC = importlib.util.spec_from_file_location(
    "custom_semantic_gate_example", _EXAMPLE_PATH
)
assert _SPEC is not None and _SPEC.loader is not None
_EXAMPLE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _EXAMPLE
_SPEC.loader.exec_module(_EXAMPLE)


def _request(body: bytes = b"ordinary") -> HttpRequest:
    return HttpRequest(
        context=RequestContext(request_id="request-1", sandbox_id="sandbox-1"),
        target=HttpTarget(
            scheme="https",
            host="api.example.com",
            port=443,
            method="POST",
            path="/v1/items",
            query="",
        ),
        headers=(),
        body=body,
    )


def _semantic_policy(*, mode: str) -> dict[str, object]:
    return {
        "pipeline": {
            "gates": [
                {
                    "name": "semantic",
                    "config": {
                        "gate": "semantic-judge",
                        "profile": "organization-default",
                        "policy": "The request is safe to inspect.",
                        "include": {
                            "target": True,
                            "body_max_bytes": 16_384,
                        },
                        "mode": mode,
                        "on_allow": "allow",
                        "deny_label": "semantic_denied",
                        "observation_label": "semantic_observed",
                        "deny_reason_code": "egress_gate_semantic_denied",
                    },
                }
            ],
            "default_decision": "allow",
        }
    }


def test_semantic_types_stay_out_of_the_core_default_registry() -> None:
    assert "custom-semantic" not in {
        item.gate_type for item in create_builtin_registry().describe_gates()
    }


def test_observation_always_proceeds_with_an_empty_patch() -> None:
    registry = _EXAMPLE.create_registry()
    config = registry.validate_config(_semantic_policy(mode="observe"))
    processor = registry.prepare_processor(
        config,
        timeout=Timeout.from_seconds(1),
    )

    result = processor.process(
        _request(b"contains [semantic-deny]"),
        timeout=Timeout.from_seconds(1),
    )

    assert result.decision is EgressDecision.ALLOW
    assert result.patch.is_empty
    assert result.findings[0].finding.label == "semantic_observed"


def test_privacy_first_judge_sees_redacted_body_and_allow_keeps_replacement() -> None:
    class RecordingJudgeClient:
        def __init__(self) -> None:
            self.requests: list[str] = []

        def judge(
            self,
            serialized_request: str,
            *,
            profile: str,
            policy: str,
            timeout: Timeout,
        ):
            del profile, policy
            timeout.raise_if_expired()
            self.requests.append(serialized_request)
            return _EXAMPLE.JudgeResult(decision="allow")

    judge = RecordingJudgeClient()
    registry = GateRegistry(include_builtin_gates=True)
    registry.register(
        _EXAMPLE.SemanticGate,
        resources=_EXAMPLE.SemanticGateResources(judge),
    )
    registry.finalize()
    config = registry.validate_config(
        {
            "pipeline": {
                "gates": [
                    {
                        "name": "redact",
                        "config": {
                            "gate": "regex-body",
                            "pattern_catalog": {
                                "entities": [
                                    {
                                        "name": "email",
                                        "rules": [
                                            {
                                                "pattern": r"alice@example[.]com",
                                                "confidence": "high",
                                            }
                                        ],
                                    }
                                ]
                            },
                            "mode": "replace",
                            "replacement": {
                                "strategy": "template",
                                "template": "[{entity}]",
                            },
                        },
                    },
                    {
                        "name": "semantic",
                        "config": {
                            "gate": "semantic-judge",
                            "profile": "organization-default",
                            "policy": "The request is safe to inspect.",
                            "include": {
                                "target": True,
                                "body_max_bytes": 16_384,
                            },
                            "mode": "enforce",
                            "on_allow": "allow",
                            "deny_label": "semantic_denied",
                            "observation_label": "semantic_observed",
                            "deny_reason_code": "egress_gate_semantic_denied",
                        },
                    },
                ],
                "default_decision": "allow",
            }
        }
    )
    processor = registry.prepare_processor(
        config,
        timeout=Timeout.from_seconds(1),
    )

    result = processor.process(
        _request(b"send alice@example.com"),
        timeout=Timeout.from_seconds(1),
    )

    assert result.decision is EgressDecision.ALLOW
    assert result.patch.replacement_body == b"send [email]"
    assert len(judge.requests) == 1
    payload = json.loads(judge.requests[0])
    assert payload["body"] == "send [email]"
    assert "alice@example.com" not in judge.requests[0]


def test_enforce_deny_uses_configured_stable_values() -> None:
    registry = _EXAMPLE.create_registry()
    config = registry.validate_config(_semantic_policy(mode="enforce"))
    processor = registry.prepare_processor(
        config,
        timeout=Timeout.from_seconds(1),
    )

    result = processor.process(
        _request(b"contains [semantic-deny]"),
        timeout=Timeout.from_seconds(1),
    )

    assert result.decision is EgressDecision.DENY
    assert result.reason_code == "egress_gate_semantic_denied"
    assert result.findings[0].finding.label == "semantic_denied"
    assert result.traces[0].control is GateControl.DENY
