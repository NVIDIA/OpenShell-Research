"""gRPC boundary for active entity-processing policy evaluation."""

from __future__ import annotations

import asyncio
import json
import math
import time
from collections.abc import Callable, Iterable
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Lock
from typing import Never, Protocol, TypedDict, TypeVar

import grpc
from google.protobuf import json_format
from google.protobuf.message import Message

from egress_gate.bindings import supervisor_middleware_pb2 as pb2
from egress_gate.bindings import supervisor_middleware_pb2_grpc as pb2_grpc
from egress_gate.config import EgressGateConfig
from egress_gate.constants import (
    BLOCK_REASON,
    BLOCK_REASON_CODE,
    DEFAULT_TIMEOUT_SECONDS,
    LIMIT_REASON,
    LIMIT_REASON_CODE,
    MAX_BODY_BYTES,
    MAX_CONCURRENT_PROCESSING,
    MAX_PROTO_CONFIG_BYTES,
    MAX_PROTO_CONTEXT_BYTES,
    MAX_PROTO_FINDING_BYTES,
    MAX_PROTO_FINDING_GROUPS,
    MAX_PROTO_HEADERS,
    MAX_PROTO_HEADERS_BYTES,
    MAX_PROTO_TARGET_BYTES,
    REASON_CODE_PATTERN,
    SERVICE_NAME,
    SERVICE_VERSION,
)
from egress_gate.engines import EngineConfig
from egress_gate.engines.registry import EngineRegistry
from egress_gate.errors import (
    EgressGateError,
    EngineRegistryError,
    ErrorCode,
    ErrorKind,
)
from egress_gate.logging import get_logger
from egress_gate.request_processor import (
    EntityDetectionSummary,
    RequestDecision,
    RequestProcessingResult,
    RequestProcessor,
)
from egress_gate.string_validators import validate_bounded_metadata_string
from egress_gate.timeout import validate_timeout_seconds


class EgressGateMiddleware(pb2_grpc.SupervisorMiddlewareServicer):
    """Validate, prepare, resolve, and run Egress Gate policies."""

    def __init__(
        self,
        registry: EngineRegistry,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        log_request_content: bool = False,
    ) -> None:
        if not registry.is_finalized:
            raise EngineRegistryError("middleware requires a finalized engine registry")
        self._registry = registry
        self._policy = _ActivePolicy(
            registry,
            timeout_seconds=validate_timeout_seconds(timeout_seconds),
            log_request_content=log_request_content,
        )
        self._processing_slots = asyncio.Semaphore(MAX_CONCURRENT_PROCESSING)
        self._processing_executor = ThreadPoolExecutor(
            max_workers=MAX_CONCURRENT_PROCESSING,
            thread_name_prefix="egress-gate-processing",
        )

    async def close(self) -> None:
        """Wait for in-flight synchronous engines during shutdown."""
        self._processing_executor.shutdown(wait=True, cancel_futures=True)
        self._policy.clear()

    async def Describe(
        self,
        request: object,
        context: grpc.aio.ServicerContext[object, pb2.MiddlewareManifest],
    ) -> pb2.MiddlewareManifest:
        """Advertise the binding and its finalized policy schema."""
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

    async def ValidateConfig(
        self,
        request: pb2.ValidateConfigRequest,
        context: grpc.aio.ServicerContext[
            pb2.ValidateConfigRequest,
            pb2.ValidateConfigResponse,
        ],
    ) -> pb2.ValidateConfigResponse:
        """Validate expanded configuration without preparing runtime state."""
        return await self._run_in_worker(lambda: self._validate_config(request))

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

    def _validate_config(
        self,
        request: pb2.ValidateConfigRequest,
    ) -> pb2.ValidateConfigResponse:
        try:
            if request.config.ByteSize() > MAX_PROTO_CONFIG_BYTES:
                raise EgressGateError(ErrorCode.CONFIG_INVALID)
            self._registry.validate_config(_mapping_from_proto(request.config))
        except EgressGateError as error:
            return pb2.ValidateConfigResponse(valid=False, reason=str(error))
        except Exception:
            error = EgressGateError(ErrorCode.UNEXPECTED_SERVICE_FAILURE)
            return pb2.ValidateConfigResponse(valid=False, reason=str(error))
        return pb2.ValidateConfigResponse(valid=True)

    async def _evaluate_rpc(
        self,
        request: pb2.HttpRequestEvaluation,
        context: _AbortContext,
    ) -> pb2.HttpRequestResult:
        started = time.monotonic()
        request_id = _request_id_for_logging(request.context.request_id)
        failure: EgressGateError | None = None
        action = "error"
        finding_count = 0
        try:
            response = await self._evaluate_http_request(request)
            action = "allow" if response.decision == pb2.DECISION_ALLOW else "deny"
            finding_count = sum(finding.count for finding in response.findings)
            return response
        except EgressGateError as error:
            failure = error
        except Exception:
            failure = EgressGateError(ErrorCode.UNEXPECTED_SERVICE_FAILURE)
        finally:
            log_extra = _evaluation_log_extra(
                request_id=request_id,
                started=started,
                action=action,
                finding_count=finding_count,
                failure=failure,
            )
            _LOGGER.info(
                "egress_gate_evaluation request_id=%s duration_ms=%.3f "
                "action=%s finding_count=%d error_code=%s",
                _request_id_for_log_message(log_extra["request_id"]),
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
            raise EgressGateError(ErrorCode.REQUEST_PHASE_INVALID)
        if len(request.body) > MAX_BODY_BYTES:
            raise EgressGateError(ErrorCode.REQUEST_BODY_TOO_LARGE)
        _validate_evaluation_envelope(request)
        result = await self._run_in_worker(
            lambda: self._prepare_and_process(request.config, request.body)
        )
        return _result_to_proto(result)

    def _prepare_and_process(
        self,
        config: Message,
        body: bytes,
    ) -> RequestProcessingResult:
        values = _mapping_from_proto(config)
        processor = self._policy.processor_for(values)
        if not body:
            return RequestProcessingResult(decision=RequestDecision.ALLOW)
        try:
            text = body.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise EgressGateError(ErrorCode.BODY_ENCODING_INVALID) from None
        return processor.process(text)

    async def _run_in_worker(
        self,
        operation: Callable[[], _WorkerResultT],
    ) -> _WorkerResultT:
        """Run one bounded synchronous operation without blocking the event loop."""
        await self._processing_slots.acquire()
        try:
            worker = self._processing_executor.submit(operation)
            future = asyncio.create_task(_await_worker(worker))
        except BaseException:
            self._processing_slots.release()
            raise
        future.add_done_callback(lambda _: self._processing_slots.release())
        return await asyncio.shield(future)


class _ActivePolicy:
    """Own the process's active policy and its prepared processor."""

    def __init__(
        self,
        registry: EngineRegistry,
        *,
        timeout_seconds: float,
        log_request_content: bool,
    ) -> None:
        self._registry = registry
        self._timeout_seconds = timeout_seconds
        self._log_request_content = log_request_content
        self._config: EgressGateConfig[EngineConfig] | None = None
        self._processor: RequestProcessor | None = None
        self._lock = Lock()

    def processor_for(self, values: object) -> RequestProcessor:
        """Return the processor for the requested policy, activating it if needed."""
        config = self._registry.validate_config(values)
        with self._lock:
            if config == self._config and self._processor is not None:
                return self._processor
            processor = self._build_processor(config)
            self._config = config
            self._processor = processor
            return processor

    def _build_processor(
        self,
        config: EgressGateConfig[EngineConfig],
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
            timeout_seconds=self._timeout_seconds,
            log_request_content=self._log_request_content,
        )

    def clear(self) -> None:
        """Release the active policy."""
        with self._lock:
            self._config = None
            self._processor = None


_WorkerResultT = TypeVar("_WorkerResultT")


async def _await_worker(worker: Future[_WorkerResultT]) -> _WorkerResultT:
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
    failure: EgressGateError | None,
) -> _EvaluationLogExtra:
    return {
        "request_id": request_id,
        "duration_ms": round((time.monotonic() - started) * 1000, 3),
        "action": action,
        "finding_count": finding_count,
        "error_code": failure.code.value if failure is not None else None,
    }


def _request_id_for_logging(request_id: object) -> str:
    try:
        return validate_bounded_metadata_string(request_id)
    except ValueError:
        return _INVALID_REQUEST_ID


def _request_id_for_log_message(request_id: str) -> str:
    return json.dumps(request_id, ensure_ascii=False).replace(" ", r"\u0020")


def _mapping_from_proto(config: Message) -> dict[str, object]:
    try:
        values: object = json_format.MessageToDict(config)
    except Exception:
        raise EgressGateError(ErrorCode.CONFIG_INVALID) from None
    if not isinstance(values, dict) or any(not isinstance(key, str) for key in values):
        raise EgressGateError(ErrorCode.CONFIG_INVALID)
    return {
        key: _normalize_proto_numbers(item)
        for key, item in values.items()
        if isinstance(key, str)
    }


def _normalize_proto_numbers(value: object) -> object:
    if isinstance(value, float):
        if (
            math.isfinite(value)
            and value.is_integer()
            and -_MAX_PROTO_SAFE_INTEGER <= value <= _MAX_PROTO_SAFE_INTEGER
        ):
            return int(value)
        return value
    if isinstance(value, list):
        return [_normalize_proto_numbers(item) for item in value]
    if isinstance(value, dict):
        return {key: _normalize_proto_numbers(item) for key, item in value.items()}
    return value


def _validate_evaluation_envelope(request: pb2.HttpRequestEvaluation) -> None:
    if request.config.ByteSize() > MAX_PROTO_CONFIG_BYTES:
        raise EgressGateError(ErrorCode.CONFIG_INVALID)
    if (
        request.context.ByteSize() > MAX_PROTO_CONTEXT_BYTES
        or request.target.ByteSize() > MAX_PROTO_TARGET_BYTES
        or len(request.headers) > MAX_PROTO_HEADERS
        or _encoded_headers_size(request.headers) > MAX_PROTO_HEADERS_BYTES
    ):
        raise EgressGateError(ErrorCode.REQUEST_ENVELOPE_INVALID)


def _encoded_headers_size(headers: Iterable[Message]) -> int:
    total = 0
    for header in headers:
        size = header.ByteSize()
        total += 1 + _varint_size(size) + size
    return total


def _varint_size(value: int) -> int:
    size = 1
    while value >= 0x80:
        value >>= 7
        size += 1
    return size


def _result_to_proto(result: RequestProcessingResult) -> pb2.HttpRequestResult:
    findings: list[pb2.Finding] = []
    for detection in result.detection_summaries:
        finding = _detection_to_proto(detection)
        if finding.ByteSize() > MAX_PROTO_FINDING_BYTES:
            return _limit_deny()
        findings.append(finding)
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
    raise EgressGateError(ErrorCode.UNEXPECTED_SERVICE_FAILURE)


def _detection_to_proto(detection: EntityDetectionSummary) -> pb2.Finding:
    confidence = detection.confidence
    confidence_text = confidence.value if confidence is not None else ""
    result = pb2.Finding(
        type="detected_entity",
        label=f"{detection.entity} ({detection.source_stage})",
        confidence=confidence_text,
        count=detection.count,
    )
    return result


def _limit_deny() -> pb2.HttpRequestResult:
    _LOGGER.info("egress_gate_processing_limit kind=resource")
    return pb2.HttpRequestResult(
        decision=pb2.DECISION_DENY,
        reason=LIMIT_REASON,
        reason_code=LIMIT_REASON_CODE,
    )


_LOGGER = get_logger(__name__)
_INVALID_REQUEST_ID = "invalid"
_MAX_PROTO_SAFE_INTEGER = (1 << 53) - 1


__all__ = ["EgressGateMiddleware"]
