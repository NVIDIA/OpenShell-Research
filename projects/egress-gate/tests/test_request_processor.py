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
    MAX_HEADER_MUTATION_DATA_BYTES,
    MAX_HEADER_MUTATIONS,
    MAX_PROTO_FINDING_GROUPS,
    MAX_PROTO_HEADERS,
    MAX_PROTO_HEADERS_BYTES,
)
from egress_gate.errors import (
    EgressGateError,
    ErrorCode,
    GateContractError,
)
from egress_gate.gates import (
    Gate,
    GateCapability,
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

_BOUNDARY_FINDING_TYPE = "t" * 1024


class _ControlConfig(GateConfig):
    kind: Literal["test-control"]
    control: Literal["proceed", "allow", "deny"] = "proceed"
    replacement: str | None = None
    expected_body: str | None = None
    expected_header_name: str | None = None
    expected_header_value: str | None = None
    header_value: str | None = None
    header_name: str = "x-openshell-middleware-test"
    header_action: Literal["append", "overwrite", "skip"] = "overwrite"
    remove_header: str | None = None
    header_count: int = 0
    finding_label: str | None = None
    finding_count: int = 1
    emit_twice: bool = False
    boundary_finding: bool = False
    reason_code: str | None = None


class _ControlGate(Gate[_ControlConfig, None]):
    capabilities = frozenset(
        {
            GateCapability.READ_BODY,
            GateCapability.REPLACE_BODY,
            GateCapability.MUTATE_HEADERS,
            GateCapability.ALLOW,
            GateCapability.DENY,
        }
    )
    finding_types = (
        FindingTypeDefinition(type="test_observation"),
        FindingTypeDefinition(type=_BOUNDARY_FINDING_TYPE),
    )

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
        if self.config.expected_header_name is not None:
            values = tuple(
                header.value
                for header in request.headers
                if header.name.lower() == self.config.expected_header_name.lower()
            )
            expected = (
                ()
                if self.config.expected_header_value is None
                else (self.config.expected_header_value,)
            )
            if values != expected:
                raise AssertionError("later gate did not see current request headers")
        findings: tuple[Finding, ...] = ()
        if self.config.boundary_finding:
            finding = Finding(
                type=_BOUNDARY_FINDING_TYPE,
                label="x" * 1024,
                count=64,
                confidence="c" * 1024,
                severity="s" * 1010,
            )
            findings = (finding, finding)
        elif self.config.finding_label is not None:
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
        mutations: tuple[WriteHeaderMutation | RemoveHeaderMutation, ...] = tuple(
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
                    name=self.config.header_name,
                    value=self.config.header_value,
                    on_existing=ExistingHeaderAction(self.config.header_action),
                ),
            )
        if self.config.remove_header is not None:
            mutations += (
                RemoveHeaderMutation(kind="remove", name=self.config.remove_header),
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
    entity: str = "token",
    pattern: str = "secret",
    template: str = "[{entity}]",
) -> dict[str, object]:
    scan_values = {"kind": "body"} if scan is None else dict(scan)
    action: dict[str, object] = {"kind": action_kind}
    if action_kind == "replace":
        action["template"] = template
    scan_values["action"] = action
    return {
        "kind": "regex",
        "scan": scan_values,
        "pattern_catalog": {
            "entities": [
                {
                    "name": entity,
                    "rules": [{"pattern": pattern, "confidence": "high"}],
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


def test_three_gates_aggregate_interacting_body_and_header_mutations() -> None:
    original = _request(
        body=b"original secret",
        headers=(
            HttpHeader(name="X-OpenShell-Middleware-State", value="old-one"),
            HttpHeader(name="x-openshell-middleware-state", value="old-two"),
            HttpHeader(name="x-remove", value="discard"),
            HttpHeader(name="x-keep", value="preserve"),
        ),
    )
    processor = _processor(
        (
            (
                "first",
                {
                    "kind": "test-control",
                    "replacement": "first redaction",
                    "header_name": "x-openshell-middleware-state",
                    "header_value": "stage-one",
                    "header_action": "overwrite",
                },
            ),
            (
                "second",
                {
                    "kind": "test-control",
                    "expected_body": "first redaction",
                    "expected_header_name": "x-openshell-middleware-state",
                    "expected_header_value": "stage-one",
                    "replacement": "",
                    "header_name": "x-openshell-middleware-chain",
                    "header_value": "stage-two",
                    "header_action": "append",
                    "remove_header": "x-openshell-middleware-state",
                },
            ),
            (
                "third",
                {
                    "kind": "test-control",
                    "expected_body": "",
                    "expected_header_name": "x-openshell-middleware-state",
                    "replacement": "final body",
                    "header_name": "x-openshell-middleware-state",
                    "header_value": "stage-three",
                    "header_action": "append",
                    "remove_header": "x-remove",
                },
            ),
        )
    )

    result = processor.process(original, timeout=Timeout.from_seconds(1))
    final_request = apply_request_mutations(original, result.request_mutations)
    first_mutations = RequestMutations(
        replacement_body=b"first redaction",
        header_mutations=(
            WriteHeaderMutation(
                kind="write",
                name="x-openshell-middleware-state",
                value="stage-one",
                on_existing=ExistingHeaderAction.OVERWRITE,
            ),
        ),
    )
    second_mutations = RequestMutations(
        replacement_body=b"",
        header_mutations=(
            WriteHeaderMutation(
                kind="write",
                name="x-openshell-middleware-chain",
                value="stage-two",
                on_existing=ExistingHeaderAction.APPEND,
            ),
            RemoveHeaderMutation(
                kind="remove",
                name="x-openshell-middleware-state",
            ),
        ),
    )
    third_mutations = RequestMutations(
        replacement_body=b"final body",
        header_mutations=(
            WriteHeaderMutation(
                kind="write",
                name="x-openshell-middleware-state",
                value="stage-three",
                on_existing=ExistingHeaderAction.APPEND,
            ),
            RemoveHeaderMutation(kind="remove", name="x-remove"),
        ),
    )
    sequential_request = original
    for mutations in (first_mutations, second_mutations, third_mutations):
        sequential_request = apply_request_mutations(sequential_request, mutations)

    assert result.decision is EgressDecision.ALLOW
    assert result.request_mutations == RequestMutations(
        replacement_body=b"final body",
        header_mutations=(
            first_mutations.header_mutations
            + second_mutations.header_mutations
            + third_mutations.header_mutations
        ),
    )
    assert final_request == sequential_request
    assert final_request.body == b"final body"
    assert final_request.headers == (
        HttpHeader(name="x-keep", value="preserve"),
        HttpHeader(name="x-openshell-middleware-chain", value="stage-two"),
        HttpHeader(name="x-openshell-middleware-state", value="stage-three"),
    )


def test_three_regex_gates_progressively_redact_the_current_body() -> None:
    original = _request(
        body=(
            b"Customer record\n"
            b"email: alice@example.com\n"
            b"api key: sk-123456\n"
            b"phone: 555-0100\n"
        )
    )
    processor = _processor(
        (
            (
                "redact-email",
                _regex_config(
                    "replace",
                    entity="email",
                    pattern=r"alice@example\.com",
                ),
            ),
            (
                "redact-api-key",
                _regex_config(
                    "replace",
                    entity="api_key",
                    pattern=r"sk-[0-9]+",
                ),
            ),
            (
                "redact-phone",
                _regex_config(
                    "replace",
                    entity="phone",
                    pattern=r"555-[0-9]{4}",
                ),
            ),
        ),
        include_regex=True,
    )

    result = processor.process(original, timeout=Timeout.from_seconds(1))
    final_request = apply_request_mutations(original, result.request_mutations)

    expected_body = (
        b"Customer record\nemail: [email]\napi key: [api_key]\nphone: [phone]\n"
    )
    assert result.decision is EgressDecision.ALLOW
    assert result.request_mutations.replacement_body == expected_body
    assert final_request.body == expected_body
    assert [(item.source_gate, item.finding.type) for item in result.findings] == [
        ("redact-email", "regex_match"),
        ("redact-api-key", "regex_match"),
        ("redact-phone", "regex_match"),
    ]


def test_later_regex_gates_use_the_body_after_overlapping_text_is_redacted() -> None:
    original = _request(body=b"credential: alice@example.com")
    processor = _processor(
        (
            (
                "redact-email",
                _regex_config(
                    "replace",
                    entity="email",
                    pattern=r"alice@example\.com",
                ),
            ),
            (
                "redact-original-domain",
                _regex_config(
                    "replace",
                    entity="domain",
                    pattern=r"example\.com",
                ),
            ),
            (
                "classify-redaction",
                _regex_config(
                    "replace",
                    entity="redacted_email",
                    pattern=r"\[email\]",
                    template="<{entity}>",
                ),
            ),
        ),
        include_regex=True,
    )

    result = processor.process(original, timeout=Timeout.from_seconds(1))
    final_request = apply_request_mutations(original, result.request_mutations)

    assert final_request.body == b"credential: <redacted_email>"
    assert [(item.source_gate, item.finding.label) for item in result.findings] == [
        ("redact-email", "email"),
        ("classify-redaction", "redacted_email"),
    ]


def test_final_empty_body_replacement_is_preserved_across_gates() -> None:
    original = _request(body=b"original")
    processor = _processor(
        (
            ("first", {"kind": "test-control", "replacement": "intermediate"}),
            (
                "second",
                {
                    "kind": "test-control",
                    "expected_body": "intermediate",
                    "replacement": "",
                },
            ),
            (
                "observe",
                {
                    "kind": "test-control",
                    "expected_body": "",
                },
            ),
        )
    )

    result = processor.process(original, timeout=Timeout.from_seconds(1))
    final_request = apply_request_mutations(original, result.request_mutations)

    assert result.decision is EgressDecision.ALLOW
    assert result.request_mutations.replacement_body == b""
    assert final_request.body == b""


def test_header_skip_and_append_remain_ordered_across_gates() -> None:
    original = _request(
        headers=(HttpHeader(name="X-OpenShell-Middleware-State", value="original"),)
    )
    processor = _processor(
        (
            (
                "overwrite",
                {
                    "kind": "test-control",
                    "header_name": "x-openshell-middleware-state",
                    "header_value": "first",
                    "header_action": "overwrite",
                },
            ),
            (
                "skip",
                {
                    "kind": "test-control",
                    "expected_header_name": "x-openshell-middleware-state",
                    "expected_header_value": "first",
                    "header_name": "x-openshell-middleware-state",
                    "header_value": "ignored",
                    "header_action": "skip",
                },
            ),
            (
                "append",
                {
                    "kind": "test-control",
                    "expected_header_name": "x-openshell-middleware-state",
                    "expected_header_value": "first",
                    "header_name": "x-openshell-middleware-state",
                    "header_value": "second",
                    "header_action": "append",
                },
            ),
        )
    )

    result = processor.process(original, timeout=Timeout.from_seconds(1))
    final_request = apply_request_mutations(original, result.request_mutations)

    assert final_request.headers == (
        HttpHeader(name="x-openshell-middleware-state", value="first"),
        HttpHeader(name="x-openshell-middleware-state", value="second"),
    )


def test_terminal_allow_returns_mutations_from_prior_gates() -> None:
    original = _request(body=b"original")
    processor = _processor(
        (
            ("body", {"kind": "test-control", "replacement": "updated"}),
            (
                "header",
                {
                    "kind": "test-control",
                    "expected_body": "updated",
                    "header_name": "x-openshell-middleware-reviewed",
                    "header_value": "true",
                },
            ),
            (
                "allow",
                {
                    "kind": "test-control",
                    "control": "allow",
                    "expected_body": "updated",
                    "expected_header_name": "x-openshell-middleware-reviewed",
                    "expected_header_value": "true",
                },
            ),
        )
    )

    result = processor.process(original, timeout=Timeout.from_seconds(1))
    final_request = apply_request_mutations(original, result.request_mutations)

    assert result.decision is EgressDecision.ALLOW
    assert isinstance(result.decision_source, GateDecisionSource)
    assert result.decision_source.gate_name == "allow"
    assert final_request.body == b"updated"
    assert final_request.headers == (
        HttpHeader(name="x-openshell-middleware-reviewed", value="true"),
    )


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


def test_aggregated_finding_size_exhaustion_returns_a_runtime_limit() -> None:
    processor = _processor(
        (
            (
                "one",
                {
                    "kind": "test-control",
                    "boundary_finding": True,
                },
            ),
        )
    )

    result = processor.process(_request(), timeout=Timeout.from_seconds(1))

    assert result.decision is EgressDecision.DENY
    assert result.decision_source.kind is DecisionSourceKind.RUNTIME_LIMIT
    assert result.reason_code == LIMIT_REASON_CODE
    assert result.findings == ()


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


def test_composed_header_mutation_data_overflow_is_an_atomic_runtime_limit() -> None:
    half_limit = MAX_HEADER_MUTATION_DATA_BYTES // 2
    processor = _processor(
        (
            (
                "first",
                {
                    "kind": "test-control",
                    "header_name": "x-openshell-middleware-first",
                    "header_value": "a" * half_limit,
                },
            ),
            (
                "second",
                {
                    "kind": "test-control",
                    "header_name": "x-openshell-middleware-second",
                    "header_value": "b" * half_limit,
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


@pytest.mark.parametrize(
    "current_request",
    [
        _request(
            headers=tuple(
                HttpHeader(name=f"x-{index}", value="value")
                for index in range(MAX_PROTO_HEADERS)
            )
        ),
        _request(
            headers=(
                HttpHeader(
                    name="x-existing",
                    value="x" * (MAX_PROTO_HEADERS_BYTES - len("x-existing")),
                ),
            )
        ),
    ],
    ids=("header-count", "header-bytes"),
)
def test_mutation_that_overflows_the_current_request_is_an_atomic_runtime_limit(
    current_request: HttpRequest,
) -> None:
    processor = _processor(
        (
            (
                "append",
                {
                    "kind": "test-control",
                    "header_name": "x-openshell-middleware-added",
                    "header_value": "value",
                    "header_action": "append",
                },
            ),
        )
    )

    result = processor.process(current_request, timeout=Timeout.from_seconds(1))

    assert result.decision is EgressDecision.DENY
    assert result.decision_source.kind is DecisionSourceKind.RUNTIME_LIMIT
    assert result.reason_code == LIMIT_REASON_CODE
    assert result.request_mutations.is_empty
    assert result.findings == ()
    assert result.traces == ()


@pytest.mark.parametrize(
    "gate_config",
    [
        {
            "kind": "test-control",
            "header_name": "authorization",
            "header_value": "secret",
        },
        {
            "kind": "test-control",
            "remove_header": "Host",
        },
    ],
    ids=("write-outside-namespace", "remove-protected-header"),
)
def test_processor_translates_disallowed_header_mutations_to_a_stable_error(
    gate_config: dict[str, object],
) -> None:
    processor = _processor((("invalid", gate_config),))

    with pytest.raises(EgressGateError) as error:
        processor.process(_request(), timeout=Timeout.from_seconds(1))

    assert error.value.code is ErrorCode.GATE_OUTPUT_INVALID


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
    registry = GateRegistry()
    registry.register(_ControlGate)
    config = registry.validate_config(
        {
            "gates": [{"name": "one", "kind": "test-control", "control": "proceed"}],
            "default_decision": "allow",
        }
    )
    gate = registry.create_gate(config.gates[0])

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

    skip_absent = apply_request_mutations(
        _request(),
        RequestMutations(
            header_mutations=(
                WriteHeaderMutation(
                    kind="write",
                    name="x-openshell-middleware-added",
                    value="created",
                    on_existing=ExistingHeaderAction.SKIP,
                ),
            )
        ),
    )
    assert skip_absent.headers == (
        HttpHeader(name="x-openshell-middleware-added", value="created"),
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
