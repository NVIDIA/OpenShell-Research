"""Service boundary tests over the canonical OpenShell-owned protobuf."""

from __future__ import annotations

import asyncio
import logging
from copy import deepcopy
from threading import get_ident

import pytest
from google.protobuf import json_format
from google.protobuf.message import Message

from privacy_guard.bindings import supervisor_middleware_pb2 as pb2
from privacy_guard.config import PrivacyGuardConfig
from privacy_guard.constants import (
    LIMIT_REASON,
    LIMIT_REASON_CODE,
    MAX_PROTO_CONFIG_BYTES,
    MAX_PROTO_CONTEXT_BYTES,
    MAX_PROTO_FINDING_BYTES,
    MAX_PROTO_HEADERS,
    MAX_PROTO_HEADERS_BYTES,
    MAX_PROTO_TARGET_BYTES,
)
from privacy_guard.engines import EngineConfig
from privacy_guard.engines.registry import create_builtin_registry
from privacy_guard.errors import ErrorCode, PrivacyGuardError
from privacy_guard.request_processor import (
    EntityDetectionSummary,
    RequestDecision,
    RequestProcessingResult,
    RequestProcessor,
)
from privacy_guard.service import servicer as servicer_module
from privacy_guard.service.servicer import PrivacyGuardMiddleware


def _values(
    action: str = "replace",
    *,
    stage_count: int = 1,
) -> dict[str, object]:
    stage: dict[str, object] = {
        "config": {
            "engine": "regex",
            "pattern_catalog": {
                "entities": [
                    {
                        "name": "email",
                        "patterns": [
                            {
                                "pattern": r"[a-z]+@[a-z]+\.[a-z]+",
                                "confidence": "high",
                            }
                        ],
                    }
                ]
            },
            "replacement": {
                "strategy": "template",
                "template": "[{entity}]",
            },
        }
    }
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


def test_copied_proto_remains_the_current_openshell_contract() -> None:
    evaluation = pb2.HttpRequestEvaluation()
    finding = pb2.Finding()

    assert isinstance(evaluation.config, Message)
    assert not hasattr(evaluation, "config_fingerprint")
    assert not hasattr(finding, "source")


def test_validate_config_is_pure_and_reports_invalid_config() -> None:
    middleware = PrivacyGuardMiddleware(create_builtin_registry())

    valid = middleware._validate_config(
        pb2.ValidateConfigRequest(config=_proto_config(_values()))
    )
    invalid = middleware._validate_config(
        pb2.ValidateConfigRequest(config=_proto_config({"on_detection": {}}))
    )

    assert valid.valid is True
    assert invalid.valid is False
    assert "config_invalid" in invalid.reason


def test_validate_config_rejects_oversized_proto_before_registry_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validation_count = 0
    original_validate = servicer_module.EngineRegistry.validate_config

    def record_validation(
        registry: servicer_module.EngineRegistry,
        values: object,
    ) -> PrivacyGuardConfig[EngineConfig]:
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
    middleware = PrivacyGuardMiddleware(create_builtin_registry())
    try:
        exact = middleware._validate_config(exact_config)
        oversized = middleware._validate_config(oversized_config)
    finally:
        asyncio.run(middleware.close())

    assert exact.valid is False
    assert oversized.valid is False
    assert validation_count == 1


def test_evaluation_enforces_exact_encoded_transport_boundaries() -> None:
    request = _request(b"")
    request.context.request_id = "x" * 4_093
    assert request.context.ByteSize() == MAX_PROTO_CONTEXT_BYTES
    servicer_module._validate_evaluation_envelope(request)
    request.context.request_id += "x"
    assert request.context.ByteSize() == MAX_PROTO_CONTEXT_BYTES + 1
    with pytest.raises(PrivacyGuardError) as context_error:
        servicer_module._validate_evaluation_envelope(request)
    assert context_error.value.code is ErrorCode.REQUEST_ENVELOPE_INVALID

    request = _request(b"")
    request.target.host = "x" * 32_764
    assert request.target.ByteSize() == MAX_PROTO_TARGET_BYTES
    servicer_module._validate_evaluation_envelope(request)
    request.target.host += "x"
    assert request.target.ByteSize() == MAX_PROTO_TARGET_BYTES + 1
    with pytest.raises(PrivacyGuardError) as target_error:
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
    with pytest.raises(PrivacyGuardError) as header_size_error:
        servicer_module._validate_evaluation_envelope(request)
    assert header_size_error.value.code is ErrorCode.REQUEST_ENVELOPE_INVALID

    request = _request(b"")
    for _ in range(MAX_PROTO_HEADERS):
        request.headers.add()
    servicer_module._validate_evaluation_envelope(request)
    request.headers.add()
    with pytest.raises(PrivacyGuardError) as header_count_error:
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

    with pytest.raises(PrivacyGuardError) as captured:
        servicer_module._validate_evaluation_envelope(request)

    assert captured.value.code is ErrorCode.CONFIG_INVALID


def test_limit_deny_explains_recovery_options() -> None:
    result = servicer_module._result_to_proto(
        RequestProcessingResult(
            decision=RequestDecision.DENY,
            reason_code=LIMIT_REASON_CODE,
        )
    )

    assert result.reason == LIMIT_REASON
    assert "Check Privacy Guard logs for the limit kind" in result.reason
    assert "Reduce the request or replacement size" in result.reason
    assert "simplify the configured stages and patterns" in result.reason
    assert "--timeout-seconds or PrivacyGuardServer(timeout_seconds=...)" in (
        result.reason
    )
    assert "additional headroom for queueing and configuration preparation" in (
        result.reason
    )


def test_service_limit_deny_logs_a_content_safe_resource_kind(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sentinel = "sensitive-finding-value"
    with caplog.at_level(logging.INFO, logger="privacy_guard.service.servicer"):
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
    assert "privacy_guard_processing_limit kind=resource" in caplog.text
    assert sentinel not in caplog.text


def test_middleware_applies_configured_timeout_to_cached_processors() -> None:
    middleware = PrivacyGuardMiddleware(
        create_builtin_registry(),
        timeout_seconds=4.5,
    )
    try:
        processor = middleware._processors.resolve(_values())
    finally:
        asyncio.run(middleware.close())

    assert processor._timeout_seconds == 4.5


def test_evaluation_decodes_one_utf8_text_and_encodes_replacement() -> None:
    async def evaluate() -> pb2.HttpRequestResult:
        middleware = PrivacyGuardMiddleware(create_builtin_registry())
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
    original_resolve = servicer_module._RequestProcessorCache.resolve

    def record_resolve(
        cache: servicer_module._RequestProcessorCache,
        values: object,
    ) -> RequestProcessor:
        preparation_threads.append(get_ident())
        return original_resolve(cache, values)

    monkeypatch.setattr(
        servicer_module._RequestProcessorCache,
        "resolve",
        record_resolve,
    )

    async def evaluate() -> None:
        middleware = PrivacyGuardMiddleware(create_builtin_registry())
        try:
            await middleware._evaluate_http_request(_request(b"email a@b.com"))
        finally:
            await middleware.close()

    asyncio.run(evaluate())

    assert len(preparation_threads) == 1
    assert preparation_threads[0] != event_loop_thread


def test_evaluation_revalidates_configuration_before_reusing_cached_processor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validation_count = 0
    original_validate = servicer_module.EngineRegistry.validate_config

    def record_validation(
        registry: servicer_module.EngineRegistry,
        values: object,
    ) -> PrivacyGuardConfig[EngineConfig]:
        nonlocal validation_count
        validation_count += 1
        return original_validate(registry, values)

    monkeypatch.setattr(
        servicer_module.EngineRegistry,
        "validate_config",
        record_validation,
    )

    async def evaluate_twice() -> None:
        middleware = PrivacyGuardMiddleware(create_builtin_registry())
        try:
            request = _request(b"email a@b.com")
            await middleware._evaluate_http_request(request)
            await middleware._evaluate_http_request(request)
        finally:
            await middleware.close()

    asyncio.run(evaluate_twice())

    assert validation_count == 2


def test_oversized_stage_list_fails_before_fingerprinting_or_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = _values(action="detect", stage_count=10_000)

    def unexpected_call(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("oversized stage list reached preparation")

    monkeypatch.setattr(
        servicer_module,
        "configuration_fingerprint",
        unexpected_call,
    )
    monkeypatch.setattr(
        servicer_module.EngineRegistry,
        "create_engine",
        unexpected_call,
    )
    middleware = PrivacyGuardMiddleware(create_builtin_registry())
    try:
        with pytest.raises(PrivacyGuardError) as captured:
            middleware._processors.resolve(values)
    finally:
        asyncio.run(middleware.close())

    assert captured.value.code is ErrorCode.CONFIG_INVALID


def test_invalid_utf8_fails_before_invoking_an_engine() -> None:
    async def evaluate() -> None:
        middleware = PrivacyGuardMiddleware(create_builtin_registry())
        try:
            with pytest.raises(PrivacyGuardError) as captured:
                await middleware._evaluate_http_request(_request(b"\xff"))
            assert captured.value.code is ErrorCode.BODY_ENCODING_INVALID
        finally:
            await middleware.close()

    asyncio.run(evaluate())


def test_detect_returns_no_body_mutation() -> None:
    async def evaluate() -> pb2.HttpRequestResult:
        middleware = PrivacyGuardMiddleware(create_builtin_registry())
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
