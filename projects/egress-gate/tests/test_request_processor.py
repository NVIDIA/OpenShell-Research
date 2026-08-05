"""Ordered current-request execution and decision-provenance tests."""

from __future__ import annotations

import inspect
from time import monotonic
from typing import Literal

import pytest

from egress_gate.config import DefaultDecision
from egress_gate.constants import (
    DEFAULT_DENY_REASON_CODE,
    LIMIT_REASON_CODE,
    MAX_FINDING_COUNT,
    MAX_HEADER_MUTATIONS,
    MAX_PROTO_FINDING_GROUPS,
)
from egress_gate.errors import (
    EgressGateError,
    ErrorCode,
    GateContractError,
)
from egress_gate.gates import (
    Gate,
    GateCapabilities,
    GateConfig,
    GateRegistry,
)
from egress_gate.request import (
    ExistingHeaderAction,
    HttpHeader,
    HttpRequest,
    HttpTarget,
    RemoveHeaderMutation,
    RequestContext,
    RequestMutations,
    WriteHeaderMutation,
)
from egress_gate.request_processor import RequestProcessor, apply_request_mutations
from egress_gate.result import (
    DecisionSourceKind,
    EgressDecision,
    Finding,
    FindingTypeDefinition,
    GateDecisionSource,
    GateEvaluation,
)
from egress_gate.timeout import Timeout


class _ControlConfig(GateConfig):
    kind: Literal["test-control"]
    control: Literal["proceed", "allow", "deny"] = "proceed"
    replacement: str | None = None
    expected_body: str | None = None
    header_value: str | None = None
    header_count: int = 0
    finding_label: str | None = None
    finding_count: int = 1
    emit_twice: bool = False
    reason_code: str | None = None


class _ControlGate(Gate[_ControlConfig, None]):
    capabilities = GateCapabilities(
        reads_body=True,
        replaces_body=True,
        mutates_headers=True,
        produces_findings=True,
        may_allow=True,
        may_deny=True,
    )
    finding_types = (FindingTypeDefinition(type="test_observation"),)

    def _evaluate(
        self,
        request: HttpRequest,
        *,
        timeout: Timeout,
    ) -> GateEvaluation:
        timeout.raise_if_expired()
        if (
            self.config.expected_body is not None
            and request.body.decode("utf-8") != self.config.expected_body
        ):
            raise AssertionError("later gate did not see the current request")
        findings: tuple[Finding, ...] = ()
        if self.config.finding_label is not None:
            finding = Finding(
                type="test_observation",
                label=self.config.finding_label,
                count=self.config.finding_count,
            )
            findings = (finding,)
            if self.config.emit_twice:
                findings += (finding,)
        if self.config.control == "deny":
            return GateEvaluation.deny(
                self.config.reason_code or "egress_gate_test_denied",
                findings=findings,
            )
        if self.config.control == "allow":
            return GateEvaluation.allow(findings=findings)
        mutations: tuple[WriteHeaderMutation, ...] = tuple(
            WriteHeaderMutation(
                kind="write",
                name=f"x-openshell-middleware-test-{index}",
                value=self.config.header_value or "true",
                on_existing=ExistingHeaderAction.OVERWRITE,
            )
            for index in range(self.config.header_count)
        )
        if self.config.header_value is not None and not mutations:
            mutations = (
                WriteHeaderMutation(
                    kind="write",
                    name="x-openshell-middleware-test",
                    value=self.config.header_value,
                    on_existing=ExistingHeaderAction.OVERWRITE,
                ),
            )
        return GateEvaluation.proceed(
            request_mutations=RequestMutations(
                replacement_body=(
                    None
                    if self.config.replacement is None
                    else self.config.replacement.encode("utf-8")
                ),
                header_mutations=mutations,
            ),
            findings=findings,
        )


def _request(
    *, body: bytes = b"original", headers: tuple[HttpHeader, ...] = ()
) -> HttpRequest:
    return HttpRequest(
        context=RequestContext(request_id="request-1", sandbox_id="sandbox-1"),
        target=HttpTarget(
            scheme="https",
            host="example.com",
            port=443,
            method="POST",
            path="/",
            query="",
        ),
        headers=headers,
        body=body,
    )


def _regex_config(
    action_kind: str = "detect",
    *,
    scan: dict[str, object] | None = None,
) -> dict[str, object]:
    scan_values = {"kind": "body"} if scan is None else dict(scan)
    action: dict[str, object] = {"kind": action_kind}
    if action_kind == "replace":
        action["template"] = "[{entity}]"
    scan_values["action"] = action
    return {
        "kind": "regex",
        "scan": scan_values,
        "pattern_catalog": {
            "entities": [
                {
                    "name": "token",
                    "rules": [{"pattern": "secret", "confidence": "high"}],
                }
            ]
        },
    }


def _processor(
    gate_values: tuple[tuple[str, dict[str, object]], ...],
    *,
    default_decision: DefaultDecision = DefaultDecision.ALLOW,
    include_regex: bool = False,
) -> RequestProcessor:
    registry = GateRegistry(include_builtin_gates=include_regex)
    registry.register(_ControlGate)
    registry.finalize()
    values = {
        "gates": [{"name": name, **config} for name, config in gate_values],
        "default_decision": default_decision.value,
    }
    config = registry.validate_config(values)
    prepared_items = []
    for entry in config.gates:
        gate_type = getattr(entry, "kind", None)
        if not isinstance(gate_type, str):
            raise AssertionError("test gate config has no discriminator")
        prepared_items.append((entry.name, gate_type, registry.create_gate(entry)))
    prepared = tuple(prepared_items)
    return RequestProcessor(
        config,
        prepared,
        policy_fingerprint="policy-fingerprint",
    )


def test_processor_process_requires_the_service_created_timeout() -> None:
    process_signature = inspect.signature(RequestProcessor.process)
    assert "timeout_seconds" not in inspect.signature(RequestProcessor).parameters
    assert (
        process_signature.parameters["timeout"].kind is inspect.Parameter.KEYWORD_ONLY
    )

    processor = _processor((("one", {"kind": "test-control", "control": "proceed"}),))
    result = processor.process(_request(), timeout=Timeout.from_seconds(1))
    assert result.decision is EgressDecision.ALLOW


def test_processor_applies_mutations_to_the_current_request_and_preserves_intent() -> (
    None
):
    processor = _processor(
        (
            (
                "redact",
                {
                    "kind": "test-control",
                    "replacement": "redacted",
                    "finding_label": "secret",
                },
            ),
            (
                "observe",
                {
                    "kind": "test-control",
                    "expected_body": "redacted",
                    "header_value": "true",
                    "finding_label": "observed",
                },
            ),
        )
    )

    result = processor.process(_request(), timeout=Timeout.from_seconds(1))

    assert result.decision is EgressDecision.ALLOW
    assert result.decision_source.kind is DecisionSourceKind.PIPELINE_DEFAULT
    assert result.request_mutations.replacement_body == b"redacted"
    mutation = result.request_mutations.header_mutations[0]
    assert isinstance(mutation, WriteHeaderMutation)
    assert mutation.value == "true"
    assert [(item.source_gate, item.finding.label) for item in result.findings] == [
        ("redact", "secret"),
        ("observe", "observed"),
    ]
    assert [trace.gate_type for trace in result.traces] == [
        "test-control",
        "test-control",
    ]
    assert result.policy_fingerprint == "policy-fingerprint"


def test_regex_gate_sees_header_mutations_from_an_earlier_gate() -> None:
    processor = _processor(
        (
            (
                "add-header",
                {
                    "kind": "test-control",
                    "header_value": "contains secret",
                },
            ),
            (
                "inspect-header",
                _regex_config(
                    "deny",
                    scan={
                        "kind": "header",
                        "names": ["x-openshell-middleware-test"],
                    },
                ),
            ),
        ),
        include_regex=True,
    )

    result = processor.process(_request(), timeout=Timeout.from_seconds(1))

    assert result.decision is EgressDecision.DENY
    assert isinstance(result.decision_source, GateDecisionSource)
    assert result.decision_source.gate_name == "inspect-header"
    assert result.request_mutations.is_empty


def test_processor_aggregates_equivalent_findings_by_gate_provenance() -> None:
    processor = _processor(
        (
            (
                "one",
                {"kind": "test-control", "finding_label": "same", "emit_twice": True},
            ),
        )
    )
    result = processor.process(_request(), timeout=Timeout.from_seconds(1))

    assert len(result.findings) == 1
    assert result.findings[0].source_gate == "one"
    assert result.findings[0].finding.count == 2


def test_terminal_decisions_skip_later_gates() -> None:
    deny = _processor(
        (
            (
                "deny",
                {
                    "kind": "test-control",
                    "control": "deny",
                    "reason_code": "policy_denied",
                },
            ),
            (
                "never",
                {
                    "kind": "test-control",
                    "expected_body": "this gate must not run",
                },
            ),
        )
    )
    allow = _processor(
        (
            ("allow", {"kind": "test-control", "control": "allow"}),
            (
                "never",
                {
                    "kind": "test-control",
                    "expected_body": "this gate must not run",
                },
            ),
        )
    )

    denied = deny.process(_request(), timeout=Timeout.from_seconds(1))
    allowed = allow.process(_request(), timeout=Timeout.from_seconds(1))

    assert denied.decision is EgressDecision.DENY
    assert denied.decision_source.kind is DecisionSourceKind.GATE
    assert isinstance(denied.decision_source, GateDecisionSource)
    assert denied.decision_source.gate_name == "deny"
    assert denied.reason_code == "policy_denied"
    assert allowed.decision is EgressDecision.ALLOW
    assert isinstance(allowed.decision_source, GateDecisionSource)
    assert allowed.decision_source.gate_name == "allow"


def test_default_deny_owns_its_reason_and_discards_accumulated_mutations() -> None:
    processor = _processor(
        (("redact", {"kind": "test-control", "replacement": "redacted"}),),
        default_decision=DefaultDecision.DENY,
    )

    result = processor.process(_request(), timeout=Timeout.from_seconds(1))

    assert result.decision is EgressDecision.DENY
    assert result.decision_source.kind is DecisionSourceKind.PIPELINE_DEFAULT
    assert result.reason_code == DEFAULT_DENY_REASON_CODE
    assert result.request_mutations.is_empty


def test_expired_shared_timeout_returns_atomic_runtime_limit_result() -> None:
    processor = _processor((("one", {"kind": "test-control", "control": "proceed"}),))

    result = processor.process(
        _request(),
        timeout=Timeout(deadline=monotonic() - 1),
    )

    assert result.decision is EgressDecision.DENY
    assert result.decision_source.kind is DecisionSourceKind.RUNTIME_LIMIT
    assert result.reason_code == LIMIT_REASON_CODE
    assert result.request_mutations.is_empty


def test_regex_finding_group_overflow_is_an_atomic_runtime_limit() -> None:
    processor = _processor(
        (
            (
                "regex",
                {
                    "kind": "regex",
                    "scan": {"kind": "body", "action": {"kind": "detect"}},
                    "pattern_catalog": {
                        "entities": [
                            {
                                "name": f"entity-{index}",
                                "rules": [{"pattern": "x", "confidence": "high"}],
                            }
                            for index in range(MAX_PROTO_FINDING_GROUPS + 1)
                        ]
                    },
                },
            ),
        ),
        include_regex=True,
    )

    result = processor.process(
        _request(body=b"x"),
        timeout=Timeout.from_seconds(1),
    )

    assert result.decision is EgressDecision.DENY
    assert result.decision_source.kind is DecisionSourceKind.RUNTIME_LIMIT
    assert result.reason_code == LIMIT_REASON_CODE
    assert not result.findings
    assert result.request_mutations.is_empty


def test_composed_header_mutation_overflow_is_an_atomic_runtime_limit() -> None:
    processor = _processor(
        (
            (
                "first",
                {"kind": "test-control", "header_count": MAX_HEADER_MUTATIONS // 2},
            ),
            (
                "second",
                {
                    "kind": "test-control",
                    "header_count": MAX_HEADER_MUTATIONS // 2 + 1,
                },
            ),
        )
    )

    result = processor.process(_request(), timeout=Timeout.from_seconds(1))

    assert result.decision is EgressDecision.DENY
    assert result.decision_source.kind is DecisionSourceKind.RUNTIME_LIMIT
    assert result.reason_code == LIMIT_REASON_CODE
    assert result.request_mutations.is_empty
    assert result.findings == ()
    assert result.traces == ()


def test_trace_finding_count_overflow_is_an_atomic_runtime_limit() -> None:
    processor = _processor(
        (
            (
                "observations",
                {
                    "kind": "test-control",
                    "finding_label": "same",
                    "finding_count": MAX_FINDING_COUNT,
                    "emit_twice": True,
                },
            ),
        )
    )

    result = processor.process(_request(), timeout=Timeout.from_seconds(1))

    assert result.decision is EgressDecision.DENY
    assert result.decision_source.kind is DecisionSourceKind.RUNTIME_LIMIT
    assert result.reason_code == LIMIT_REASON_CODE
    assert result.request_mutations.is_empty
    assert result.findings == ()
    assert result.traces == ()


def test_invalid_utf8_is_translated_to_the_stable_input_error() -> None:
    processor = _processor(
        (("regex", _regex_config()),),
        include_regex=True,
    )

    with pytest.raises(EgressGateError) as error:
        processor.process(_request(body=b"\xff"), timeout=Timeout.from_seconds(1))

    assert error.value.code is ErrorCode.BODY_ENCODING_INVALID


def test_prepared_gate_type_is_part_of_the_processor_contract() -> None:
    processor = _processor((("one", {"kind": "test-control", "control": "proceed"}),))
    config = processor._config
    gate = processor._gates[0][2]

    with pytest.raises(ValueError):
        RequestProcessor(
            config,
            (("one", "wrong-type", gate),),
        )


def test_header_mutations_are_ordered_and_protected() -> None:
    original = _request(
        headers=(
            HttpHeader(name="x-openshell-middleware-test", value="old"),
            HttpHeader(name="x-other", value="keep"),
        )
    )
    request_mutations = RequestMutations(
        header_mutations=(
            WriteHeaderMutation(
                kind="write",
                name="x-openshell-middleware-test",
                value="new",
                on_existing=ExistingHeaderAction.OVERWRITE,
            ),
            WriteHeaderMutation(
                kind="write",
                name="x-openshell-middleware-added",
                value="one",
                on_existing=ExistingHeaderAction.APPEND,
            ),
            WriteHeaderMutation(
                kind="write",
                name="x-openshell-middleware-added",
                value="two",
                on_existing=ExistingHeaderAction.SKIP,
            ),
            RemoveHeaderMutation(kind="remove", name="x-other"),
        )
    )
    updated = apply_request_mutations(original, request_mutations)

    assert updated.headers == (
        HttpHeader(name="x-openshell-middleware-test", value="new"),
        HttpHeader(name="x-openshell-middleware-added", value="one"),
    )

    with pytest.raises(GateContractError):
        apply_request_mutations(
            original,
            RequestMutations(
                header_mutations=(
                    WriteHeaderMutation(
                        kind="write",
                        name="authorization",
                        value="secret",
                        on_existing=ExistingHeaderAction.APPEND,
                    ),
                )
            ),
        )
