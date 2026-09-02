# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""gRPC boundary for the protobuf-free Egress Gate pipeline processor."""

from __future__ import annotations

import asyncio
import json
import math
import time
from collections.abc import Callable, Iterable
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Lock
from typing import Literal, Never, Protocol, TypedDict, TypeVar

import grpc
from google.protobuf import json_format
from google.protobuf.message import Message

from egress_gate.admission import (
    MAX_ADMISSION_BODY_BYTES,
    PI_HARNESS_VERSION,
    RECEIPT_HEADER,
    AdmissionDecision,
    AdmissionHook,
    AdmissionProvenance,
    AttestedEgressProcessor,
    HarnessAdmissionContext,
    HarnessAdmissionProcessor,
    HarnessAdmissionRequest,
    ReceiptAuthority,
    create_pi_adapter_registry,
    create_provider_adapter_registry,
)
from egress_gate.bindings import supervisor_middleware_pb2 as pb2
from egress_gate.bindings import supervisor_middleware_pb2_grpc as pb2_grpc
from egress_gate.config import EgressGateConfig
from egress_gate.constants import (
    BLOCK_REASON,
    DEFAULT_TIMEOUT_MIDDLEWARE_PROCESSING,
    LIMIT_REASON,
    LIMIT_REASON_CODE,
    MAX_AGENT_ATTESTATION_BYTES,
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
from egress_gate.gates.base import GateConfig
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
from egress_gate.result import (
    DecisionSourceKind,
    EgressDecision,
    EgressResult,
    GateDecisionSource,
    SourcedFinding,
)
from egress_gate.string_validators import validate_bounded_metadata_string
from egress_gate.timeout import (
    Timeout,
    format_timeout_middleware_processing,
    validate_timeout_middleware_processing,
)


def _require_harness_version(value: str) -> Literal["sdk-v1"]:
    if value == PI_HARNESS_VERSION:
        return value
    raise ValueError("invalid admission harness version")


class EgressGateMiddleware(pb2_grpc.SupervisorMiddlewareServicer):
    """Validate, prepare, resolve, and run Egress Gate policies."""

    def __init__(
        self,
        registry: GateRegistry,
        *,
        timeout_middleware_processing: float = DEFAULT_TIMEOUT_MIDDLEWARE_PROCESSING,
        require_agent_attestation: bool = False,
    ) -> None:
        registry.configuration_json_schema()
        self._registry = registry
        self._timeout_middleware_processing_seconds = (
            validate_timeout_middleware_processing(timeout_middleware_processing)
        )
        self._policy = _ActivePolicy(registry)
        self._receipt_authority = ReceiptAuthority()
        self._admission_adapters = create_pi_adapter_registry()
        self._require_agent_attestation = require_agent_attestation
        self._processing_slots = asyncio.Semaphore(MAX_CONCURRENT_PROCESSING)
        self._processing_executor = ThreadPoolExecutor(
            max_workers=MAX_CONCURRENT_PROCESSING,
            thread_name_prefix="egress-gate-processing",
        )

    @property
    def timeout_middleware_processing(self) -> str:
        """Return the configured middleware processing timeout."""
        return format_timeout_middleware_processing(
            self._timeout_middleware_processing_seconds
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
        """Describe the binding and its complete policy schema."""
        # The protocol does not expose the operator-configured gateway timeout
        # to this service. An empty binding timeout leaves that RPC limit under
        # gateway ownership instead of replacing it with the internal budget.
        return pb2.MiddlewareManifest(
            name=SERVICE_NAME,
            service_version=SERVICE_VERSION,
            bindings=[
                pb2.MiddlewareBinding(
                    operation=pb2.SUPERVISOR_MIDDLEWARE_OPERATION_HTTP_REQUEST,
                    phase=pb2.SUPERVISOR_MIDDLEWARE_PHASE_PRE_CREDENTIALS,
                    max_payload_bytes=MAX_BODY_BYTES,
                ),
                *(
                    pb2.MiddlewareBinding(
                        operation=pb2.SUPERVISOR_MIDDLEWARE_OPERATION_AGENT_CONVERSATION,
                        phase=pb2.SUPERVISOR_MIDDLEWARE_PHASE_AGENT_CONTEXT,
                        max_payload_bytes=MAX_ADMISSION_BODY_BYTES,
                        harness=harness,
                        hook=hook,
                        schema_version=schema_version,
                    )
                    for harness, hook, schema_version in (
                        self._admission_adapters.bindings
                    )
                    if self._require_agent_attestation
                ),
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
        """Validate expanded configuration without preparing processor state."""
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

    async def EvaluateAgentConversation(
        self,
        request: pb2.AgentConversationEvaluation,
        context: grpc.aio.ServicerContext[
            pb2.AgentConversationEvaluation,
            pb2.AgentConversationResult,
        ],
    ) -> pb2.AgentConversationResult:
        """Evaluate one supervisor-stamped Pi admission request."""
        timeout = Timeout.from_seconds(self._timeout_middleware_processing_seconds)
        return await self._run_in_worker(
            lambda: self._evaluate_agent_admission(request, timeout),
            timeout=timeout,
        )

    def _evaluate_agent_admission(
        self,
        request: pb2.AgentConversationEvaluation,
        timeout: Timeout,
    ) -> pb2.AgentConversationResult:
        try:
            if not self._require_agent_attestation:
                raise ValueError("agent admission is disabled")
            if request.phase != pb2.SUPERVISOR_MIDDLEWARE_PHASE_AGENT_CONTEXT:
                raise ValueError("invalid admission phase")
            if len(request.request_body) > MAX_ADMISSION_BODY_BYTES:
                raise ValueError("admission request body is too large")
            hook = AdmissionHook(request.target.hook)
            target = HttpTarget(
                scheme=request.target.scheme,
                host=request.target.host,
                port=request.target.port,
                method="POST",
                path=request.target.path,
                query="",
            )
            provenance = AdmissionProvenance(
                session_id=request.session_id,
                submission_id=request.turn_id,
            )
            processor = HarnessAdmissionProcessor(
                self._policy.processor_for(
                    _mapping_from_proto(request.config), timeout=timeout
                ),
                self._admission_adapters,
                self._receipt_authority,
            )
            result = processor.process(
                HarnessAdmissionRequest(
                    request_body=request.request_body,
                    provenance=provenance,
                ),
                HarnessAdmissionContext(
                    request_id=request.context.request_id,
                    sandbox_id=request.context.sandbox_id,
                    middleware_name=request.middleware_name,
                    harness=request.target.harness,
                    harness_version=_require_harness_version(
                        request.target.harness_version
                    ),
                    hook=hook,
                    schema_version=request.target.schema_version,
                    provider_target=target,
                    provider_adapter_schema="openai.request.v1",
                ),
                timeout=timeout,
            )
            response = pb2.AgentConversationResult(
                decision=(
                    pb2.DECISION_DENY
                    if result.decision is AdmissionDecision.DENY
                    else pb2.DECISION_ALLOW
                ),
                reason_code=result.reason_code or "",
                attestation=result.attestation or b"",
                replacement_body=result.replacement_body or b"",
                has_replacement_body=result.replacement_body is not None,
            )
            response.findings.extend(
                _finding_to_proto(item) for item in result.findings
            )
            return response
        except Exception:
            return pb2.AgentConversationResult(
                decision=pb2.DECISION_DENY,
                reason_code="admission_unavailable",
            )

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
        reason_code: str | None = None
        finding_count = 0
        source_kind = "none"
        try:
            timeout = Timeout.from_seconds(self._timeout_middleware_processing_seconds)
            response, source_kind = await self._evaluate_http_request(
                request,
                timeout,
            )
            action = "allow" if response.decision == pb2.DECISION_ALLOW else "deny"
            reason_code = response.reason_code or None
            finding_count = sum(finding.count for finding in response.findings)
            return response
        except TimeoutExpiredError:
            response = _limit_deny()
            action = "deny"
            reason_code = response.reason_code or None
            source_kind = DecisionSourceKind.RUNTIME_LIMIT.value
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
                reason_code=reason_code,
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
    ) -> tuple[pb2.HttpRequestResult, str]:
        if request.phase != pb2.SUPERVISOR_MIDDLEWARE_PHASE_PRE_CREDENTIALS:
            raise EgressGateError(ErrorCode.REQUEST_PHASE_INVALID)
        if len(request.body) > MAX_BODY_BYTES:
            raise EgressGateError(ErrorCode.REQUEST_BODY_TOO_LARGE)
        _validate_evaluation_envelope(request)
        result = await self._run_in_worker(
            lambda: self._prepare_and_process(
                request,
                timeout,
            ),
            timeout=timeout,
        )
        timeout.raise_if_expired()
        response, source_kind = _result_to_proto(result)
        timeout.raise_if_expired()
        return response, source_kind

    def _prepare_and_process(
        self,
        request: pb2.HttpRequestEvaluation,
        timeout: Timeout,
    ) -> EgressResult:
        domain_request = _request_from_proto(request)
        values = _mapping_from_proto(request.config)
        processor = self._policy.processor_for(
            values,
            timeout=timeout,
        )
        if self._require_agent_attestation:
            return AttestedEgressProcessor(
                processor,
                create_provider_adapter_registry(),
                self._receipt_authority,
                middleware_name=request.middleware_name,
                harness_version=PI_HARNESS_VERSION,
            ).process(
                domain_request,
                agent_attestation=request.agent_attestation,
                timeout=timeout,
            )
        if any(
            header.name.lower() == RECEIPT_HEADER for header in domain_request.headers
        ):
            return EgressResult(
                decision=EgressDecision.DENY,
                decision_source=GateDecisionSource(
                    kind=DecisionSourceKind.GATE,
                    gate_name="reserved-receipt-header",
                    gate_type="reserved-receipt-header",
                ),
                reason_code="reserved_header_present",
                policy_fingerprint=processor.policy_fingerprint,
            )
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
        future.add_done_callback(self._worker_finished)
        return await asyncio.shield(future)

    def _worker_finished(self, future: asyncio.Future[object]) -> None:
        self._processing_slots.release()
        if not future.cancelled():
            future.exception()


class _ActivePolicy:
    """Own one active validated policy and its prepared immutable gates."""

    def __init__(self, registry: GateRegistry) -> None:
        self._registry = registry
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
            processor = self._registry.prepare_processor(config, timeout=timeout)
            timeout.raise_if_expired()

            self._config = config
            self._processor = processor
            return processor
        except (GateConfigurationError, GateRegistryError):
            raise EgressGateError(ErrorCode.CONFIG_INVALID) from None
        finally:
            self._lock.release()

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
    event: str
    request_id: str
    duration_ms: float
    action: str
    reason_code: str | None
    finding_count: int
    decision_source_kind: str
    error_code: str | None


def _evaluation_log_extra(
    *,
    request_id: str,
    started: float,
    action: str,
    reason_code: str | None,
    finding_count: int,
    source_kind: str,
    failure: EgressGateError | None,
) -> _EvaluationLogExtra:
    return {
        "event": "egress_gate_evaluation",
        "request_id": request_id,
        "duration_ms": round((time.monotonic() - started) * 1000, 3),
        "action": action,
        "reason_code": reason_code,
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
        or len(request.agent_attestation) > MAX_AGENT_ATTESTATION_BYTES
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


def _result_to_proto(
    result: EgressResult,
) -> tuple[pb2.HttpRequestResult, str]:
    response = _serialize_result(result)
    if (
        response.reason_code == LIMIT_REASON_CODE
        and result.reason_code != LIMIT_REASON_CODE
    ):
        return response, DecisionSourceKind.RUNTIME_LIMIT.value
    return response, result.decision_source.kind.value


def _serialize_result(result: EgressResult) -> pb2.HttpRequestResult:
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
            if result.request_mutations.replacement_body is not None:
                if len(result.request_mutations.replacement_body) > MAX_BODY_BYTES:
                    return _limit_deny()
                response.body = result.request_mutations.replacement_body
                response.has_body = True
            for mutation in result.request_mutations.header_mutations:
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
