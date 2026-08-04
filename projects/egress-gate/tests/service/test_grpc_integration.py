"""Loopback coverage for the generated OpenShell gRPC service."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import grpc
import pytest
from google.protobuf import empty_pb2, json_format, message_factory
from google.protobuf.message import Message

from egress_gate.bindings import supervisor_middleware_pb2 as pb2
from egress_gate.bindings import supervisor_middleware_pb2_grpc as pb2_grpc
from egress_gate.errors import EgressGateError, ErrorCode
from egress_gate.gates import create_builtin_registry
from egress_gate.service.servicer import EgressGateMiddleware


def _config(*, action_kind: str = "replace") -> Message:
    action: dict[str, object] = {"kind": action_kind}
    if action_kind == "replace":
        action["template"] = "[{entity}]"
    values: dict[str, object] = {
        "pipeline": {
            "gates": [
                {
                    "name": "identifiers",
                    "config": {
                        "kind": "regex",
                        "scan": {"kind": "body", "action": action},
                        "pattern_catalog": {
                            "entities": [
                                {
                                    "name": "email",
                                    "rules": [
                                        {
                                            "pattern": r"[a-z]+@[a-z]+\.[a-z]+",
                                            "confidence": "high",
                                        }
                                    ],
                                }
                            ]
                        },
                    },
                }
            ],
            "default_decision": "allow",
        }
    }
    request = pb2.ValidateConfigRequest()
    json_format.ParseDict(values, request.config)
    return request.config


def _evaluation(
    body: bytes,
    *,
    action_kind: str = "replace",
) -> pb2.HttpRequestEvaluation:
    return pb2.HttpRequestEvaluation(
        phase=pb2.SUPERVISOR_MIDDLEWARE_PHASE_PRE_CREDENTIALS,
        context=pb2.RequestContext(request_id="grpc-integration", sandbox_id="sandbox"),
        config=_config(action_kind=action_kind),
        target=pb2.HttpRequestTarget(
            scheme="https",
            host="example.com",
            port=443,
            method="POST",
            path="/",
            query="",
        ),
        body=body,
    )


@asynccontextmanager
async def _running_stub(
    middleware: EgressGateMiddleware,
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
async def test_generated_stub_round_trip_covers_manifest_and_gate_actions() -> None:
    middleware = EgressGateMiddleware(create_builtin_registry())
    async with _running_stub(middleware) as stub:
        empty_message_type = message_factory.GetMessageClass(
            empty_pb2.DESCRIPTOR.message_types_by_name["Empty"]
        )
        empty_message: Message = empty_message_type()
        manifest = await stub.Describe(empty_message)
        replaced = await stub.EvaluateHttpRequest(_evaluation(b"contact a@b.com"))
        detected = await stub.EvaluateHttpRequest(
            _evaluation(b"contact a@b.com", action_kind="detect")
        )
        denied_config = _config(action_kind="deny")
        denied_request = _evaluation(b"contact a@b.com", action_kind="deny")
        denied_request.config.CopyFrom(denied_config)
        denied = await stub.EvaluateHttpRequest(denied_request)

    assert manifest.name == "egress-gate"
    assert len(manifest.bindings) == 1
    assert replaced.decision == pb2.DECISION_ALLOW
    assert replaced.has_body is True
    assert replaced.body == b"contact [email]"
    assert detected.decision == pb2.DECISION_ALLOW
    assert detected.has_body is False
    assert len(detected.findings) == 1
    assert denied.decision == pb2.DECISION_DENY
    assert denied.reason_code == "egress_gate_regex_denied"


@pytest.mark.asyncio
async def test_generated_stub_maps_invalid_phase_to_invalid_argument() -> None:
    middleware = EgressGateMiddleware(create_builtin_registry())
    async with _running_stub(middleware) as stub:
        request = _evaluation(b"body")
        request.phase = pb2.SUPERVISOR_MIDDLEWARE_PHASE_UNSPECIFIED

        with pytest.raises(grpc.aio.AioRpcError) as error:
            await stub.EvaluateHttpRequest(request)

    assert error.value.code() is grpc.StatusCode.INVALID_ARGUMENT
    assert "request_phase_invalid" in (error.value.details() or "")


@pytest.mark.asyncio
async def test_generated_stub_maps_gate_failure_to_internal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    middleware = EgressGateMiddleware(create_builtin_registry())

    def fail_processing(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise EgressGateError(ErrorCode.GATE_EXECUTION_FAILED)

    monkeypatch.setattr(middleware, "_prepare_and_process", fail_processing)
    async with _running_stub(middleware) as stub:
        with pytest.raises(grpc.aio.AioRpcError) as error:
            await stub.EvaluateHttpRequest(_evaluation(b"body"))

    assert error.value.code() is grpc.StatusCode.INTERNAL
    assert "gate_execution_failed" in (error.value.details() or "")
