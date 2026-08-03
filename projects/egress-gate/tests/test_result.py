"""Focused invariant and boundary tests for Egress Gate result models."""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from egress_gate.constants import MAX_FINDING_COUNT, MAX_PROTO_FINDING_GROUPS
from egress_gate.request import RequestPatch
from egress_gate.result import (
    DecisionSource,
    DecisionSourceKind,
    EgressDecision,
    EgressResult,
    Finding,
    GateControl,
    GateEvaluation,
    GateTrace,
    MutationKind,
    SourcedFinding,
)


def _finding(**values: object) -> Finding:
    defaults: dict[str, object] = {"type": "sensitive_entity", "label": "email"}
    defaults.update(values)
    return Finding.model_validate(defaults)


def test_finding_matches_the_current_five_field_wire_contract() -> None:
    finding = _finding(count=MAX_FINDING_COUNT, confidence="high", severity="medium")

    assert set(Finding.model_fields) == {
        "type",
        "label",
        "count",
        "confidence",
        "severity",
    }
    assert finding.count == MAX_FINDING_COUNT

    with pytest.raises(ValidationError):
        Finding.model_validate(
            {"type": "sensitive_entity", "label": "email", "source_gate": "hidden"}
        )


@pytest.mark.parametrize("count", [0, MAX_FINDING_COUNT + 1])
def test_finding_count_is_bounded(count: int) -> None:
    with pytest.raises(ValidationError):
        _finding(count=count)


def test_gate_evaluation_helpers_and_control_invariants() -> None:
    finding = _finding()
    assert GateEvaluation.proceed(findings=(finding,)).control is GateControl.PROCEED
    assert GateEvaluation.allow().patch.is_empty
    assert GateEvaluation.deny("egress_gate_blocked").reason_code == (
        "egress_gate_blocked"
    )

    with pytest.raises(ValidationError):
        GateEvaluation(
            control=GateControl.ALLOW, patch=RequestPatch(replacement_body=b"x")
        )
    with pytest.raises(ValidationError):
        GateEvaluation(control=GateControl.DENY)
    with pytest.raises(ValidationError):
        GateEvaluation(control=GateControl.PROCEED, reason_code="invalid_control")


def test_decision_source_keeps_gate_provenance_outside_finding() -> None:
    source = DecisionSource.gate(name="identifiers", gate_type="regex")
    sourced = SourcedFinding(source_gate="identifiers", finding=_finding())

    assert source.kind is DecisionSourceKind.GATE
    assert sourced.finding.model_dump() == _finding().model_dump()
    assert "source_gate" not in sourced.finding.model_dump()

    with pytest.raises(ValidationError):
        DecisionSource(kind=DecisionSourceKind.GATE, gate_name="identifiers")
    with pytest.raises(ValidationError):
        DecisionSource(kind=DecisionSourceKind.RUNTIME_LIMIT, gate_name="identifiers")


def test_egress_result_suppresses_mutations_on_deny_by_rejecting_them() -> None:
    finding = SourcedFinding(source_gate="identifiers", finding=_finding())
    allowed = EgressResult(
        decision=EgressDecision.ALLOW,
        decision_source=DecisionSource.gate(name="identifiers", gate_type="regex"),
        patch=RequestPatch(replacement_body=b"redacted"),
        findings=(finding,),
    )
    assert allowed.patch.replacement_body == b"redacted"

    with pytest.raises(ValidationError):
        EgressResult(
            decision=EgressDecision.DENY,
            decision_source=DecisionSource.runtime_limit(),
            patch=RequestPatch(replacement_body=b"must-not-leak"),
            reason_code="egress_gate_limit_exceeded",
        )
    with pytest.raises(ValidationError):
        EgressResult(
            decision=EgressDecision.DENY,
            decision_source=DecisionSource.runtime_limit(),
        )
    with pytest.raises(ValidationError):
        EgressResult(
            decision=EgressDecision.ALLOW,
            decision_source=DecisionSource.pipeline_default(),
            reason_code="not-allowed-on-allow",
        )


def test_egress_result_limits_finding_groups_and_trace_values() -> None:
    finding = SourcedFinding(source_gate="identifiers", finding=_finding())
    findings = tuple(
        SourcedFinding(
            source_gate=f"gate-{index}", finding=_finding(label=f"label-{index}")
        )
        for index in range(MAX_PROTO_FINDING_GROUPS)
    )
    result = EgressResult(
        decision=EgressDecision.ALLOW,
        decision_source=DecisionSource.pipeline_default(),
        findings=findings,
    )
    assert len(result.findings) == MAX_PROTO_FINDING_GROUPS
    assert finding.finding.type == "sensitive_entity"

    with pytest.raises(ValidationError):
        EgressResult(
            decision=EgressDecision.ALLOW,
            decision_source=DecisionSource.pipeline_default(),
            findings=findings + (finding,),
        )

    trace = GateTrace(
        gate_name="identifiers",
        gate_type="regex",
        control=GateControl.PROCEED,
        duration_ms=0,
        finding_count=0,
        mutation_kinds=(MutationKind.BODY,),
    )
    assert trace.duration_ms == 0
    with pytest.raises(ValidationError):
        GateTrace(
            gate_name="identifiers",
            gate_type="regex",
            control=GateControl.PROCEED,
            duration_ms=math.inf,
            finding_count=0,
        )
