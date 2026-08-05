"""Transport-boundary tests for the canonical OpenShell protobuf adapter."""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from typing import Never

import grpc
import pytest
from google.protobuf import json_format
from google.protobuf.message import Message

from egress_gate.bindings import supervisor_middleware_pb2 as pb2
from egress_gate.config import EgressGateConfig
from egress_gate.constants import (
    BLOCK_REASON,
    DEFAULT_DENY_REASON_CODE,
    LIMIT_REASON,
    LIMIT_REASON_CODE,
    MAX_BODY_BYTES,
    MAX_PROTO_CONFIG_BYTES,
    MAX_PROTO_CONTEXT_BYTES,
    MAX_PROTO_FINDING_BYTES,
    MAX_PROTO_HEADERS,
    MAX_PROTO_HEADERS_BYTES,
    MAX_PROTO_TARGET_BYTES,
)
from egress_gate.errors import EgressGateError, ErrorCode, GateRegistryError
from egress_gate.gates import (
    GateConfig,
    RegexConfig,
    RegexReplaceAction,
    create_builtin_registry,
)
from egress_gate.request import (
    ExistingHeaderAction,
    RequestMutations,
    WriteHeaderMutation,
)
from egress_gate.result import (
    DecisionSourceKind,
    EgressDecision,
    EgressResult,
    Finding,
    PipelineDefaultDecisionSource,
    RuntimeLimitDecisionSource,
    SourcedFinding,
)
from egress_gate.service import servicer as servicer_module
from egress_gate.service.servicer import EgressGateMiddleware
from egress_gate.timeout import Timeout


def _values(
    *, action_kind: str = "detect", default_decision: str = "allow"
) -> dict[str, object]:
    action: dict[str, object] = {"kind": action_kind}
    if action_kind == "replace":
        action["template"] = "[{entity}]"
    config: dict[str, object] = {
        "kind": "regex",
        "scan": {"kind": "body", "action": action},
        "pattern_catalog": {
            "entities": [
                {
                    "name": "token",
                    "rules": [{"pattern": "secret", "confidence": "high"}],
                }
            ]
        },
    }
    return {
        "gates": [{"name": "body", **config}],
        "default_decision": default_decision,
    }


def _proto_config(values: dict[str, object]) -> Message:
    config = pb2.ValidateConfigRequest().config
    json_format.ParseDict(values, config)
    return config


def _request(body: bytes = b"secret") -> pb2.HttpRequestEvaluation:
    return pb2.HttpRequestEvaluation(
        phase=pb2.SUPERVISOR_MIDDLEWARE_PHASE_PRE_CREDENTIALS,
        context=pb2.RequestContext(request_id="request-1", sandbox_id="sandbox-1"),
        config=_proto_config(_values()),
        target=pb2.HttpRequestTarget(
            scheme="https",
            host="example.com",
            port=443,
            method="POST",
            path="/",
            query="",
        ),
        headers=[pb2.HttpHeader(name="x-test", value="one")],
        body=body,
    )


class _SuccessfulEvaluationContext:
    async def abort(self, code: grpc.StatusCode, details: str) -> Never:
        del code, details
        raise AssertionError("successful evaluation unexpectedly aborted")


def test_copied_proto_remains_the_current_five_field_finding_contract() -> None:
    evaluation = pb2.HttpRequestEvaluation()
    finding = pb2.Finding()

    assert isinstance(evaluation.config, Message)
    assert not hasattr(evaluation, "config_fingerprint")
    assert not hasattr(finding, "source")
    assert not hasattr(finding, "attributes")


def test_validate_config_is_pure_and_reports_invalid_config() -> None:
    middleware = EgressGateMiddleware(create_builtin_registry())
    try:
        valid = middleware._validate_config(
            pb2.ValidateConfigRequest(config=_proto_config(_values()))
        )
        invalid = middleware._validate_config(
            pb2.ValidateConfigRequest(config=_proto_config({"unexpected": {}}))
        )
    finally:
        asyncio.run(middleware.close())

    assert valid.valid is True
    assert invalid.valid is False
    assert "config_invalid" in invalid.reason


def test_validate_config_rejects_oversized_wire_config_before_parsing() -> None:
    exact = pb2.ValidateConfigRequest()
    json_format.ParseDict({"padding": "x" * 65_515}, exact.config)
    oversized = pb2.ValidateConfigRequest()
    json_format.ParseDict({"padding": "x" * 65_516}, oversized.config)
    assert exact.config.ByteSize() == MAX_PROTO_CONFIG_BYTES
    assert oversized.config.ByteSize() == MAX_PROTO_CONFIG_BYTES + 1

    middleware = EgressGateMiddleware(create_builtin_registry())
    try:
        exact_response = middleware._validate_config(exact)
        oversized_response = middleware._validate_config(oversized)
    finally:
        asyncio.run(middleware.close())

    assert exact_response.valid is False
    assert oversized_response.valid is False
    assert "config_invalid" in oversized_response.reason


def test_evaluation_enforces_exact_encoded_transport_boundaries() -> None:
    request = _request(body=b"")
    request.context.sandbox_id = ""
    request.context.request_id = "x" * 4_093
    assert request.context.ByteSize() == MAX_PROTO_CONTEXT_BYTES
    servicer_module._validate_evaluation_envelope(request)
    request.context.request_id += "x"
    assert request.context.ByteSize() == MAX_PROTO_CONTEXT_BYTES + 1
    with pytest.raises(EgressGateError) as context_error:
        servicer_module._validate_evaluation_envelope(request)
    assert context_error.value.code is ErrorCode.REQUEST_ENVELOPE_INVALID

    request = _request(body=b"")
    request.target.Clear()
    request.target.host = "x" * 32_764
    assert request.target.ByteSize() == MAX_PROTO_TARGET_BYTES
    servicer_module._validate_evaluation_envelope(request)
    request.target.host += "x"
    with pytest.raises(EgressGateError) as target_error:
        servicer_module._validate_evaluation_envelope(request)
    assert target_error.value.code is ErrorCode.REQUEST_ENVELOPE_INVALID

    request = _request(body=b"")
    request.headers.clear()
    request.headers.add(name="x", value="x" * 65_525)
    assert servicer_module._encoded_headers_size(request.headers) == (
        MAX_PROTO_HEADERS_BYTES
    )
    servicer_module._validate_evaluation_envelope(request)
    request.headers[0].value += "x"
    with pytest.raises(EgressGateError) as header_error:
        servicer_module._validate_evaluation_envelope(request)
    assert header_error.value.code is ErrorCode.REQUEST_ENVELOPE_INVALID

    request = _request(body=b"")
    request.headers.clear()
    for _ in range(MAX_PROTO_HEADERS):
        request.headers.add()
    servicer_module._validate_evaluation_envelope(request)
    request.headers.add()
    with pytest.raises(EgressGateError):
        servicer_module._validate_evaluation_envelope(request)


def test_request_adapter_builds_the_full_domain_request() -> None:
    domain = servicer_module._request_from_proto(_request(b"bytes"))

    assert domain.body == b"bytes"
    assert domain.context.request_id == "request-1"
    assert domain.target.host == "example.com"
    assert domain.headers[0].name == "x-test"


def test_result_adapter_serializes_only_five_finding_fields_and_empty_body_intent() -> (
    None
):
    finding = Finding(
        type="t" * 1024,
        label="l" * 1024,
        confidence="c" * 1024,
        severity="s" * 1010,
    )
    result = EgressResult(
        decision=EgressDecision.ALLOW,
        decision_source=PipelineDefaultDecisionSource(
            kind=DecisionSourceKind.PIPELINE_DEFAULT
        ),
        request_mutations=RequestMutations(replacement_body=b""),
        findings=(SourcedFinding(source_gate="body", finding=finding),),
    )

    response = servicer_module._result_to_proto(result)

    assert response.decision == pb2.DECISION_ALLOW
    assert response.has_body is True
    assert response.body == b""
    assert response.findings[0].ByteSize() == MAX_PROTO_FINDING_BYTES
    assert {field.name for field, _ in response.findings[0].ListFields()} == {
        "type",
        "label",
        "count",
        "confidence",
        "severity",
    }


def test_result_adapter_preserves_ordered_header_mutations_and_deny_reason() -> None:
    allowed = EgressResult(
        decision=EgressDecision.ALLOW,
        decision_source=PipelineDefaultDecisionSource(
            kind=DecisionSourceKind.PIPELINE_DEFAULT
        ),
        request_mutations=RequestMutations(
            header_mutations=(
                WriteHeaderMutation(
                    kind="write",
                    name="x-openshell-middleware-reviewed",
                    value="true",
                    on_existing=ExistingHeaderAction.OVERWRITE,
                ),
            )
        ),
    )
    denied = EgressResult(
        decision=EgressDecision.DENY,
        decision_source=RuntimeLimitDecisionSource(
            kind=DecisionSourceKind.RUNTIME_LIMIT
        ),
        reason_code=LIMIT_REASON_CODE,
    )

    allowed_response = servicer_module._result_to_proto(allowed)
    denied_response = servicer_module._result_to_proto(denied)

    assert allowed_response.header_mutations[0].write.name == (
        "x-openshell-middleware-reviewed"
    )
    assert denied_response.reason == LIMIT_REASON
    assert denied_response.reason_code == LIMIT_REASON_CODE


def test_processor_preparation_reuses_only_the_current_validated_policy() -> None:
    middleware = EgressGateMiddleware(create_builtin_registry())
    try:
        first = middleware._policy.processor_for(
            _values(), timeout=Timeout.from_seconds(1)
        )
        same = middleware._policy.processor_for(
            _values(), timeout=Timeout.from_seconds(1)
        )
        changed = middleware._policy.processor_for(
            _values(action_kind="replace"), timeout=Timeout.from_seconds(1)
        )
    finally:
        asyncio.run(middleware.close())

    assert same is first
    assert changed is not first


def test_concurrent_same_candidate_is_prepared_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    middleware = EgressGateMiddleware(create_builtin_registry())
    original_create_gate = middleware._registry.create_gate
    workers_ready = Barrier(2)
    build_started = Event()
    release_build = Event()
    create_count = 0

    def counted_create_gate(
        config: GateConfig,
        *,
        timeout: Timeout | None = None,
    ) -> object:
        nonlocal create_count
        create_count += 1
        build_started.set()
        assert release_build.wait(2)
        return original_create_gate(config, timeout=timeout)

    monkeypatch.setattr(middleware._registry, "create_gate", counted_create_gate)

    def resolve_candidate() -> object:
        workers_ready.wait(timeout=2)
        return middleware._policy.processor_for(
            _values(),
            timeout=Timeout.from_seconds(2),
        )

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first_future = executor.submit(resolve_candidate)
            second_future = executor.submit(resolve_candidate)
            assert build_started.wait(1)
            assert not first_future.done()
            assert not second_future.done()
            release_build.set()
            first = first_future.result()
            second = second_future.result()
    finally:
        release_build.set()
        asyncio.run(middleware.close())

    assert second is first
    assert create_count == 1


def test_failed_candidate_leaves_the_old_policy_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    middleware = EgressGateMiddleware(create_builtin_registry())
    old = middleware._policy.processor_for(_values(), timeout=Timeout.from_seconds(1))
    original_create_gate = middleware._registry.create_gate

    def fail_changed_candidate(
        config: GateConfig,
        *,
        timeout: Timeout | None = None,
    ) -> object:
        if isinstance(config, RegexConfig) and isinstance(
            config.scan.action, RegexReplaceAction
        ):
            raise GateRegistryError("candidate preparation failed")
        return original_create_gate(config, timeout=timeout)

    monkeypatch.setattr(
        middleware._registry,
        "create_gate",
        fail_changed_candidate,
    )
    try:
        with pytest.raises(EgressGateError) as error:
            middleware._policy.processor_for(
                _values(action_kind="replace"),
                timeout=Timeout.from_seconds(1),
            )
        active = middleware._policy.processor_for(
            _values(), timeout=Timeout.from_seconds(1)
        )
    finally:
        asyncio.run(middleware.close())

    assert error.value.code is ErrorCode.CONFIG_INVALID
    assert active is old


def test_invalid_request_cannot_publish_a_changed_policy() -> None:
    middleware = EgressGateMiddleware(create_builtin_registry())
    old = middleware._policy.processor_for(_values(), timeout=Timeout.from_seconds(1))
    invalid = _request()
    invalid.config.CopyFrom(_proto_config(_values(action_kind="replace")))
    invalid.headers[0].name = ""

    try:
        with pytest.raises(EgressGateError) as error:
            middleware._prepare_and_process(invalid, Timeout.from_seconds(1))
        assert middleware._policy._processor is old
    finally:
        asyncio.run(middleware.close())

    assert error.value.code is ErrorCode.REQUEST_ENVELOPE_INVALID


def test_in_flight_processor_reference_survives_policy_replacement() -> None:
    middleware = EgressGateMiddleware(create_builtin_registry())
    try:
        old = middleware._policy.processor_for(
            _values(), timeout=Timeout.from_seconds(1)
        )
        replacement = middleware._policy.processor_for(
            _values(action_kind="replace"), timeout=Timeout.from_seconds(1)
        )
        domain_request = servicer_module._request_from_proto(_request())

        old_result = old.process(domain_request, timeout=Timeout.from_seconds(1))
        replacement_result = replacement.process(
            domain_request,
            timeout=Timeout.from_seconds(1),
        )
    finally:
        asyncio.run(middleware.close())

    assert old_result.request_mutations.replacement_body is None
    assert replacement_result.request_mutations.replacement_body == b"[token]"


@pytest.mark.asyncio
async def test_cancelled_candidate_keeps_its_slot_and_is_not_published(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    middleware = EgressGateMiddleware(
        create_builtin_registry(),
        timeout_seconds=5,
    )
    old = middleware._policy.processor_for(_values(), timeout=Timeout.from_seconds(1))
    started = Event()
    release = Event()
    original_build = middleware._policy._build_processor

    def blocked_build(
        config: EgressGateConfig[GateConfig],
        *,
        timeout: Timeout,
    ) -> object:
        started.set()
        assert release.wait(2)
        return original_build(config, timeout=timeout)

    monkeypatch.setattr(middleware._policy, "_build_processor", blocked_build)
    changed_request = _request()
    changed_request.config.CopyFrom(_proto_config(_values(action_kind="replace")))
    task = asyncio.create_task(
        middleware._evaluate_http_request(
            changed_request,
            Timeout.from_seconds(5),
        )
    )
    try:
        assert await asyncio.to_thread(started.wait, 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert middleware._processing_slots._value == 3

        release.set()
        for _ in range(100):
            if middleware._processing_slots._value == 4:
                break
            await asyncio.sleep(0.01)

        assert middleware._processing_slots._value == 4
        assert middleware._policy._processor is old
    finally:
        release.set()
        await middleware.close()


@pytest.mark.asyncio
async def test_result_serialization_is_bracketed_by_the_shared_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    middleware = EgressGateMiddleware(create_builtin_registry())
    result = EgressResult(
        decision=EgressDecision.ALLOW,
        decision_source=PipelineDefaultDecisionSource(
            kind=DecisionSourceKind.PIPELINE_DEFAULT
        ),
    )
    events: list[str] = []
    original_serialize = servicer_module._result_to_proto_with_source

    async def return_result(*args: object, **kwargs: object) -> EgressResult:
        del args, kwargs
        return result

    def record_deadline_check(self: Timeout) -> None:
        del self
        events.append("deadline")

    def record_serialization(
        value: EgressResult,
    ) -> tuple[pb2.HttpRequestResult, str]:
        events.append("serialize")
        return original_serialize(value)

    monkeypatch.setattr(middleware, "_run_in_worker", return_result)
    monkeypatch.setattr(Timeout, "raise_if_expired", record_deadline_check)
    monkeypatch.setattr(
        servicer_module,
        "_result_to_proto_with_source",
        record_serialization,
    )
    try:
        await middleware._evaluate_http_request(
            _request(),
            Timeout.from_seconds(1),
        )
    finally:
        await middleware.close()

    assert events == ["deadline", "serialize", "deadline"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action_kind", "expected_source"),
    (("detect", "pipeline_default"), ("deny", "gate")),
)
async def test_evaluation_log_records_decision_source(
    action_kind: str,
    expected_source: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    middleware = EgressGateMiddleware(create_builtin_registry())
    request = _request()
    request.config.CopyFrom(_proto_config(_values(action_kind=action_kind)))
    try:
        with caplog.at_level(logging.INFO, logger=servicer_module.__name__):
            await middleware._evaluate_rpc(request, _SuccessfulEvaluationContext())
    finally:
        await middleware.close()

    record = next(
        item
        for item in caplog.records
        if item.message.startswith("egress_gate_evaluation")
    )
    assert getattr(record, "decision_source_kind", None) == expected_source


@pytest.mark.asyncio
async def test_evaluation_log_records_runtime_limit_source(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    middleware = EgressGateMiddleware(create_builtin_registry())

    async def return_limit(
        *args: object,
        **kwargs: object,
    ) -> tuple[pb2.HttpRequestResult, str]:
        del args, kwargs
        return servicer_module._limit_deny(), DecisionSourceKind.RUNTIME_LIMIT.value

    monkeypatch.setattr(
        middleware,
        "_evaluate_http_request_with_source",
        return_limit,
    )
    try:
        with caplog.at_level(logging.INFO, logger=servicer_module.__name__):
            await middleware._evaluate_rpc(_request(), _SuccessfulEvaluationContext())
    finally:
        await middleware.close()

    record = next(
        item
        for item in caplog.records
        if item.message.startswith("egress_gate_evaluation")
    )
    assert getattr(record, "decision_source_kind", None) == "runtime_limit"


def test_serialized_limit_result_reports_runtime_limit_source() -> None:
    result = EgressResult(
        decision=EgressDecision.DENY,
        decision_source=RuntimeLimitDecisionSource(
            kind=DecisionSourceKind.RUNTIME_LIMIT
        ),
        reason_code=LIMIT_REASON_CODE,
    )

    response, source_kind = servicer_module._result_to_proto_with_source(result)

    assert response.reason_code == LIMIT_REASON_CODE
    assert source_kind == DecisionSourceKind.RUNTIME_LIMIT.value


def test_invalid_utf8_is_an_input_failure_before_wire_evaluation() -> None:
    middleware = EgressGateMiddleware(create_builtin_registry())
    request = _request(body=b"\xff")
    try:
        with pytest.raises(EgressGateError) as error:
            asyncio.run(
                middleware._evaluate_http_request(
                    request,
                    Timeout.from_seconds(1),
                )
            )
    finally:
        asyncio.run(middleware.close())

    assert error.value.code is ErrorCode.BODY_ENCODING_INVALID


def test_service_request_body_limit_is_checked_before_worker_execution() -> None:
    request = _request(body=b"x" * (MAX_BODY_BYTES + 1))
    middleware = EgressGateMiddleware(create_builtin_registry())
    try:
        with pytest.raises(EgressGateError) as error:
            asyncio.run(
                middleware._evaluate_http_request(
                    request,
                    Timeout.from_seconds(1),
                )
            )
    finally:
        asyncio.run(middleware.close())

    assert error.value.code is ErrorCode.REQUEST_BODY_TOO_LARGE


def test_default_deny_reason_is_wire_safe() -> None:
    result = EgressResult(
        decision=EgressDecision.DENY,
        decision_source=PipelineDefaultDecisionSource(
            kind=DecisionSourceKind.PIPELINE_DEFAULT
        ),
        reason_code=DEFAULT_DENY_REASON_CODE,
    )
    response = servicer_module._result_to_proto(result)
    assert response.reason == BLOCK_REASON
    assert response.reason_code == DEFAULT_DENY_REASON_CODE
