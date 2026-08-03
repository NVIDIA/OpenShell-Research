"""gRPC boundary for the protobuf-free Egress Gate pipeline runtime."""

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
from egress_gate.errors import (
    EgressGateError,
    ErrorCode,
    ErrorKind,
    GateConfigurationError,
    GateRegistryError,
    TimeoutExpiredError,
)
from egress_gate.gates.base import Gate, GateConfig, GateResources
from egress_gate.gates.registry import GateRegistry
from egress_gate.logging import get_logger
from egress_gate.request import (
    HeaderMutation,
    HttpHeader,
    HttpRequest,
    HttpTarget,
    Process,
    RemoveHeaderMutation,
    RequestContext,
    WriteHeaderMutation,
)
from egress_gate.request_processor import RequestProcessor
from egress_gate.result import EgressDecision, EgressResult, SourcedFinding
from egress_gate.string_validators import validate_bounded_metadata_string
from egress_gate.timeout import Timeout, validate_timeout_seconds


class EgressGateMiddleware(pb2_grpc.SupervisorMiddlewareServicer):
    """Validate, prepare, resolve, and run Egress Gate policies."""

    def __init__(
        self,
        registry: GateRegistry,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        log_request_content: bool = False,
    ) -> None:
        if not registry.is_finalized:
            raise GateRegistryError("middleware requires a finalized gate registry")
        self._registry = registry
        self._timeout_seconds = validate_timeout_seconds(timeout_seconds)
        self._policy = _ActivePolicy(
            registry,
            log_request_content=log_request_content,
        )
        self._processing_slots = asyncio.Semaphore(MAX_CONCURRENT_PROCESSING)
        self._processing_executor = ThreadPoolExecutor(
            max_workers=MAX_CONCURRENT_PROCESSING,
            thread_name_prefix="egress-gate-processing",
        )

    async def close(self) -> None:
        """Wait for in-flight synchronous gates during shutdown."""
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
        """Resolve the prepared pipeline and evaluate one current request."""
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
        source_kind = "none"
        try:
            timeout = Timeout.from_seconds(self._timeout_seconds)
            response = await self._evaluate_http_request(request, timeout)
            action = "allow" if response.decision == pb2.DECISION_ALLOW else "deny"
            finding_count = sum(finding.count for finding in response.findings)
            return response
        except TimeoutExpiredError:
            response = _limit_deny()
            action = "deny"
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
                source_kind=source_kind,
                failure=failure,
            )
            _LOGGER.info(
                "egress_gate_evaluation request_id=%s duration_ms=%.3f "
                "action=%s finding_count=%d decision_source_kind=%s error_code=%s",
                _request_id_for_log_message(log_extra["request_id"]),
                log_extra["duration_ms"],
                log_extra["action"],
                log_extra["finding_count"],
                log_extra["decision_source_kind"],
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
        timeout: Timeout,
    ) -> pb2.HttpRequestResult:
        if request.phase != pb2.SUPERVISOR_MIDDLEWARE_PHASE_PRE_CREDENTIALS:
            raise EgressGateError(ErrorCode.REQUEST_PHASE_INVALID)
        if len(request.body) > MAX_BODY_BYTES:
            raise EgressGateError(ErrorCode.REQUEST_BODY_TOO_LARGE)
        _validate_evaluation_envelope(request)
        result = await self._run_in_worker(
            lambda: self._prepare_and_process(request, timeout),
            timeout=timeout,
        )
        return _result_to_proto(result)

    def _prepare_and_process(
        self,
        request: pb2.HttpRequestEvaluation,
        timeout: Timeout,
    ) -> EgressResult:
        values = _mapping_from_proto(request.config)
        processor = self._policy.processor_for(values, timeout=timeout)
        domain_request = _request_from_proto(request)
        return processor.process(domain_request, timeout=timeout)

    async def _run_in_worker(
        self,
        operation: Callable[[], _WorkerResultT],
        *,
        timeout: Timeout | None = None,
    ) -> _WorkerResultT:
        """Run one bounded synchronous operation without blocking the event loop."""
        try:
            if timeout is None:
                await self._processing_slots.acquire()
            else:
                await asyncio.wait_for(
                    self._processing_slots.acquire(),
                    timeout=timeout.remaining_seconds(),
                )
        except TimeoutError:
            raise TimeoutExpiredError from None
        try:
            if timeout is not None:
                timeout.raise_if_expired()
            worker = self._processing_executor.submit(operation)
            future = asyncio.create_task(_await_worker(worker))
        except BaseException:
            self._processing_slots.release()
            raise
        future.add_done_callback(lambda _: self._processing_slots.release())
        return await asyncio.shield(future)


class _ActivePolicy:
    """Own one active validated policy and its prepared immutable gates."""

    def __init__(
        self,
        registry: GateRegistry,
        *,
        log_request_content: bool,
    ) -> None:
        self._registry = registry
        self._log_request_content = log_request_content
        self._config: EgressGateConfig[GateConfig] | None = None
        self._processor: RequestProcessor | None = None
        self._lock = Lock()

    def processor_for(
        self,
        values: object,
        *,
        timeout: Timeout,
    ) -> RequestProcessor:
        """Validate and activate a complete candidate under the shared deadline."""
        config = self._registry.validate_config(values)
        timeout.raise_if_expired()
        if not self._lock.acquire(timeout=timeout.remaining_seconds()):
            raise TimeoutExpiredError
        try:
            timeout.raise_if_expired()
            if config == self._config and self._processor is not None:
                return self._processor
            processor = self._build_processor(config, timeout=timeout)
            timeout.raise_if_expired()
            self._config = config
            self._processor = processor
            return processor
        except (GateConfigurationError, GateRegistryError):
            raise EgressGateError(ErrorCode.CONFIG_INVALID) from None
        finally:
            self._lock.release()

    def _build_processor(
        self,
        config: EgressGateConfig[GateConfig],
        *,
        timeout: Timeout,
    ) -> RequestProcessor:
        prepared: list[tuple[str, str, Gate[GateConfig, GateResources | None]]] = []
        for configured_gate in config.pipeline.gates:
            timeout.raise_if_expired()
            gate_type = getattr(configured_gate.config, "gate", None)
            if not isinstance(gate_type, str):
                raise GateRegistryError("gate config discriminator is invalid")
            prepared.append(
                (
                    configured_gate.name,
                    gate_type,
                    self._registry.create_gate(configured_gate.config),
                )
            )
        fingerprint = self._registry.policy_fingerprint(config)
        return RequestProcessor(
            config,
            tuple(prepared),
            policy_fingerprint=fingerprint,
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
    decision_source_kind: str
    error_code: str | None


def _evaluation_log_extra(
    *,
    request_id: str,
    started: float,
    action: str,
    finding_count: int,
    source_kind: str,
    failure: EgressGateError | None,
) -> _EvaluationLogExtra:
    return {
        "request_id": request_id,
        "duration_ms": round((time.monotonic() - started) * 1000, 3),
        "action": action,
        "finding_count": finding_count,
        "decision_source_kind": source_kind,
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


def _request_from_proto(request: pb2.HttpRequestEvaluation) -> HttpRequest:
    process = None
    if request.context.HasField("originating_process"):
        process = Process(
            binary=request.context.originating_process.binary,
            pid=request.context.originating_process.pid,
            ancestors=tuple(request.context.originating_process.ancestors),
        )
    try:
        return HttpRequest(
            context=RequestContext(
                request_id=request.context.request_id,
                sandbox_id=request.context.sandbox_id,
                originating_process=process,
            ),
            target=HttpTarget(
                scheme=request.target.scheme,
                host=request.target.host,
                port=request.target.port,
                method=request.target.method,
                path=request.target.path,
                query=request.target.query,
            ),
            headers=tuple(
                HttpHeader(name=header.name, value=header.value)
                for header in request.headers
            ),
            body=request.body,
        )
    except (TypeError, ValueError):
        raise EgressGateError(ErrorCode.REQUEST_ENVELOPE_INVALID) from None


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


def _result_to_proto(result: EgressResult) -> pb2.HttpRequestResult:
    try:
        response = pb2.HttpRequestResult(
            decision=(
                pb2.DECISION_ALLOW
                if result.decision is EgressDecision.ALLOW
                else pb2.DECISION_DENY
            ),
            reason_code=result.reason_code or "",
        )
        for sourced in result.findings:
            finding = _finding_to_proto(sourced)
            if finding.ByteSize() > MAX_PROTO_FINDING_BYTES:
                return _limit_deny()
            response.findings.append(finding)
        if len(response.findings) > MAX_PROTO_FINDING_GROUPS:
            return _limit_deny()
        if result.decision is EgressDecision.ALLOW:
            if result.patch.replacement_body is not None:
                if len(result.patch.replacement_body) > MAX_BODY_BYTES:
                    return _limit_deny()
                response.body = result.patch.replacement_body
                response.has_body = True
            for mutation in result.patch.header_mutations:
                _append_header_mutation(response, mutation)
            response.metadata.update(
                {entry.key: entry.value for entry in result.metadata}
            )
            if (
                _encoded_headers_size(response.header_mutations)
                > MAX_PROTO_HEADERS_BYTES
            ):
                return _limit_deny()
            return response
        if (
            result.reason_code is None
            or REASON_CODE_PATTERN.fullmatch(result.reason_code) is None
        ):
            return _limit_deny()
        response.reason = (
            LIMIT_REASON if result.reason_code == LIMIT_REASON_CODE else BLOCK_REASON
        )
        return response
    except (TypeError, ValueError):
        return _limit_deny()


def _finding_to_proto(sourced: SourcedFinding) -> pb2.Finding:
    finding = sourced.finding
    return pb2.Finding(
        type=finding.type,
        label=finding.label,
        count=finding.count,
        confidence=finding.confidence or "",
        severity=finding.severity or "",
    )


def _append_header_mutation(
    response: pb2.HttpRequestResult,
    mutation: HeaderMutation,
) -> None:
    if isinstance(mutation, WriteHeaderMutation):
        action = {
            "append": pb2.EXISTING_HEADER_ACTION_APPEND,
            "overwrite": pb2.EXISTING_HEADER_ACTION_OVERWRITE,
            "skip": pb2.EXISTING_HEADER_ACTION_SKIP,
        }[mutation.on_existing.value]
        response.header_mutations.add(
            write=pb2.WriteHeader(
                name=mutation.name,
                value=mutation.value,
                on_existing=action,
            )
        )
    elif isinstance(mutation, RemoveHeaderMutation):
        response.header_mutations.add(remove=pb2.RemoveHeader(name=mutation.name))
    else:
        raise ValueError("header mutation is invalid")


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
