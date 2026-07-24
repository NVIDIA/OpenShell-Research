"""gRPC boundary for cached entity-processing configurations."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from threading import RLock
from typing import Never, Protocol, TypedDict

import grpc
from google.protobuf import json_format
from google.protobuf.message import Message
from typing_extensions import override

from privacy_guard.bindings import supervisor_middleware_pb2 as pb2
from privacy_guard.bindings import supervisor_middleware_pb2_grpc as pb2_grpc
from privacy_guard.config import FinalizedPrivacyGuardConfig, configuration_fingerprint
from privacy_guard.constants import (
    BLOCK_REASON,
    BLOCK_REASON_CODE,
    LIMIT_REASON,
    LIMIT_REASON_CODE,
    MAX_BODY_BYTES,
    MAX_CONCURRENT_PROCESSING,
    MAX_PROTO_FINDING_BYTES,
    MAX_PROTO_FINDING_GROUPS,
    REASON_CODE_PATTERN,
    SERVICE_NAME,
    SERVICE_VERSION,
)
from privacy_guard.engine_registry import EngineRegistry
from privacy_guard.engines import ConfidenceLevel
from privacy_guard.errors import (
    EngineRegistryError,
    ErrorCode,
    ErrorKind,
    PrivacyGuardError,
)
from privacy_guard.request_processor import (
    EntityDetectionSummary,
    RequestDecision,
    RequestProcessingResult,
    RequestProcessor,
)


class PrivacyGuardMiddleware(pb2_grpc.SupervisorMiddlewareServicer):
    """Validate, prepare, resolve, and run Privacy Guard policies."""

    def __init__(
        self,
        registry: EngineRegistry,
        *,
        log_request_content: bool = False,
    ) -> None:
        if not registry.is_finalized:
            raise EngineRegistryError("middleware requires a finalized engine registry")
        self._registry = registry
        self._processors = _RequestProcessorCache(
            registry,
            log_request_content=log_request_content,
        )
        self._processing_slots = asyncio.Semaphore(MAX_CONCURRENT_PROCESSING)
        self._processing_executor = ThreadPoolExecutor(
            max_workers=MAX_CONCURRENT_PROCESSING,
            thread_name_prefix="privacy-guard-processing",
        )

    async def close(self) -> None:
        """Wait for in-flight synchronous engines during shutdown."""
        self._processing_executor.shutdown(wait=True, cancel_futures=True)

    @override
    async def Describe(
        self,
        request: object,
        context: grpc.aio.ServicerContext[object, pb2.MiddlewareManifest],
    ) -> pb2.MiddlewareManifest:
        """Advertise the binding and its finalized policy schema."""
        return self._describe()

    @override
    async def ValidateConfig(
        self,
        request: pb2.ValidateConfigRequest,
        context: grpc.aio.ServicerContext[
            pb2.ValidateConfigRequest,
            pb2.ValidateConfigResponse,
        ],
    ) -> pb2.ValidateConfigResponse:
        """Validate expanded configuration without preparing runtime state."""
        return self._validate_config(request)

    @override
    async def EvaluateHttpRequest(
        self,
        request: pb2.HttpRequestEvaluation,
        context: grpc.aio.ServicerContext[
            pb2.HttpRequestEvaluation,
            pb2.HttpRequestResult,
        ],
    ) -> pb2.HttpRequestResult:
        """Resolve the prepared config, decode one text, and process it."""
        return await self._evaluate_rpc(request, context)

    def _describe(self) -> pb2.MiddlewareManifest:
        return pb2.MiddlewareManifest(
            name=SERVICE_NAME,
            service_version=SERVICE_VERSION,
            bindings=[
                pb2.MiddlewareBinding(
                    operation=pb2.SUPERVISOR_MIDDLEWARE_OPERATION_HTTP_REQUEST,
                    phase=pb2.SUPERVISOR_MIDDLEWARE_PHASE_PRE_CREDENTIALS,
                    max_body_bytes=MAX_BODY_BYTES,
                )
            ],
        )

    def _validate_config(
        self,
        request: pb2.ValidateConfigRequest,
    ) -> pb2.ValidateConfigResponse:
        try:
            self._registry.validate_config(_mapping_from_proto(request.config))
        except PrivacyGuardError as error:
            return pb2.ValidateConfigResponse(valid=False, reason=str(error))
        except Exception:
            error = PrivacyGuardError(ErrorCode.UNEXPECTED_SERVICE_FAILURE)
            return pb2.ValidateConfigResponse(valid=False, reason=str(error))
        return pb2.ValidateConfigResponse(valid=True)

    async def _evaluate_rpc(
        self,
        request: pb2.HttpRequestEvaluation,
        context: _AbortContext,
    ) -> pb2.HttpRequestResult:
        started = time.monotonic()
        request_id = request.context.request_id
        failure: PrivacyGuardError | None = None
        action = "error"
        finding_count = 0
        try:
            response = await self._evaluate_http_request(request)
            action = "allow" if response.decision == pb2.DECISION_ALLOW else "deny"
            finding_count = sum(finding.count for finding in response.findings)
            return response
        except PrivacyGuardError as error:
            failure = error
        except Exception:
            failure = PrivacyGuardError(ErrorCode.UNEXPECTED_SERVICE_FAILURE)
        finally:
            log_extra = _evaluation_log_extra(
                request_id=request_id,
                started=started,
                action=action,
                finding_count=finding_count,
                failure=failure,
            )
            _LOGGER.info(
                "privacy_guard_evaluation request_id=%s duration_ms=%.3f "
                "action=%s finding_count=%d error_code=%s",
                log_extra["request_id"],
                log_extra["duration_ms"],
                log_extra["action"],
                log_extra["finding_count"],
                log_extra["error_code"] or "none",
                extra=log_extra,
            )
        status = (
            grpc.StatusCode.INVALID_ARGUMENT
            if failure.kind is ErrorKind.INVALID_INPUT
            else grpc.StatusCode.INTERNAL
        )
        await context.abort(status, str(failure))

    async def _evaluate_http_request(
        self,
        request: pb2.HttpRequestEvaluation,
    ) -> pb2.HttpRequestResult:
        if request.phase != pb2.SUPERVISOR_MIDDLEWARE_PHASE_PRE_CREDENTIALS:
            raise PrivacyGuardError(ErrorCode.REQUEST_PHASE_INVALID)
        if len(request.body) > MAX_BODY_BYTES:
            raise PrivacyGuardError(ErrorCode.REQUEST_BODY_TOO_LARGE)
        processor = self._processors.resolve(_mapping_from_proto(request.config))
        if not request.body:
            return pb2.HttpRequestResult(decision=pb2.DECISION_ALLOW)
        try:
            text = request.body.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise PrivacyGuardError(ErrorCode.BODY_ENCODING_INVALID) from None

        await self._processing_slots.acquire()
        try:
            worker = self._processing_executor.submit(
                processor.process,
                text,
            )
            future = asyncio.create_task(_await_worker(worker))
        except BaseException:
            self._processing_slots.release()
            raise
        future.add_done_callback(lambda _: self._processing_slots.release())
        result = await asyncio.shield(future)
        return _result_to_proto(result)


class _RequestProcessorCache:
    """Bounded, recoverable cache keyed by canonical expanded configuration."""

    def __init__(
        self,
        registry: EngineRegistry,
        *,
        log_request_content: bool,
    ) -> None:
        self._registry = registry
        self._log_request_content = log_request_content
        self._processors: OrderedDict[str, RequestProcessor] = OrderedDict()
        self._lock = RLock()

    def resolve(self, values: object) -> RequestProcessor:
        """Return the cached or newly prepared processor for expanded config."""
        _, processor = self._prepare(values)
        return processor

    def _prepare(self, values: object) -> tuple[str, RequestProcessor]:
        config = self._registry.validate_config(values)
        fingerprint = configuration_fingerprint(config)
        with self._lock:
            cached = self._processors.get(fingerprint)
            if cached is not None:
                self._processors.move_to_end(fingerprint)
                return fingerprint, cached
        processor = self._build_processor(config)
        with self._lock:
            self._processors[fingerprint] = processor
            self._processors.move_to_end(fingerprint)
            while len(self._processors) > _MAX_CACHED_PROCESSORS:
                self._processors.popitem(last=False)
        return fingerprint, processor

    def _build_processor(
        self,
        config: FinalizedPrivacyGuardConfig,
    ) -> RequestProcessor:
        stages = tuple(
            (
                stage.diagnostic_name(index),
                self._registry.create_engine(stage.config),
            )
            for index, stage in enumerate(
                config.entity_processing.stages,
                start=1,
            )
        )
        return RequestProcessor(
            config,
            stages,
            log_request_content=self._log_request_content,
        )


async def _await_worker(
    worker: Future[RequestProcessingResult],
) -> RequestProcessingResult:
    """Bridge a worker without relying on broken cross-thread loop wakeups."""
    while not worker.done():
        await asyncio.sleep(0.001)
    return worker.result()


class _AbortContext(Protocol):
    async def abort(self, code: grpc.StatusCode, details: str) -> Never: ...


class _EvaluationLogExtra(TypedDict):
    request_id: str
    duration_ms: float
    action: str
    finding_count: int
    error_code: str | None


def _evaluation_log_extra(
    *,
    request_id: str,
    started: float,
    action: str,
    finding_count: int,
    failure: PrivacyGuardError | None,
) -> _EvaluationLogExtra:
    return {
        "request_id": request_id,
        "duration_ms": round((time.monotonic() - started) * 1000, 3),
        "action": action,
        "finding_count": finding_count,
        "error_code": failure.code.value if failure is not None else None,
    }


def _mapping_from_proto(config: Message) -> dict[str, object]:
    try:
        return json_format.MessageToDict(config)
    except Exception:
        raise PrivacyGuardError(ErrorCode.CONFIG_INVALID) from None


def _result_to_proto(result: RequestProcessingResult) -> pb2.HttpRequestResult:
    try:
        findings = [
            _detection_to_proto(detection) for detection in result.detection_summaries
        ]
    except PrivacyGuardError as error:
        if error.code is ErrorCode.RESULT_LIMIT_EXCEEDED:
            return _limit_deny()
        raise
    if len(findings) > MAX_PROTO_FINDING_GROUPS:
        return _limit_deny()
    if result.decision is RequestDecision.ALLOW:
        replacement = result.replacement_text
        replacement_body = (
            replacement.encode("utf-8") if replacement is not None else b""
        )
        if len(replacement_body) > MAX_BODY_BYTES:
            return _limit_deny()
        return pb2.HttpRequestResult(
            decision=pb2.DECISION_ALLOW,
            body=replacement_body,
            has_body=replacement is not None,
            findings=findings,
        )
    if result.decision is RequestDecision.DENY:
        reason_code = result.reason_code or BLOCK_REASON_CODE
        if REASON_CODE_PATTERN.fullmatch(reason_code) is None:
            return _limit_deny()
        return pb2.HttpRequestResult(
            decision=pb2.DECISION_DENY,
            reason=LIMIT_REASON if reason_code == LIMIT_REASON_CODE else BLOCK_REASON,
            reason_code=reason_code,
            findings=findings,
        )
    raise PrivacyGuardError(ErrorCode.UNEXPECTED_SERVICE_FAILURE)


def _detection_to_proto(detection: EntityDetectionSummary) -> pb2.Finding:
    confidence = detection.confidence
    if isinstance(confidence, ConfidenceLevel):
        confidence_text = confidence.value
    elif confidence is None:
        confidence_text = ""
    else:
        confidence_text = format(confidence, ".12g")
    result = pb2.Finding(
        type="detected_entity",
        label=f"{detection.entity} ({detection.source_stage})",
        confidence=confidence_text,
        count=detection.count,
    )
    if result.ByteSize() > MAX_PROTO_FINDING_BYTES:
        raise PrivacyGuardError(ErrorCode.RESULT_LIMIT_EXCEEDED)
    return result


def _limit_deny() -> pb2.HttpRequestResult:
    return pb2.HttpRequestResult(
        decision=pb2.DECISION_DENY,
        reason=LIMIT_REASON,
        reason_code=LIMIT_REASON_CODE,
    )


_LOGGER = logging.getLogger(__name__)
_MAX_CACHED_PROCESSORS = 128


__all__ = ["PrivacyGuardMiddleware"]
