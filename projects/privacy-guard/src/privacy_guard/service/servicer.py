"""Thin gRPC adapter for the proto-free Privacy Guard processor."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Never, Protocol, TypedDict

import grpc
from google.protobuf import json_format
from google.protobuf.message import Message
from pydantic import ValidationError
from typing_extensions import override

from privacy_guard.bindings import supervisor_middleware_pb2 as pb2
from privacy_guard.bindings import supervisor_middleware_pb2_grpc as pb2_grpc
from privacy_guard.config import PolicyConfig
from privacy_guard.constants import (
    BLOCK_REASON,
    BLOCK_REASON_CODE,
    LIMIT_REASON,
    LIMIT_REASON_CODE,
    MAX_BODY_BYTES,
    MAX_CONCURRENT_SCANS,
    MAX_PROTO_FINDING_BYTES,
    MAX_PROTO_FINDING_GROUPS,
    PATTERN_NAME_METADATA_KEY,
    REASON_CODE_PATTERN,
    SERVICE_NAME,
    SERVICE_VERSION,
    UINT32_MAX,
)
from privacy_guard.errors import ErrorCode, ErrorKind, PrivacyGuardError
from privacy_guard.payloads import (
    InterceptedRequest,
    ProcessingDecision,
    ProcessingResult,
)
from privacy_guard.scanners import RequestBodyFinding


class RequestProcessorLike(Protocol):
    """Narrow structural seam for injecting request processors into the service."""

    def validate_policy_config(self, policy_config: PolicyConfig) -> None: ...

    def process(self, request: InterceptedRequest) -> ProcessingResult: ...


class PrivacyGuardMiddleware(pb2_grpc.SupervisorMiddlewareServicer):
    """Translate protobuf requests and responses at the transport boundary."""

    def __init__(self, processor: RequestProcessorLike) -> None:
        self._processor = processor
        self._scan_slots = asyncio.Semaphore(MAX_CONCURRENT_SCANS)
        self._scan_executor = ThreadPoolExecutor(
            max_workers=MAX_CONCURRENT_SCANS,
            thread_name_prefix="privacy-guard-scan",
        )

    async def close(self) -> None:
        """Stop accepting worker jobs and wait for active synchronous scans."""
        await asyncio.to_thread(
            self._scan_executor.shutdown, wait=True, cancel_futures=True
        )

    @override
    async def Describe(
        self,
        request: object,
        context: grpc.aio.ServicerContext[object, pb2.MiddlewareManifest],
    ) -> pb2.MiddlewareManifest:
        """Advertise the HTTP pre-credentials binding and maximum body size."""
        return self._describe()

    @override
    async def ValidateConfig(
        self,
        request: pb2.ValidateConfigRequest,
        context: grpc.aio.ServicerContext[
            pb2.ValidateConfigRequest, pb2.ValidateConfigResponse
        ],
    ) -> pb2.ValidateConfigResponse:
        """Parse and validate policy configuration without processing a body."""
        return self._validate_config(request)

    @override
    async def EvaluateHttpRequest(
        self,
        request: pb2.HttpRequestEvaluation,
        context: grpc.aio.ServicerContext[
            pb2.HttpRequestEvaluation, pb2.HttpRequestResult
        ],
    ) -> pb2.HttpRequestResult:
        """Validate transport input, delegate valid requests, and map the result."""
        return await self._evaluate_rpc(request, context)

    def _describe(self) -> pb2.MiddlewareManifest:
        """Build the transport manifest without requiring an RPC context."""
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
        self, request: pb2.ValidateConfigRequest
    ) -> pb2.ValidateConfigResponse:
        """Validate config through the narrow processor seam."""
        try:
            policy_config = _policy_from_proto(request.config)
            self._processor.validate_policy_config(policy_config)
        except PrivacyGuardError as error:
            return pb2.ValidateConfigResponse(valid=False, reason=str(error))
        except Exception:
            error = PrivacyGuardError(ErrorCode.UNEXPECTED_SERVICE_FAILURE)
            return pb2.ValidateConfigResponse(valid=False, reason=str(error))
        return pb2.ValidateConfigResponse(valid=True)

    async def _evaluate_rpc(
        self, request: pb2.HttpRequestEvaluation, context: _AbortContext
    ) -> pb2.HttpRequestResult:
        """Run one evaluation using only the context's abort capability."""
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
                "privacy_guard_evaluation",
                extra=log_extra,
            )

        status = (
            grpc.StatusCode.INVALID_ARGUMENT
            if failure.kind is ErrorKind.INVALID_INPUT
            else grpc.StatusCode.INTERNAL
        )
        await context.abort(status, str(failure))

    async def _evaluate_http_request(
        self, request: pb2.HttpRequestEvaluation
    ) -> pb2.HttpRequestResult:
        """Evaluate a request through the context-free application seam."""
        if request.phase != pb2.SUPERVISOR_MIDDLEWARE_PHASE_PRE_CREDENTIALS:
            raise PrivacyGuardError(ErrorCode.REQUEST_PHASE_INVALID)
        if len(request.body) > MAX_BODY_BYTES:
            raise PrivacyGuardError(ErrorCode.REQUEST_BODY_TOO_LARGE)

        intercepted = _request_from_proto(request)
        # Cancellation cannot stop a Python worker already running. The done
        # callback keeps its slot occupied until it really finishes, while the
        # RPC can still observe cancellation immediately.
        await self._scan_slots.acquire()
        try:
            future = asyncio.get_running_loop().run_in_executor(
                self._scan_executor, self._processor.process, intercepted
            )
        except BaseException:
            self._scan_slots.release()
            raise
        future.add_done_callback(lambda _: self._scan_slots.release())
        result = await asyncio.shield(future)
        return _result_to_proto(result)


_LOGGER = logging.getLogger(__name__)


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
    """Build the typed operational fields attached to an evaluation log."""
    return {
        "request_id": request_id,
        "duration_ms": round((time.monotonic() - started) * 1000, 3),
        "action": action,
        "finding_count": finding_count,
        "error_code": failure.code.value if failure is not None else None,
    }


def _policy_from_proto(config: Message) -> PolicyConfig:
    try:
        values = json_format.MessageToDict(config)
        return PolicyConfig.from_mapping(values)
    except Exception:
        raise PrivacyGuardError(ErrorCode.CONFIG_INVALID) from None


def _request_from_proto(request: pb2.HttpRequestEvaluation) -> InterceptedRequest:
    policy_config = _policy_from_proto(request.config)
    try:
        return InterceptedRequest(
            raw_body=request.body,
            content_type=next(
                (
                    header.value
                    for header in request.headers
                    if header.name == "content-type"
                ),
                "",
            ),
            request_id=request.context.request_id,
            policy_config=policy_config,
        )
    except ValidationError:
        raise PrivacyGuardError(ErrorCode.UNEXPECTED_SERVICE_FAILURE) from None


def _result_to_proto(result: ProcessingResult) -> pb2.HttpRequestResult:
    if (
        result.replacement_body is not None
        and len(result.replacement_body) > MAX_BODY_BYTES
    ):
        return _limit_deny()
    try:
        findings = _aggregate_findings(result.findings)
    except PrivacyGuardError as error:
        if error.code is ErrorCode.RESULT_LIMIT_EXCEEDED:
            return _limit_deny()
        raise
    if result.decision is ProcessingDecision.ALLOW:
        has_body = result.replacement_body is not None
        return pb2.HttpRequestResult(
            decision=pb2.DECISION_ALLOW,
            body=result.replacement_body if has_body else b"",
            has_body=has_body,
            findings=findings,
        )
    if result.decision is ProcessingDecision.DENY:
        reason_code = result.reason_code or BLOCK_REASON_CODE
        if REASON_CODE_PATTERN.fullmatch(reason_code) is None:
            return _limit_deny()
        return pb2.HttpRequestResult(
            decision=pb2.DECISION_DENY,
            reason=LIMIT_REASON if reason_code == LIMIT_REASON_CODE else BLOCK_REASON,
            reason_code=reason_code,
            has_body=False,
            findings=findings,
        )
    raise PrivacyGuardError(ErrorCode.UNEXPECTED_SERVICE_FAILURE)


def _limit_deny() -> pb2.HttpRequestResult:
    return pb2.HttpRequestResult(
        decision=pb2.DECISION_DENY,
        reason=LIMIT_REASON,
        reason_code=LIMIT_REASON_CODE,
        has_body=False,
    )


def _aggregate_findings(findings: tuple[RequestBodyFinding, ...]) -> list[pb2.Finding]:
    groups: OrderedDict[tuple[str, str, str | None, str], int] = OrderedDict()
    for request_body_finding in findings:
        finding = request_body_finding.finding
        pattern_name = (
            None
            if finding.metadata is None
            else finding.metadata.get(PATTERN_NAME_METADATA_KEY)
        )
        key = (
            finding.scanner_name,
            finding.entity,
            pattern_name,
            finding.confidence.value,
        )
        groups[key] = groups.get(key, 0) + 1
    if len(groups) > MAX_PROTO_FINDING_GROUPS:
        raise PrivacyGuardError(ErrorCode.RESULT_LIMIT_EXCEEDED)
    aggregated = [
        pb2.Finding(
            type=scanner_name,
            label=entity if pattern_name is None else f"{entity}/{pattern_name}",
            confidence=confidence,
            count=min(count, UINT32_MAX),
        )
        for (scanner_name, entity, pattern_name, confidence), count in groups.items()
    ]
    if any(finding.ByteSize() > MAX_PROTO_FINDING_BYTES for finding in aggregated):
        raise PrivacyGuardError(ErrorCode.RESULT_LIMIT_EXCEEDED)
    return aggregated


__all__ = ["PrivacyGuardMiddleware", "RequestProcessorLike"]
