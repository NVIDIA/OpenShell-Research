"""Service boundary tests over the canonical OpenShell-owned protobuf."""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier, Event, Lock, get_ident
from typing import Never

import grpc
import pytest
from google.protobuf import json_format
from google.protobuf.message import Message

from privacy_guard.bindings import supervisor_middleware_pb2 as pb2
from privacy_guard.config import (
    PrivacyGuardConfig,
    _configuration_fingerprint_and_size,
)
from privacy_guard.constants import (
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
    stage_name: str | None = None,
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


def _waiter_observing_future_type(
    *,
    expected_waiters: int,
    waiters_ready: Event,
) -> type[Future[RequestProcessor]]:
    waiter_count = 0
    waiter_count_lock = Lock()

    class _WaiterObservingFuture(Future[RequestProcessor]):
        def result(self, timeout: float | None = None) -> RequestProcessor:
            nonlocal waiter_count
            with waiter_count_lock:
                waiter_count += 1
                if waiter_count == expected_waiters:
                    waiters_ready.set()
            return super().result(timeout)

    return _WaiterObservingFuture


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

    with pytest.raises(PrivacyGuardError) as captured:
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
        middleware = PrivacyGuardMiddleware(create_builtin_registry())
        request = _request(b"no match", action="detect")
        request.context.request_id = invalid_request_id
        try:
            return await middleware._evaluate_rpc(
                request,
                _SuccessfulEvaluationContext(),
            )
        finally:
            await middleware.close()

    with caplog.at_level(logging.INFO, logger="privacy_guard.service.servicer"):
        result = asyncio.run(evaluate())

    records = [
        record
        for record in caplog.records
        if record.name == "privacy_guard.service.servicer"
        and record.getMessage().startswith("privacy_guard_evaluation ")
    ]
    assert result.decision == pb2.DECISION_ALLOW
    assert len(records) == 1
    assert "request_id=invalid " in records[0].getMessage()
    assert records[0].getMessage().isprintable()
    assert len(caplog.text.splitlines()) == 1


def test_evaluation_logs_printable_unicode_request_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def evaluate() -> None:
        middleware = PrivacyGuardMiddleware(create_builtin_registry())
        request = _request(b"no match", action="detect")
        request.context.request_id = "请求-42 🛡️"
        try:
            await middleware._evaluate_rpc(
                request,
                _SuccessfulEvaluationContext(),
            )
        finally:
            await middleware.close()

    with caplog.at_level(logging.INFO, logger="privacy_guard.service.servicer"):
        asyncio.run(evaluate())

    assert "request_id=请求-42 🛡️ " in caplog.text
    assert len(caplog.text.splitlines()) == 1


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


def test_same_fingerprint_misses_build_one_shared_processor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker_count = 4
    workers_ready = Barrier(worker_count)
    waiters_ready = Event()
    build_count = 0
    build_count_lock = Lock()
    original_build = servicer_module._RequestProcessorCache._build_processor

    def synchronized_build(
        cache: servicer_module._RequestProcessorCache,
        config: PrivacyGuardConfig[EngineConfig],
    ) -> RequestProcessor:
        nonlocal build_count
        with build_count_lock:
            build_count += 1
        assert waiters_ready.wait(timeout=5)
        return original_build(cache, config)

    monkeypatch.setattr(
        servicer_module,
        "Future",
        _waiter_observing_future_type(
            expected_waiters=worker_count - 1,
            waiters_ready=waiters_ready,
        ),
    )
    monkeypatch.setattr(
        servicer_module._RequestProcessorCache,
        "_build_processor",
        synchronized_build,
    )
    cache = servicer_module._RequestProcessorCache(
        create_builtin_registry(),
        timeout_seconds=1,
        log_request_content=False,
    )

    def resolve() -> RequestProcessor:
        workers_ready.wait(timeout=5)
        return cache.resolve(_values())

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        processors = tuple(executor.map(lambda _: resolve(), range(worker_count)))

    assert build_count == 1
    assert all(processor is processors[0] for processor in processors)
    _, expected_weight = _configuration_fingerprint_and_size(
        create_builtin_registry().validate_config(_values())
    )
    assert cache._weight_bytes == expected_weight
    assert cache._in_flight == {}


def test_different_fingerprints_build_concurrently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builds_ready = Barrier(2)
    original_build = servicer_module._RequestProcessorCache._build_processor

    def synchronized_build(
        cache: servicer_module._RequestProcessorCache,
        config: PrivacyGuardConfig[EngineConfig],
    ) -> RequestProcessor:
        builds_ready.wait(timeout=5)
        return original_build(cache, config)

    monkeypatch.setattr(
        servicer_module._RequestProcessorCache,
        "_build_processor",
        synchronized_build,
    )
    cache = servicer_module._RequestProcessorCache(
        create_builtin_registry(),
        timeout_seconds=1,
        log_request_content=False,
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        processors = tuple(
            executor.map(
                cache.resolve,
                (_values(action="detect"), _values(action="block")),
            )
        )

    assert processors[0] is not processors[1]
    assert len(cache._processors) == 2
    expected_weight = sum(
        _configuration_fingerprint_and_size(
            create_builtin_registry().validate_config(values)
        )[1]
        for values in (_values(action="detect"), _values(action="block"))
    )
    assert cache._weight_bytes == expected_weight
    assert cache._in_flight == {}


def test_failed_processor_build_wakes_waiters_and_allows_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker_count = 4
    workers_ready = Barrier(worker_count)
    waiters_ready = Event()
    build_count = 0
    build_count_lock = Lock()
    failure = PrivacyGuardError(ErrorCode.UNEXPECTED_SERVICE_FAILURE)
    original_build = servicer_module._RequestProcessorCache._build_processor

    def fail_first_build(
        cache: servicer_module._RequestProcessorCache,
        config: PrivacyGuardConfig[EngineConfig],
    ) -> RequestProcessor:
        nonlocal build_count
        with build_count_lock:
            build_count += 1
            current_build = build_count
        if current_build == 1:
            assert waiters_ready.wait(timeout=5)
            raise failure
        return original_build(cache, config)

    monkeypatch.setattr(
        servicer_module,
        "Future",
        _waiter_observing_future_type(
            expected_waiters=worker_count - 1,
            waiters_ready=waiters_ready,
        ),
    )
    monkeypatch.setattr(
        servicer_module._RequestProcessorCache,
        "_build_processor",
        fail_first_build,
    )
    cache = servicer_module._RequestProcessorCache(
        create_builtin_registry(),
        timeout_seconds=1,
        log_request_content=False,
    )

    def capture_failure() -> PrivacyGuardError:
        workers_ready.wait(timeout=5)
        with pytest.raises(PrivacyGuardError) as captured:
            cache.resolve(_values())
        return captured.value

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        failures = tuple(executor.map(lambda _: capture_failure(), range(worker_count)))

    assert build_count == 1
    assert all(error is failure for error in failures)
    assert cache._processors == {}
    assert cache._weight_bytes == 0
    assert cache._in_flight == {}

    processor = cache.resolve(_values())

    assert isinstance(processor, RequestProcessor)
    assert build_count == 2
    assert len(cache._processors) == 1
    assert cache._weight_bytes > 0
    assert cache._in_flight == {}


def test_processor_cache_evicts_least_recently_used_config_by_weight(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    registry = create_builtin_registry()
    values = tuple(
        _values(action="detect", stage_name=f"sensitive-stage-{suffix}")
        for suffix in ("a", "b", "c")
    )
    identities = tuple(
        _configuration_fingerprint_and_size(registry.validate_config(item))
        for item in values
    )
    assert len({weight for _, weight in identities}) == 1
    entry_weight = identities[0][1]
    monkeypatch.setattr(
        servicer_module,
        "MAX_PROCESSOR_CACHE_CONFIG_BYTES",
        entry_weight * 2,
    )
    cache = servicer_module._RequestProcessorCache(
        registry,
        timeout_seconds=1,
        log_request_content=False,
    )

    with caplog.at_level(logging.DEBUG, logger="privacy_guard.service.servicer"):
        first = cache.resolve(values[0])
        cache.resolve(values[1])
        assert cache.resolve(values[0]) is first
        cache.resolve(values[2])

    assert tuple(cache._processors) == (identities[0][0], identities[2][0])
    assert cache._weight_bytes == entry_weight * 2
    assert "privacy_guard_cache_eviction cache=processor entries=1" in caplog.text
    assert "sensitive-stage" not in caplog.text


def test_processor_cache_builds_but_does_not_retain_oversized_config(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    registry = create_builtin_registry()
    values = _values(action="detect", stage_name="sensitive-oversized-stage")
    _, weight_bytes = _configuration_fingerprint_and_size(
        registry.validate_config(values)
    )
    monkeypatch.setattr(
        servicer_module,
        "MAX_PROCESSOR_CACHE_CONFIG_BYTES",
        weight_bytes - 1,
    )
    build_count = 0
    original_build = servicer_module._RequestProcessorCache._build_processor

    def record_build(
        cache: servicer_module._RequestProcessorCache,
        config: PrivacyGuardConfig[EngineConfig],
    ) -> RequestProcessor:
        nonlocal build_count
        build_count += 1
        return original_build(cache, config)

    monkeypatch.setattr(
        servicer_module._RequestProcessorCache,
        "_build_processor",
        record_build,
    )
    cache = servicer_module._RequestProcessorCache(
        registry,
        timeout_seconds=1,
        log_request_content=False,
    )

    with caplog.at_level(logging.DEBUG, logger="privacy_guard.service.servicer"):
        first = cache.resolve(values)
        second = cache.resolve(values)

    assert first is not second
    assert build_count == 2
    assert cache._processors == {}
    assert cache._weight_bytes == 0
    assert caplog.text.count("privacy_guard_cache_skip cache=processor") == 2
    assert "sensitive-oversized-stage" not in caplog.text


def test_oversized_same_fingerprint_misses_remain_single_flight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publication_started = Event()
    waiter_ready = Event()
    release_publication = Event()

    class _PublicationPausingFuture(Future[RequestProcessor]):
        def result(self, timeout: float | None = None) -> RequestProcessor:
            waiter_ready.set()
            return super().result(timeout)

        def set_result(self, result: RequestProcessor) -> None:
            publication_started.set()
            assert release_publication.wait(timeout=5)
            super().set_result(result)

    registry = create_builtin_registry()
    values = _values(action="detect", stage_name="oversized-single-flight")
    _, weight_bytes = _configuration_fingerprint_and_size(
        registry.validate_config(values)
    )
    monkeypatch.setattr(
        servicer_module,
        "MAX_PROCESSOR_CACHE_CONFIG_BYTES",
        weight_bytes - 1,
    )
    monkeypatch.setattr(servicer_module, "Future", _PublicationPausingFuture)
    build_count = 0
    original_build = servicer_module._RequestProcessorCache._build_processor

    def record_build(
        cache: servicer_module._RequestProcessorCache,
        config: PrivacyGuardConfig[EngineConfig],
    ) -> RequestProcessor:
        nonlocal build_count
        build_count += 1
        return original_build(cache, config)

    monkeypatch.setattr(
        servicer_module._RequestProcessorCache,
        "_build_processor",
        record_build,
    )
    cache = servicer_module._RequestProcessorCache(
        registry,
        timeout_seconds=1,
        log_request_content=False,
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        owner = executor.submit(cache.resolve, values)
        assert publication_started.wait(timeout=5)
        waiter = executor.submit(cache.resolve, values)
        try:
            assert waiter_ready.wait(timeout=5)
        finally:
            release_publication.set()
        processors = (owner.result(timeout=5), waiter.result(timeout=5))

    assert processors[0] is processors[1]
    assert build_count == 1
    assert cache._processors == {}
    assert cache._weight_bytes == 0
    assert cache._in_flight == {}


def test_oversized_stage_list_fails_before_fingerprinting_or_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = _values(action="detect", stage_count=10_000)

    def unexpected_call(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("oversized stage list reached preparation")

    monkeypatch.setattr(
        servicer_module,
        "_configuration_fingerprint_and_size",
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
