"""Service boundary tests over the canonical OpenShell-owned protobuf."""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier, Event, Lock, get_ident
from typing import Never

import grpc
import pytest
from google.protobuf import json_format
from google.protobuf.message import Message

from egress_gate.bindings import supervisor_middleware_pb2 as pb2
from egress_gate.config import EgressGateConfig
from egress_gate.constants import (
    LIMIT_REASON,
    LIMIT_REASON_CODE,
    MAX_DIAGNOSTIC_TEXT_BYTES,
    MAX_PROTO_CONFIG_BYTES,
    MAX_PROTO_CONTEXT_BYTES,
    MAX_PROTO_FINDING_BYTES,
    MAX_PROTO_HEADERS,
    MAX_PROTO_HEADERS_BYTES,
    MAX_PROTO_TARGET_BYTES,
)
from egress_gate.engines import (
    EngineConfig,
)
from egress_gate.engines import regex as regex_module
from egress_gate.engines.registry import create_builtin_registry
from egress_gate.errors import EgressGateError, ErrorCode
from egress_gate.request_processor import (
    EntityDetectionSummary,
    RequestDecision,
    RequestProcessingResult,
    RequestProcessor,
)
from egress_gate.service import servicer as servicer_module
from egress_gate.service.servicer import EgressGateMiddleware


def _values(
    action: str = "replace",
    *,
    rules: list[dict[str, object]] | None = None,
    stage_count: int = 1,
    stage_name: str | None = None,
) -> dict[str, object]:
    if rules is None:
        rules = [
            {
                "pattern": r"[a-z]+@[a-z]+\.[a-z]+",
                "confidence": "high",
            }
        ]
    stage: dict[str, object] = {
        "config": {
            "engine": "regex",
            "pattern_catalog": {
                "entities": [
                    {
                        "name": "email",
                        "rules": rules,
                    }
                ]
            },
            "replacement": {
                "strategy": "template",
                "template": "[{entity}]",
            },
        }
    }
    if stage_name is not None:
        stage["name"] = stage_name
    return {
        "entity_processing": {"stages": [deepcopy(stage) for _ in range(stage_count)]},
        "on_detection": {"action": action},
    }


def _proto_config(values: dict[str, object]) -> Message:
    result = pb2.ValidateConfigRequest().config
    json_format.ParseDict(values, result)
    return result


def _request(body: bytes, *, action: str = "replace") -> pb2.HttpRequestEvaluation:
    return pb2.HttpRequestEvaluation(
        phase=pb2.SUPERVISOR_MIDDLEWARE_PHASE_PRE_CREDENTIALS,
        config=_proto_config(_values(action)),
        body=body,
    )


class _SuccessfulEvaluationContext:
    async def abort(self, code: grpc.StatusCode, details: str) -> Never:
        del code, details
        raise AssertionError("successful evaluation unexpectedly aborted")


def test_copied_proto_remains_the_current_openshell_contract() -> None:
    evaluation = pb2.HttpRequestEvaluation()
    finding = pb2.Finding()

    assert isinstance(evaluation.config, Message)
    assert not hasattr(evaluation, "config_fingerprint")
    assert not hasattr(finding, "source")


def test_validate_config_is_pure_and_reports_invalid_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    middleware = EgressGateMiddleware(create_builtin_registry())
    active = middleware._policy.processor_for(_values(action="replace"))
    processor_build_count = 0
    original_build = servicer_module._ActivePolicy._build_processor

    def record_processor_build(
        policy: servicer_module._ActivePolicy,
        config: EgressGateConfig[EngineConfig],
    ) -> RequestProcessor:
        nonlocal processor_build_count
        processor_build_count += 1
        return original_build(policy, config)

    monkeypatch.setattr(
        servicer_module._ActivePolicy,
        "_build_processor",
        record_processor_build,
    )
    try:
        valid = middleware._validate_config(
            pb2.ValidateConfigRequest(config=_proto_config(_values("detect")))
        )
        invalid = middleware._validate_config(
            pb2.ValidateConfigRequest(config=_proto_config({"on_detection": {}}))
        )
        still_active = middleware._policy.processor_for(_values(action="replace"))
    finally:
        asyncio.run(middleware.close())

    assert valid.valid is True
    assert invalid.valid is False
    assert "config_invalid" in invalid.reason
    assert still_active is active
    assert processor_build_count == 0


def test_validate_config_rejects_oversized_proto_before_registry_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validation_count = 0
    original_validate = servicer_module.EngineRegistry.validate_config

    def record_validation(
        registry: servicer_module.EngineRegistry,
        values: object,
    ) -> EgressGateConfig[EngineConfig]:
        nonlocal validation_count
        validation_count += 1
        return original_validate(registry, values)

    monkeypatch.setattr(
        servicer_module.EngineRegistry,
        "validate_config",
        record_validation,
    )
    exact_config = pb2.ValidateConfigRequest()
    json_format.ParseDict({"padding": "x" * 65_515}, exact_config.config)
    oversized_config = pb2.ValidateConfigRequest()
    json_format.ParseDict({"padding": "x" * 65_516}, oversized_config.config)
    assert exact_config.config.ByteSize() == MAX_PROTO_CONFIG_BYTES
    assert oversized_config.config.ByteSize() == MAX_PROTO_CONFIG_BYTES + 1
    middleware = EgressGateMiddleware(create_builtin_registry())
    try:
        exact = middleware._validate_config(exact_config)
        oversized = middleware._validate_config(oversized_config)
    finally:
        asyncio.run(middleware.close())

    assert exact.valid is False
    assert oversized.valid is False
    assert validation_count == 1


@pytest.mark.parametrize(
    "unsafe_value",
    [
        "line\nbreak",
        "ansi\x1b[31m",
        "nul\x00byte",
        "right-to-left\u202eoverride",
    ],
)
def test_validate_config_rejects_non_printable_stage_names(
    unsafe_value: str,
) -> None:
    registry = create_builtin_registry()

    with pytest.raises(EgressGateError) as captured:
        registry.validate_config(_values(stage_name=unsafe_value))

    assert captured.value.code is ErrorCode.CONFIG_INVALID


def test_validate_config_accepts_printable_unicode_stage_names() -> None:
    config = create_builtin_registry().validate_config(_values(stage_name="身份检查 🛡️"))

    assert config.entity_processing.stages[0].name == "身份检查 🛡️"


def test_evaluation_enforces_exact_encoded_transport_boundaries() -> None:
    request = _request(b"")
    request.context.request_id = "x" * 4_093
    assert request.context.ByteSize() == MAX_PROTO_CONTEXT_BYTES
    servicer_module._validate_evaluation_envelope(request)
    request.context.request_id += "x"
    assert request.context.ByteSize() == MAX_PROTO_CONTEXT_BYTES + 1
    with pytest.raises(EgressGateError) as context_error:
        servicer_module._validate_evaluation_envelope(request)
    assert context_error.value.code is ErrorCode.REQUEST_ENVELOPE_INVALID

    request = _request(b"")
    request.target.host = "x" * 32_764
    assert request.target.ByteSize() == MAX_PROTO_TARGET_BYTES
    servicer_module._validate_evaluation_envelope(request)
    request.target.host += "x"
    assert request.target.ByteSize() == MAX_PROTO_TARGET_BYTES + 1
    with pytest.raises(EgressGateError) as target_error:
        servicer_module._validate_evaluation_envelope(request)
    assert target_error.value.code is ErrorCode.REQUEST_ENVELOPE_INVALID

    request = _request(b"")
    request.headers.add(name="x", value="x" * 65_525)
    assert servicer_module._encoded_headers_size(request.headers) == (
        MAX_PROTO_HEADERS_BYTES
    )
    servicer_module._validate_evaluation_envelope(request)
    request.headers[0].value += "x"
    assert servicer_module._encoded_headers_size(request.headers) == (
        MAX_PROTO_HEADERS_BYTES + 1
    )
    with pytest.raises(EgressGateError) as header_size_error:
        servicer_module._validate_evaluation_envelope(request)
    assert header_size_error.value.code is ErrorCode.REQUEST_ENVELOPE_INVALID

    request = _request(b"")
    for _ in range(MAX_PROTO_HEADERS):
        request.headers.add()
    servicer_module._validate_evaluation_envelope(request)
    request.headers.add()
    with pytest.raises(EgressGateError) as header_count_error:
        servicer_module._validate_evaluation_envelope(request)
    assert header_count_error.value.code is ErrorCode.REQUEST_ENVELOPE_INVALID


def test_evaluation_enforces_exact_encoded_config_boundary() -> None:
    request = _request(b"")
    request.config.Clear()
    json_format.ParseDict({"padding": "x" * 65_515}, request.config)
    assert request.config.ByteSize() == MAX_PROTO_CONFIG_BYTES
    servicer_module._validate_evaluation_envelope(request)
    request.config.Clear()
    json_format.ParseDict({"padding": "x" * 65_516}, request.config)
    assert request.config.ByteSize() == MAX_PROTO_CONFIG_BYTES + 1

    with pytest.raises(EgressGateError) as captured:
        servicer_module._validate_evaluation_envelope(request)

    assert captured.value.code is ErrorCode.CONFIG_INVALID
    assert "encoded configuration at or below 64 KiB" in str(captured.value)


def test_limit_deny_explains_recovery_options() -> None:
    result = servicer_module._result_to_proto(
        RequestProcessingResult(
            decision=RequestDecision.DENY,
            reason_code=LIMIT_REASON_CODE,
        )
    )

    assert result.reason == LIMIT_REASON
    assert "Check Egress Gate logs for the limit kind" in result.reason
    assert "Reduce the request or replacement size" in result.reason
    assert "simplify the configured stages and rules" in result.reason
    assert "--timeout-seconds or EgressGateServer(timeout_seconds=...)" in (
        result.reason
    )
    assert "additional headroom for queueing and configuration preparation" in (
        result.reason
    )


def test_service_limit_deny_logs_a_content_safe_resource_kind(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sentinel = "sensitive-finding-value"
    with caplog.at_level(logging.INFO, logger="egress_gate.service.servicer"):
        result = servicer_module._result_to_proto(
            RequestProcessingResult(
                decision=RequestDecision.ALLOW,
                detection_summaries=(
                    EntityDetectionSummary(
                        entity=sentinel + ("x" * MAX_PROTO_FINDING_BYTES),
                        source_stage="stage",
                        count=1,
                    ),
                ),
            )
        )

    assert result.reason_code == LIMIT_REASON_CODE
    assert "egress_gate_processing_limit kind=resource" in caplog.text
    assert sentinel not in caplog.text


@pytest.mark.parametrize(
    "invalid_request_id",
    [
        "line\nbreak",
        "ansi\x1b[31m",
        "nul\x00byte",
        "right-to-left\u202eoverride",
        "x" * (MAX_DIAGNOSTIC_TEXT_BYTES + 1),
    ],
)
def test_evaluation_logs_placeholder_for_invalid_request_id(
    caplog: pytest.LogCaptureFixture,
    invalid_request_id: str,
) -> None:
    async def evaluate() -> pb2.HttpRequestResult:
        middleware = EgressGateMiddleware(create_builtin_registry())
        request = _request(b"no match", action="detect")
        request.context.request_id = invalid_request_id
        try:
            return await middleware._evaluate_rpc(
                request,
                _SuccessfulEvaluationContext(),
            )
        finally:
            await middleware.close()

    with caplog.at_level(logging.INFO, logger="egress_gate.service.servicer"):
        result = asyncio.run(evaluate())

    records = [
        record
        for record in caplog.records
        if record.name == "egress_gate.service.servicer"
        and record.getMessage().startswith("egress_gate_evaluation ")
    ]
    assert result.decision == pb2.DECISION_ALLOW
    assert len(records) == 1
    assert 'request_id="invalid" ' in records[0].getMessage()
    assert records[0].getMessage().isprintable()
    assert len(caplog.text.splitlines()) == 1


def test_evaluation_logs_printable_unicode_request_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def evaluate() -> None:
        middleware = EgressGateMiddleware(create_builtin_registry())
        request = _request(b"no match", action="detect")
        request.context.request_id = "请求-42 🛡️"
        try:
            await middleware._evaluate_rpc(
                request,
                _SuccessfulEvaluationContext(),
            )
        finally:
            await middleware.close()

    with caplog.at_level(logging.INFO, logger="egress_gate.service.servicer"):
        asyncio.run(evaluate())

    assert r'request_id="请求-42\u0020🛡️"' in caplog.text
    assert len(caplog.text.splitlines()) == 1


def test_evaluation_quotes_request_id_delimiters_in_message_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    request_id = 'trusted action=allow error_code="none"'

    async def evaluate() -> None:
        middleware = EgressGateMiddleware(create_builtin_registry())
        request = _request(b"no match", action="detect")
        request.context.request_id = request_id
        try:
            await middleware._evaluate_rpc(
                request,
                _SuccessfulEvaluationContext(),
            )
        finally:
            await middleware.close()

    with caplog.at_level(logging.INFO, logger="egress_gate.service.servicer"):
        asyncio.run(evaluate())

    records = [
        record
        for record in caplog.records
        if record.name == "egress_gate.service.servicer"
        and record.getMessage().startswith("egress_gate_evaluation ")
    ]
    assert len(records) == 1
    assert getattr(records[0], "request_id") == request_id
    assert (
        r'request_id="trusted\u0020action=allow\u0020error_code=\"none\""'
        in records[0].getMessage()
    )
    assert records[0].getMessage().count(" action=") == 1


def test_middleware_applies_configured_timeout_to_active_processor() -> None:
    middleware = EgressGateMiddleware(
        create_builtin_registry(),
        timeout_seconds=4.5,
    )
    try:
        processor = middleware._policy.processor_for(_values())
    finally:
        asyncio.run(middleware.close())

    assert processor._timeout_seconds == 4.5


def test_evaluation_decodes_one_utf8_text_and_encodes_replacement() -> None:
    async def evaluate() -> pb2.HttpRequestResult:
        middleware = EgressGateMiddleware(create_builtin_registry())
        try:
            return await middleware._evaluate_http_request(_request(b"email a@b.com"))
        finally:
            await middleware.close()

    result = asyncio.run(evaluate())

    assert result.decision == pb2.DECISION_ALLOW
    assert result.has_body is True
    assert result.body == b"email [email]"
    assert len(result.findings) == 1
    assert result.findings[0].type == "detected_entity"
    assert result.findings[0].label == "email (regex[1])"


def test_evaluation_prepares_configuration_off_the_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event_loop_thread = get_ident()
    preparation_threads: list[int] = []
    original_processor_for = servicer_module._ActivePolicy.processor_for

    def record_preparation(
        policy: servicer_module._ActivePolicy,
        values: object,
    ) -> RequestProcessor:
        preparation_threads.append(get_ident())
        return original_processor_for(policy, values)

    monkeypatch.setattr(
        servicer_module._ActivePolicy,
        "processor_for",
        record_preparation,
    )

    async def evaluate() -> None:
        middleware = EgressGateMiddleware(create_builtin_registry())
        try:
            await middleware._evaluate_http_request(_request(b"email a@b.com"))
        finally:
            await middleware.close()

    asyncio.run(evaluate())

    assert len(preparation_threads) == 1
    assert preparation_threads[0] != event_loop_thread


def test_evaluation_revalidates_configuration_before_reusing_active_processor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validation_count = 0
    original_validate = servicer_module.EngineRegistry.validate_config

    def record_validation(
        registry: servicer_module.EngineRegistry,
        values: object,
    ) -> EgressGateConfig[EngineConfig]:
        nonlocal validation_count
        validation_count += 1
        return original_validate(registry, values)

    monkeypatch.setattr(
        servicer_module.EngineRegistry,
        "validate_config",
        record_validation,
    )

    async def evaluate_twice() -> None:
        middleware = EgressGateMiddleware(create_builtin_registry())
        try:
            request = _request(b"email a@b.com")
            await middleware._evaluate_http_request(request)
            await middleware._evaluate_http_request(request)
        finally:
            await middleware.close()

    asyncio.run(evaluate_twice())

    assert validation_count == 2


def test_active_policy_reuses_only_the_current_configuration() -> None:
    policy = servicer_module._ActivePolicy(
        create_builtin_registry(),
        timeout_seconds=1,
        log_request_content=False,
    )
    first_values = _values(action="detect")
    second_values = _values(action="block")

    first = policy.processor_for(first_values)
    same = policy.processor_for(deepcopy(first_values))
    second = policy.processor_for(second_values)
    rebuilt_first = policy.processor_for(first_values)

    assert same is first
    assert second is not first
    assert rebuilt_first is not first
    assert rebuilt_first is not second


@pytest.mark.parametrize("initial_action", [None, "detect"])
def test_concurrent_requests_for_the_same_policy_build_once(
    monkeypatch: pytest.MonkeyPatch,
    initial_action: str | None,
) -> None:
    worker_count = 4
    workers_ready = Barrier(worker_count)
    build_started = Event()
    release_build = Event()
    build_count = 0
    build_count_lock = Lock()
    original_build = servicer_module._ActivePolicy._build_processor
    policy = servicer_module._ActivePolicy(
        create_builtin_registry(),
        timeout_seconds=1,
        log_request_content=False,
    )
    initial = (
        policy.processor_for(_values(action=initial_action))
        if initial_action is not None
        else None
    )
    requested_values = _values(
        action="block" if initial_action is not None else "detect"
    )

    def pause_build(
        active_policy: servicer_module._ActivePolicy,
        config: EgressGateConfig[EngineConfig],
    ) -> RequestProcessor:
        nonlocal build_count
        with build_count_lock:
            build_count += 1
        build_started.set()
        assert release_build.wait(timeout=5)
        return original_build(active_policy, config)

    monkeypatch.setattr(
        servicer_module._ActivePolicy,
        "_build_processor",
        pause_build,
    )

    def resolve_policy() -> RequestProcessor:
        workers_ready.wait(timeout=5)
        return policy.processor_for(requested_values)

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = tuple(executor.submit(resolve_policy) for _ in range(worker_count))
        assert build_started.wait(timeout=5)
        assert all(not future.done() for future in futures)
        release_build.set()
        processors = tuple(future.result(timeout=5) for future in futures)

    assert build_count == 1
    assert all(processor is processors[0] for processor in processors)
    assert processors[0] is not initial
    assert policy.processor_for(requested_values) is processors[0]


def test_different_policy_updates_are_serialized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_build_started = Event()
    release_first_build = Event()
    second_build_started = Event()
    build_actions: list[str] = []
    active_builds = 0
    maximum_active_builds = 0
    build_count_lock = Lock()
    original_build = servicer_module._ActivePolicy._build_processor
    policy = servicer_module._ActivePolicy(
        create_builtin_registry(),
        timeout_seconds=1,
        log_request_content=False,
    )
    initial = policy.processor_for(_values(action="detect"))

    def control_build(
        active_policy: servicer_module._ActivePolicy,
        config: EgressGateConfig[EngineConfig],
    ) -> RequestProcessor:
        nonlocal active_builds, maximum_active_builds
        action = config.on_detection.action.value
        with build_count_lock:
            active_builds += 1
            maximum_active_builds = max(maximum_active_builds, active_builds)
            build_actions.append(action)
        try:
            if action == "block":
                first_build_started.set()
                assert release_first_build.wait(timeout=5)
            elif action == "replace":
                second_build_started.set()
            return original_build(active_policy, config)
        finally:
            with build_count_lock:
                active_builds -= 1

    monkeypatch.setattr(
        servicer_module._ActivePolicy,
        "_build_processor",
        control_build,
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_update = executor.submit(policy.processor_for, _values(action="block"))
        assert first_build_started.wait(timeout=5)
        second_update = executor.submit(
            policy.processor_for,
            _values(action="replace"),
        )
        assert not second_build_started.wait(timeout=0.1)
        release_first_build.set()
        first_processor = first_update.result(timeout=5)
        second_processor = second_update.result(timeout=5)

    assert second_build_started.is_set()
    assert build_actions == ["block", "replace"]
    assert maximum_active_builds == 1
    assert first_processor is not initial
    assert second_processor is not first_processor
    assert policy.processor_for(_values(action="replace")) is second_processor


def test_failed_policy_update_preserves_the_active_processor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = EgressGateError(ErrorCode.UNEXPECTED_SERVICE_FAILURE)
    original_build = servicer_module._ActivePolicy._build_processor
    policy = servicer_module._ActivePolicy(
        create_builtin_registry(),
        timeout_seconds=1,
        log_request_content=False,
    )
    active_values = _values(action="detect")
    update_values = _values(action="block")
    active = policy.processor_for(active_values)

    def fail_update(
        active_policy: servicer_module._ActivePolicy,
        config: EgressGateConfig[EngineConfig],
    ) -> RequestProcessor:
        if config.on_detection.action.value == "block":
            raise failure
        return original_build(active_policy, config)

    monkeypatch.setattr(
        servicer_module._ActivePolicy,
        "_build_processor",
        fail_update,
    )

    with pytest.raises(EgressGateError) as captured:
        policy.processor_for(update_values)

    assert captured.value is failure
    assert policy.processor_for(active_values) is active

    monkeypatch.setattr(
        servicer_module._ActivePolicy,
        "_build_processor",
        original_build,
    )
    updated = policy.processor_for(update_values)

    assert updated is not active
    assert policy.processor_for(update_values) is updated


def test_compiled_cache_eviction_does_not_invalidate_active_processor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    regex_module._clear_compiled_pattern_cache()
    registry = create_builtin_registry()
    policy = servicer_module._ActivePolicy(
        registry,
        timeout_seconds=1,
        log_request_content=False,
    )
    processor_values = _values(
        "detect",
        rules=[{"pattern": "aaa", "confidence": "high"}],
    )
    validation_values = _values(
        "detect",
        rules=[{"pattern": "bbb", "confidence": "high"}],
    )

    try:
        processor = policy.processor_for(processor_values)
        entry_weight = regex_module._COMPILED_PATTERN_CACHE_WEIGHT_BYTES
        monkeypatch.setattr(
            regex_module,
            "MAX_REGEX_COMPILED_CACHE_WEIGHT_BYTES",
            entry_weight,
        )

        registry.validate_config(validation_values)

        result = processor.process("aaa")
        assert len(result.detection_summaries) == 1
        assert policy.processor_for(processor_values) is processor
        assert regex_module._COMPILED_PATTERN_CACHE_WEIGHT_BYTES <= entry_weight
    finally:
        policy.clear()
        regex_module._clear_compiled_pattern_cache()


def test_middleware_shutdown_clears_active_policy() -> None:
    regex_module._clear_compiled_pattern_cache()
    middleware = EgressGateMiddleware(create_builtin_registry())
    try:
        middleware._policy.processor_for(_values("detect"))

        asyncio.run(middleware.close())

        assert middleware._policy._config is None
        assert middleware._policy._processor is None
    finally:
        middleware._policy.clear()
        regex_module._clear_compiled_pattern_cache()


def test_oversized_stage_list_fails_before_engine_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = _values(action="detect", stage_count=10_000)

    def unexpected_call(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("oversized stage list reached preparation")

    monkeypatch.setattr(
        servicer_module.EngineRegistry,
        "create_engine",
        unexpected_call,
    )
    middleware = EgressGateMiddleware(create_builtin_registry())
    try:
        with pytest.raises(EgressGateError) as captured:
            middleware._policy.processor_for(values)
    finally:
        asyncio.run(middleware.close())

    assert captured.value.code is ErrorCode.CONFIG_INVALID


def test_invalid_utf8_fails_before_invoking_an_engine() -> None:
    async def evaluate() -> None:
        middleware = EgressGateMiddleware(create_builtin_registry())
        try:
            with pytest.raises(EgressGateError) as captured:
                await middleware._evaluate_http_request(_request(b"\xff"))
            assert captured.value.code is ErrorCode.BODY_ENCODING_INVALID
        finally:
            await middleware.close()

    asyncio.run(evaluate())


def test_detect_returns_no_body_mutation() -> None:
    async def evaluate() -> pb2.HttpRequestResult:
        middleware = EgressGateMiddleware(create_builtin_registry())
        try:
            return await middleware._evaluate_http_request(
                _request(b"a@b.com", action="detect")
            )
        finally:
            await middleware.close()

    result = asyncio.run(evaluate())

    assert result.decision == pb2.DECISION_ALLOW
    assert result.has_body is False
    assert result.body == b""
