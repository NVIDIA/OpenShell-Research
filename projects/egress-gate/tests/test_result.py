"""Focused invariant and boundary tests for Egress Gate result models."""

from __future__ import annotations

import math

import pytest
from pydantic import TypeAdapter, ValidationError

from egress_gate.constants import (
    DEFAULT_DENY_REASON_CODE,
    LIMIT_REASON_CODE,
    MAX_FINDING_COUNT,
    MAX_GATE_TRACES,
    MAX_PROTO_FINDING_BYTES,
    MAX_PROTO_FINDING_GROUPS,
    MAX_RESULT_METADATA_BYTES,
    MAX_RESULT_METADATA_ENTRIES,
    MAX_TRACE_MUTATION_KINDS,
)
from egress_gate.request import RequestMutations
from egress_gate.result import (
    DecisionSource,
    DecisionSourceKind,
    EgressDecision,
    EgressResult,
    Finding,
    GateControl,
    GateDecisionSource,
    GateEvaluation,
    GateTrace,
    MutationKind,
    PipelineDefaultDecisionSource,
    ResultMetadata,
    RuntimeLimitDecisionSource,
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


def test_finding_encoded_size_has_an_exact_four_kibibyte_boundary() -> None:
    exact = Finding(
        type="t" * 1024,
        label="l" * 1024,
        confidence="c" * 1024,
        severity="s" * 1010,
    )
    assert exact.encoded_size_bytes == MAX_PROTO_FINDING_BYTES

    with pytest.raises(ValidationError):
        Finding(
            type="t" * 1024,
            label="l" * 1024,
            confidence="c" * 1024,
            severity="s" * 1011,
        )
    assert MAX_PROTO_FINDING_BYTES == 4 * 1024


def test_gate_evaluation_helpers_and_control_invariants() -> None:
    finding = _finding()
    assert GateEvaluation.proceed(findings=(finding,)).control is GateControl.PROCEED
    assert GateEvaluation.allow().request_mutations.is_empty
    assert GateEvaluation.deny("egress_gate_blocked").reason_code == (
        "egress_gate_blocked"
    )

    with pytest.raises(ValidationError):
        GateEvaluation(
            control=GateControl.ALLOW,
            request_mutations=RequestMutations(replacement_body=b"x"),
        )
    with pytest.raises(ValidationError):
        GateEvaluation(control=GateControl.DENY)
    with pytest.raises(ValidationError):
        GateEvaluation(control=GateControl.PROCEED, reason_code="invalid_control")


@pytest.mark.parametrize("reason_code", ["", "UPPERCASE", "has-hyphen", "x" * 65])
def test_reason_codes_use_stable_identifier_format(reason_code: str) -> None:
    with pytest.raises(ValidationError):
        GateEvaluation.deny(reason_code)


def test_decision_source_keeps_gate_provenance_outside_finding() -> None:
    adapter = TypeAdapter(DecisionSource)
    discriminator = adapter.json_schema().get("discriminator")
    assert isinstance(discriminator, dict)
    assert discriminator.get("propertyName") == "kind"
    source = adapter.validate_python(
        {
            "kind": "gate",
            "gate_name": "identifiers",
            "gate_type": "regex",
        }
    )
    sourced = SourcedFinding(source_gate="identifiers", finding=_finding())

    assert isinstance(source, GateDecisionSource)
    assert source.kind is DecisionSourceKind.GATE
    assert sourced.finding.model_dump() == _finding().model_dump()
    assert "source_gate" not in sourced.finding.model_dump()

    with pytest.raises(ValidationError):
        adapter.validate_python({"kind": "gate", "gate_name": "identifiers"})
    with pytest.raises(ValidationError):
        adapter.validate_python({"kind": "runtime_limit", "gate_name": "identifiers"})


def test_egress_result_suppresses_mutations_on_deny_by_rejecting_them() -> None:
    finding = SourcedFinding(source_gate="identifiers", finding=_finding())
    allowed = EgressResult(
        decision=EgressDecision.ALLOW,
        decision_source=GateDecisionSource(
            kind=DecisionSourceKind.GATE, gate_name="identifiers", gate_type="regex"
        ),
        request_mutations=RequestMutations(replacement_body=b"redacted"),
        findings=(finding,),
    )
    assert allowed.request_mutations.replacement_body == b"redacted"

    with pytest.raises(ValidationError):
        EgressResult(
            decision=EgressDecision.DENY,
            decision_source=RuntimeLimitDecisionSource(
                kind=DecisionSourceKind.RUNTIME_LIMIT
            ),
            request_mutations=RequestMutations(replacement_body=b"must-not-leak"),
            reason_code="egress_gate_limit_exceeded",
        )
    with pytest.raises(ValidationError):
        EgressResult(
            decision=EgressDecision.DENY,
            decision_source=RuntimeLimitDecisionSource(
                kind=DecisionSourceKind.RUNTIME_LIMIT
            ),
        )
    with pytest.raises(ValidationError):
        EgressResult(
            decision=EgressDecision.ALLOW,
            decision_source=PipelineDefaultDecisionSource(
                kind=DecisionSourceKind.PIPELINE_DEFAULT
            ),
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
        decision_source=PipelineDefaultDecisionSource(
            kind=DecisionSourceKind.PIPELINE_DEFAULT
        ),
        findings=findings,
    )
    assert len(result.findings) == MAX_PROTO_FINDING_GROUPS
    assert finding.finding.type == "sensitive_entity"

    with pytest.raises(ValidationError):
        EgressResult(
            decision=EgressDecision.ALLOW,
            decision_source=PipelineDefaultDecisionSource(
                kind=DecisionSourceKind.PIPELINE_DEFAULT
            ),
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


def test_gate_evaluation_and_result_group_limits_have_exact_boundaries() -> None:
    findings = tuple(
        _finding(label=f"label-{index}") for index in range(MAX_PROTO_FINDING_GROUPS)
    )
    assert len(GateEvaluation.proceed(findings=findings).findings) == (
        MAX_PROTO_FINDING_GROUPS
    )
    with pytest.raises(ValidationError):
        GateEvaluation.proceed(findings=findings + (_finding(label="over"),))

    sourced = tuple(
        SourcedFinding(source_gate=f"gate-{index}", finding=finding)
        for index, finding in enumerate(findings)
    )
    result = EgressResult(
        decision=EgressDecision.ALLOW,
        decision_source=PipelineDefaultDecisionSource(
            kind=DecisionSourceKind.PIPELINE_DEFAULT
        ),
        findings=sourced,
    )
    assert len(result.findings) == MAX_PROTO_FINDING_GROUPS
    with pytest.raises(ValidationError):
        EgressResult(
            decision=EgressDecision.ALLOW,
            decision_source=PipelineDefaultDecisionSource(
                kind=DecisionSourceKind.PIPELINE_DEFAULT
            ),
            findings=sourced
            + (SourcedFinding(source_gate="over", finding=_finding(label="over")),),
        )


def test_metadata_count_and_aggregate_byte_limits_have_exact_boundaries() -> None:
    entries = tuple(
        ResultMetadata(
            key=f"k{index}",
            value="v" * (510 if index < 10 else 509),
        )
        for index in range(MAX_RESULT_METADATA_ENTRIES)
    )
    result = EgressResult(
        decision=EgressDecision.ALLOW,
        decision_source=PipelineDefaultDecisionSource(
            kind=DecisionSourceKind.PIPELINE_DEFAULT
        ),
        metadata=entries,
    )
    assert len(result.metadata) == MAX_RESULT_METADATA_ENTRIES
    assert (
        sum(len(entry.key.encode()) + len(entry.value.encode()) for entry in entries)
        == MAX_RESULT_METADATA_BYTES
    )

    with pytest.raises(ValidationError):
        EgressResult(
            decision=EgressDecision.ALLOW,
            decision_source=PipelineDefaultDecisionSource(
                kind=DecisionSourceKind.PIPELINE_DEFAULT
            ),
            metadata=entries[:-1]
            + (
                ResultMetadata(
                    key="k63",
                    value="v" * 510,
                ),
            ),
        )
    with pytest.raises(ValidationError):
        EgressResult(
            decision=EgressDecision.ALLOW,
            decision_source=PipelineDefaultDecisionSource(
                kind=DecisionSourceKind.PIPELINE_DEFAULT
            ),
            metadata=entries + (ResultMetadata(key="over", value="v"),),
        )


def test_trace_count_and_mutation_kind_limits_have_exact_boundaries() -> None:
    trace = GateTrace(
        gate_name="gate",
        gate_type="test",
        control=GateControl.PROCEED,
        duration_ms=0,
        finding_count=0,
        mutation_kinds=(MutationKind.BODY, MutationKind.HEADERS),
    )
    assert len(trace.mutation_kinds) == MAX_TRACE_MUTATION_KINDS
    with pytest.raises(ValidationError):
        GateTrace(
            gate_name="gate",
            gate_type="test",
            control=GateControl.PROCEED,
            duration_ms=0,
            finding_count=0,
            mutation_kinds=(
                MutationKind.BODY,
                MutationKind.HEADERS,
                MutationKind.BODY,
            ),
        )

    traces = tuple(
        trace.model_copy(update={"gate_name": f"gate-{index}"})
        for index in range(MAX_GATE_TRACES)
    )
    result = EgressResult(
        decision=EgressDecision.ALLOW,
        decision_source=PipelineDefaultDecisionSource(
            kind=DecisionSourceKind.PIPELINE_DEFAULT
        ),
        traces=traces,
    )
    assert len(result.traces) == MAX_GATE_TRACES
    with pytest.raises(ValidationError):
        EgressResult(
            decision=EgressDecision.ALLOW,
            decision_source=PipelineDefaultDecisionSource(
                kind=DecisionSourceKind.PIPELINE_DEFAULT
            ),
            traces=traces + (trace,),
        )


def test_decision_source_reason_code_ownership_is_strict() -> None:
    default_deny = EgressResult(
        decision=EgressDecision.DENY,
        decision_source=PipelineDefaultDecisionSource(
            kind=DecisionSourceKind.PIPELINE_DEFAULT
        ),
        reason_code=DEFAULT_DENY_REASON_CODE,
    )
    runtime_limit = EgressResult(
        decision=EgressDecision.DENY,
        decision_source=RuntimeLimitDecisionSource(
            kind=DecisionSourceKind.RUNTIME_LIMIT
        ),
        reason_code=LIMIT_REASON_CODE,
    )
    assert default_deny.reason_code == DEFAULT_DENY_REASON_CODE
    assert runtime_limit.reason_code == LIMIT_REASON_CODE

    with pytest.raises(ValidationError):
        EgressResult(
            decision=EgressDecision.ALLOW,
            decision_source=RuntimeLimitDecisionSource(
                kind=DecisionSourceKind.RUNTIME_LIMIT
            ),
        )
    with pytest.raises(ValidationError):
        EgressResult(
            decision=EgressDecision.DENY,
            decision_source=RuntimeLimitDecisionSource(
                kind=DecisionSourceKind.RUNTIME_LIMIT
            ),
            reason_code=DEFAULT_DENY_REASON_CODE,
        )
    with pytest.raises(ValidationError):
        EgressResult(
            decision=EgressDecision.DENY,
            decision_source=PipelineDefaultDecisionSource(
                kind=DecisionSourceKind.PIPELINE_DEFAULT
            ),
            reason_code=LIMIT_REASON_CODE,
        )
