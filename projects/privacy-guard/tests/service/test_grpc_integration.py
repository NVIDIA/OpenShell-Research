"""Real loopback coverage for the generated OpenShell gRPC service."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

import grpc
import pytest
from google.protobuf import empty_pb2, json_format, message_factory
from google.protobuf.message import Message
from pydantic import field_validator

from privacy_guard.base import StrictDomainModel
from privacy_guard.bindings import supervisor_middleware_pb2 as pb2
from privacy_guard.bindings import supervisor_middleware_pb2_grpc as pb2_grpc
from privacy_guard.engines import (
    EngineConfig,
    EntityProcessingEngine,
    EntityProcessingStrategy,
    TextProcessingResult,
)
from privacy_guard.engines.registry import EngineRegistry, create_builtin_registry
from privacy_guard.errors import PrivacyGuardError
from privacy_guard.service.servicer import PrivacyGuardMiddleware
from privacy_guard.timeout import Timeout


def _config(*, action: str = "replace") -> pb2.ValidateConfigRequest:
    request = pb2.ValidateConfigRequest()
    json_format.ParseDict(
        {
            "entity_processing": {
                "stages": [
                    {
                        "name": "identifiers",
                        "config": {
                            "engine": "regex",
                            "pattern_catalog": {
                                "entities": [
                                    {
                                        "name": "email",
                                        "patterns": [
                                            {
                                                "pattern": (r"[a-z]+@[a-z]+\.[a-z]+"),
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
                        },
                    }
                ]
            },
            "on_detection": {"action": action},
        },
        request.config,
    )
    return request


def _config_with_stages(stage_count: int) -> pb2.ValidateConfigRequest:
    values = json_format.MessageToDict(_config(action="detect").config)
    stage = values["entity_processing"]["stages"][0]
    stage.pop("name")
    values["entity_processing"]["stages"] = [stage] * stage_count
    request = pb2.ValidateConfigRequest()
    json_format.ParseDict(values, request.config)
    return request


def _evaluation(
    body: bytes,
    *,
    action: str = "replace",
    phase: pb2.SupervisorMiddlewarePhase = (
        pb2.SUPERVISOR_MIDDLEWARE_PHASE_PRE_CREDENTIALS
    ),
) -> pb2.HttpRequestEvaluation:
    return pb2.HttpRequestEvaluation(
        phase=phase,
        context=pb2.RequestContext(request_id="grpc-integration"),
        config=_config(action=action).config,
        body=body,
    )


@asynccontextmanager
async def _running_stub(
    middleware: PrivacyGuardMiddleware,
) -> AsyncIterator[pb2_grpc.SupervisorMiddlewareStub]:
    server = grpc.aio.server()
    pb2_grpc.add_SupervisorMiddlewareServicer_to_server(middleware, server)
    port = server.add_insecure_port("127.0.0.1:0")
    assert port > 0
    await server.start()
    channel = grpc.aio.insecure_channel(f"127.0.0.1:{port}")
    try:
        yield pb2_grpc.SupervisorMiddlewareStub(channel)
    finally:
        await channel.close()
        await server.stop(grace=0)
        await middleware.close()


@pytest.mark.asyncio
async def test_generated_stub_round_trip_covers_manifest_config_and_actions() -> None:
    middleware = PrivacyGuardMiddleware(create_builtin_registry())
    async with _running_stub(middleware) as stub:
        empty_message_type = message_factory.GetMessageClass(
            empty_pb2.DESCRIPTOR.message_types_by_name["Empty"]
        )
        empty_message: Message = empty_message_type()
        manifest = await stub.Describe(empty_message)
        valid = await stub.ValidateConfig(_config())
        invalid = await stub.ValidateConfig(pb2.ValidateConfigRequest())
        detected = await stub.EvaluateHttpRequest(
            _evaluation(b"contact a@b.com", action="detect")
        )
        replaced = await stub.EvaluateHttpRequest(_evaluation(b"contact a@b.com"))
        blocked = await stub.EvaluateHttpRequest(
            _evaluation(b"contact a@b.com", action="block")
        )
        clean = await stub.EvaluateHttpRequest(_evaluation(b"no match", action="block"))

    assert manifest.name == "privacy-guard"
    assert len(manifest.bindings) == 1
    assert valid.valid is True
    assert invalid.valid is False
    assert "config_invalid" in invalid.reason
    assert detected.decision == pb2.DECISION_ALLOW
    assert detected.has_body is False
    assert len(detected.findings) == 1
    assert replaced.decision == pb2.DECISION_ALLOW
    assert replaced.has_body is True
    assert replaced.body == b"contact [email]"
    assert blocked.decision == pb2.DECISION_DENY
    assert blocked.reason_code == "privacy_guard_blocked"
    assert clean.decision == pb2.DECISION_ALLOW


@pytest.mark.asyncio
async def test_generated_stub_maps_invalid_and_internal_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    middleware = PrivacyGuardMiddleware(create_builtin_registry())
    async with _running_stub(middleware) as stub:
        with pytest.raises(grpc.aio.AioRpcError) as invalid:
            await stub.EvaluateHttpRequest(
                _evaluation(
                    b"body",
                    phase=pb2.SUPERVISOR_MIDDLEWARE_PHASE_UNSPECIFIED,
                )
            )
        assert invalid.value.code() is grpc.StatusCode.INVALID_ARGUMENT
        assert "request_phase_invalid" in (invalid.value.details() or "")

        def fail_unexpectedly(values: object, body: bytes) -> None:
            del values, body
            raise RuntimeError

        monkeypatch.setattr(middleware, "_prepare_and_process", fail_unexpectedly)
        with pytest.raises(grpc.aio.AioRpcError) as internal:
            await stub.EvaluateHttpRequest(_evaluation(b"body"))
        assert internal.value.code() is grpc.StatusCode.INTERNAL
        assert "unexpected_service_failure" in (internal.value.details() or "")


@pytest.mark.asyncio
async def test_generated_stub_enforces_ten_stage_limit() -> None:
    middleware = PrivacyGuardMiddleware(create_builtin_registry())
    async with _running_stub(middleware) as stub:
        exact_config = _config_with_stages(10)
        oversized_config = _config_with_stages(11)
        exact_validation = await stub.ValidateConfig(exact_config)
        oversized_validation = await stub.ValidateConfig(oversized_config)
        exact_evaluation = _evaluation(b"no match", action="detect")
        exact_evaluation.config.CopyFrom(exact_config.config)
        exact_result = await stub.EvaluateHttpRequest(exact_evaluation)
        oversized_evaluation = _evaluation(b"no match", action="detect")
        oversized_evaluation.config.CopyFrom(oversized_config.config)
        with pytest.raises(grpc.aio.AioRpcError) as oversized_result:
            await stub.EvaluateHttpRequest(oversized_evaluation)

    assert exact_validation.valid is True
    assert oversized_validation.valid is False
    assert exact_result.decision == pb2.DECISION_ALLOW
    assert oversized_result.value.code() is grpc.StatusCode.INVALID_ARGUMENT
    assert "config_invalid" in (oversized_result.value.details() or "")


class _NumericNestedConfig(StrictDomainModel):
    count: int


class _NumericEngineConfig(EngineConfig):
    engine: Literal["numeric"] = "numeric"
    threshold: int
    ratio: float
    nested: _NumericNestedConfig
    values: tuple[int, ...]

    @field_validator("values", mode="before")
    @classmethod
    def _values_are_a_tuple(cls, value: object) -> object:
        if not isinstance(value, list | tuple):
            raise ValueError("values must be a list")
        return tuple(value)


class _NumericEngine(EntityProcessingEngine[_NumericEngineConfig]):
    supported_strategies = frozenset({EntityProcessingStrategy.DETECT})

    def _run(
        self,
        text: str,
        *,
        strategy: EntityProcessingStrategy,
        timeout: Timeout,
    ) -> TextProcessingResult:
        del strategy, timeout
        return TextProcessingResult(text=text, detections=())


def _numeric_values(
    threshold: int | float,
    *,
    ratio: float = 3.0,
) -> dict[str, object]:
    return {
        "entity_processing": {
            "stages": [
                {
                    "config": {
                        "engine": "numeric",
                        "threshold": threshold,
                        "ratio": ratio,
                        "nested": {"count": 4},
                        "values": [5, 6],
                    }
                }
            ]
        },
        "on_detection": {"action": "detect"},
    }


def _numeric_request(
    threshold: int | float,
    *,
    ratio: float = 3.0,
) -> pb2.ValidateConfigRequest:
    request = pb2.ValidateConfigRequest()
    json_format.ParseDict(_numeric_values(threshold, ratio=ratio), request.config)
    return request


def _numeric_registry() -> EngineRegistry:
    registry = EngineRegistry()
    registry.register(_NumericEngine)
    return registry.finalize()


@pytest.mark.asyncio
async def test_generated_stub_normalizes_transport_safe_integral_numbers() -> None:
    registry = _numeric_registry()
    with pytest.raises(PrivacyGuardError):
        registry.validate_config(_numeric_values(3.0))

    middleware = PrivacyGuardMiddleware(registry)
    async with _running_stub(middleware) as stub:
        ordinary = await stub.ValidateConfig(_numeric_request(3, ratio=3.5))
        safe_max = await stub.ValidateConfig(_numeric_request((1 << 53) - 1))
        safe_min = await stub.ValidateConfig(_numeric_request(-((1 << 53) - 1)))
        non_integral = await stub.ValidateConfig(_numeric_request(3.5))
        beyond_safe = await stub.ValidateConfig(_numeric_request(1 << 53))
        evaluation = pb2.HttpRequestEvaluation(
            phase=pb2.SUPERVISOR_MIDDLEWARE_PHASE_PRE_CREDENTIALS,
            config=_numeric_request(3).config,
            body=b"body",
        )
        result = await stub.EvaluateHttpRequest(evaluation)

    assert ordinary.valid is True
    assert safe_max.valid is True
    assert safe_min.valid is True
    assert non_integral.valid is False
    assert beyond_safe.valid is False
    assert result.decision == pb2.DECISION_ALLOW
