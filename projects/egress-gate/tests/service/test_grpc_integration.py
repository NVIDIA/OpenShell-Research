# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Loopback coverage for the generated OpenShell gRPC service."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import grpc
import pytest
from google.protobuf import empty_pb2, json_format, message_factory
from google.protobuf.message import Message

from egress_gate.admission import (
    PiMessageV1,
    canonical_json_bytes,
)
from egress_gate.bindings import supervisor_middleware_pb2 as pb2
from egress_gate.bindings import supervisor_middleware_pb2_grpc as pb2_grpc
from egress_gate.errors import EgressGateError, ErrorCode
from egress_gate.gates import create_builtin_registry
from egress_gate.service import server as server_module
from egress_gate.service.servicer import EgressGateMiddleware


def _config(*, action_kind: str = "replace") -> Message:
    action: dict[str, object] = {"kind": action_kind}
    if action_kind == "replace":
        action["template"] = "[{entity}]"
    values: dict[str, object] = {
        "gates": [
            {
                "name": "identifiers",
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
            }
        ],
        "default_decision": "allow",
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


def _progressive_redaction_config() -> Message:
    values = {
        "gates": [
            {
                "name": name,
                "kind": "regex",
                "scan": {
                    "kind": "body",
                    "action": {"kind": "replace", "template": "[{entity}]"},
                },
                "pattern_catalog": {
                    "entities": [
                        {
                            "name": entity,
                            "rules": [{"pattern": pattern, "confidence": "high"}],
                        }
                    ]
                },
            }
            for name, entity, pattern in (
                ("redact-email", "email", r"alice@example\.com"),
                ("redact-api-key", "api_key", r"sk-[0-9]+"),
                ("redact-phone", "phone", r"555-[0-9]{4}"),
            )
        ],
        "default_decision": "allow",
    }
    request = pb2.ValidateConfigRequest()
    json_format.ParseDict(values, request.config)
    return request.config


@asynccontextmanager
async def _running_stub(
    middleware: EgressGateMiddleware,
) -> AsyncIterator[tuple[pb2_grpc.SupervisorMiddlewareStub, grpc.aio.Channel]]:
    server = server_module._create_grpc_server(middleware)
    port = server.add_insecure_port("127.0.0.1:0")
    assert port > 0
    await server.start()
    channel = grpc.aio.insecure_channel(f"127.0.0.1:{port}")
    try:
        yield pb2_grpc.SupervisorMiddlewareStub(channel), channel
    finally:
        await channel.close()
        await server.stop(grace=0)
        await middleware.close()


@pytest.mark.asyncio
async def test_generated_stub_round_trip_covers_manifest_and_gate_actions() -> None:
    middleware = EgressGateMiddleware(create_builtin_registry())
    async with _running_stub(middleware) as (stub, _):
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
async def test_generated_stub_issues_a_user_message_attestation() -> None:
    body = canonical_json_bytes(
        PiMessageV1(
            schema_version="openshell.pi-message.v1", origin="user", text="safe"
        )
    )
    request = pb2.AgentConversationEvaluation(
        phase=pb2.SUPERVISOR_MIDDLEWARE_PHASE_AGENT_CONTEXT,
        context=pb2.RequestContext(request_id="admission-1", sandbox_id="sandbox"),
        config=_config(action_kind="detect"),
        target=pb2.AgentConversationTarget(
            harness="pi",
            harness_version="sdk-v1",
            hook="user_message",
            schema_version="openshell.pi-message.v1",
            scheme="https",
            host="provider.invalid",
            port=443,
            path="/v1/chat/completions",
        ),
        middleware_name="pi-egress",
        session_id="session-1",
        turn_id="submission-1",
        request_body=body,
    )
    middleware = EgressGateMiddleware(
        create_builtin_registry(), require_agent_attestation=True
    )
    async with _running_stub(middleware) as (stub, _):
        response = await stub.EvaluateAgentConversation(request)

    assert response.decision == pb2.DECISION_ALLOW
    assert response.attestation.startswith(b"ag1.")
    assert response.has_replacement_body is False
    assert response.metadata["admission_schema"] == "openshell.pi-message.v1"


@pytest.mark.asyncio
async def test_agent_admission_is_unavailable_when_managed_mode_is_off() -> None:
    request = pb2.AgentConversationEvaluation(
        phase=pb2.SUPERVISOR_MIDDLEWARE_PHASE_AGENT_CONTEXT
    )
    middleware = EgressGateMiddleware(create_builtin_registry())
    async with _running_stub(middleware) as (stub, _):
        response = await stub.EvaluateAgentConversation(request)

    assert response.decision == pb2.DECISION_DENY
    assert response.reason_code == "admission_unavailable"


@pytest.mark.asyncio
async def test_trusted_agent_attestation_is_verified_for_http_egress() -> None:
    pi_body = canonical_json_bytes(
        PiMessageV1(
            schema_version="openshell.pi-message.v1", origin="user", text="safe"
        )
    )
    admission = pb2.AgentConversationEvaluation(
        phase=pb2.SUPERVISOR_MIDDLEWARE_PHASE_AGENT_CONTEXT,
        context=pb2.RequestContext(request_id="admission-2", sandbox_id="sandbox"),
        config=_config(action_kind="detect"),
        target=pb2.AgentConversationTarget(
            harness="pi",
            harness_version="sdk-v1",
            hook="user_message",
            schema_version="openshell.pi-message.v1",
            scheme="https",
            host="provider.invalid",
            port=443,
            path="/v1/chat/completions",
        ),
        middleware_name="pi-egress",
        session_id="session-1",
        turn_id="submission-2",
        request_body=pi_body,
    )
    provider_body = json.dumps(
        {
            "model": "fixture-model",
            "messages": [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "safe"},
            ],
            "temperature": 0,
            "max_completion_tokens": 128,
            "tool_choice": "auto",
            "stream": True,
            "stream_options": {"include_usage": True},
            "store": False,
            "prompt_cache_key": "session-1",
        },
        separators=(",", ":"),
    ).encode()
    middleware = EgressGateMiddleware(
        create_builtin_registry(), require_agent_attestation=True
    )
    async with _running_stub(middleware) as (stub, _):
        admitted = await stub.EvaluateAgentConversation(admission)
        network = _evaluation(provider_body, action_kind="detect")
        network.context.request_id = "network-2"
        network.target.host = "provider.invalid"
        network.target.path = "/v1/chat/completions"
        network.middleware_name = "pi-egress"
        network.headers.append(
            pb2.HttpHeader(name="content-type", value="application/json")
        )
        network.agent_attestation = admitted.attestation
        allowed = await stub.EvaluateHttpRequest(network)
        missing = _evaluation(provider_body, action_kind="detect")
        missing.target.host = "provider.invalid"
        missing.target.path = "/v1/chat/completions"
        missing.middleware_name = "pi-egress"
        missing.headers.append(
            pb2.HttpHeader(name="content-type", value="application/json")
        )
        denied = await stub.EvaluateHttpRequest(missing)

    assert allowed.decision == pb2.DECISION_ALLOW
    assert not allowed.header_mutations
    assert denied.decision == pb2.DECISION_DENY
    assert denied.reason_code == "attestation_missing"


@pytest.mark.asyncio
async def test_unmanaged_http_rejects_the_reserved_header() -> None:
    middleware = EgressGateMiddleware(create_builtin_registry())
    request = _evaluation(b"safe", action_kind="detect")
    request.headers.append(
        pb2.HttpHeader(
            name="X-OpenShell-Middleware-Egress-Receipt",
            value="eg1.untrusted",
        )
    )

    async with _running_stub(middleware) as (stub, _):
        response = await stub.EvaluateHttpRequest(request)

    assert response.decision == pb2.DECISION_DENY
    assert response.reason_code == "reserved_header_present"


@pytest.mark.asyncio
async def test_generated_stub_returns_three_gate_progressive_redaction() -> None:
    middleware = EgressGateMiddleware(create_builtin_registry())
    request = _evaluation(b"email=alice@example.com api_key=sk-123456 phone=555-0100")
    request.config.CopyFrom(_progressive_redaction_config())

    async with _running_stub(middleware) as (stub, _):
        response = await stub.EvaluateHttpRequest(request)

    assert response.decision == pb2.DECISION_ALLOW
    assert response.has_body is True
    assert response.body == b"email=[email] api_key=[api_key] phone=[phone]"
    assert [finding.label for finding in response.findings] == [
        "email",
        "api_key",
        "phone",
    ]


@pytest.mark.asyncio
async def test_generated_stub_maps_invalid_phase_to_invalid_argument() -> None:
    middleware = EgressGateMiddleware(create_builtin_registry())
    async with _running_stub(middleware) as (stub, _):
        request = _evaluation(b"body")
        request.phase = pb2.SUPERVISOR_MIDDLEWARE_PHASE_UNSPECIFIED

        with pytest.raises(grpc.aio.AioRpcError) as error:
            await stub.EvaluateHttpRequest(request)

    assert error.value.code() is grpc.StatusCode.INVALID_ARGUMENT
    assert "request_phase_invalid" in (error.value.details() or "")


@pytest.mark.asyncio
async def test_malformed_protobuf_maps_to_content_safe_invalid_argument() -> None:
    middleware = EgressGateMiddleware(create_builtin_registry())
    async with _running_stub(middleware) as (stub, channel):
        raw_evaluate = channel.unary_unary(
            "/openshell.middleware.v1.SupervisorMiddleware/EvaluateHttpRequest",
            request_serializer=lambda value: value,
            response_deserializer=lambda value: value,
        )
        with pytest.raises(grpc.aio.AioRpcError) as error:
            await raw_evaluate(b"\x12\x02\x0a\xff")

        recovered = await stub.EvaluateHttpRequest(_evaluation(b"body"))

    details = error.value.details() or ""
    assert error.value.code() is grpc.StatusCode.INVALID_ARGUMENT
    assert details == str(EgressGateError(ErrorCode.REQUEST_PROTOBUF_INVALID))
    assert "DecodeError" not in details
    assert "HttpRequestEvaluation" not in details
    assert recovered.decision == pb2.DECISION_ALLOW


@pytest.mark.asyncio
async def test_generated_stub_maps_gate_failure_to_internal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    middleware = EgressGateMiddleware(create_builtin_registry())

    def fail_processing(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise EgressGateError(ErrorCode.GATE_EXECUTION_FAILED)

    monkeypatch.setattr(middleware, "_prepare_and_process", fail_processing)
    async with _running_stub(middleware) as (stub, _):
        with pytest.raises(grpc.aio.AioRpcError) as error:
            await stub.EvaluateHttpRequest(_evaluation(b"body"))

    assert error.value.code() is grpc.StatusCode.INTERNAL
    assert "gate_execution_failed" in (error.value.details() or "")
