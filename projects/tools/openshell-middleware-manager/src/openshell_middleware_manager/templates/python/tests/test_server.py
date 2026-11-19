# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable

import grpc
import pytest

from __PACKAGE_NAME__.bindings import extension_pb2
from __PACKAGE_NAME__.bindings import supervisor_middleware_pb2 as pb2
from __PACKAGE_NAME__.bindings import supervisor_middleware_pb2_grpc as pb2_grpc
from __PACKAGE_NAME__.server import (
    CONTRACT_CAPABILITY,
    HTTP_HOOKS_CAPABILITY,
    MAX_MESSAGE_BYTES,
    MAX_PAYLOAD_BYTES,
    SERVICE_NAME,
    build_manifest,
    create_server,
    evaluate_http_request_body,
    evaluate_http_request_event,
    evaluate_http_request_preflight,
    find_protocol_mismatch,
    validate_config,
)


def caller_metadata(
    *,
    major: int = 1,
    supported: tuple[str, ...] = (CONTRACT_CAPABILITY, HTTP_HOOKS_CAPABILITY),
    required: tuple[str, ...] = (CONTRACT_CAPABILITY,),
) -> pb2.MiddlewareDescribeRequest:
    return pb2.MiddlewareDescribeRequest(
        gateway=extension_pb2.PeerMetadata(
            protocol_version=extension_pb2.ProtocolVersion(major=major, minor=0),
            implementation_name="openshell/gateway",
            implementation_version="test",
            supported_capabilities=supported,
            required_capabilities=required,
        )
    )


def run_with_server(exercise: Callable[[grpc.aio.Channel], Awaitable[None]]) -> None:
    async def run() -> None:
        server = create_server()
        port = server.add_insecure_port("127.0.0.1:0")
        assert port != 0
        await server.start()
        options = (
            ("grpc.max_send_message_length", MAX_MESSAGE_BYTES),
            ("grpc.max_receive_message_length", MAX_MESSAGE_BYTES),
        )
        try:
            async with grpc.aio.insecure_channel(f"127.0.0.1:{port}", options=options) as channel:
                await exercise(channel)
        finally:
            await server.stop(grace=0)

    asyncio.run(run())


def test_manifest_advertises_pre_credentials_http_hooks() -> None:
    manifest = build_manifest()

    assert manifest.name == SERVICE_NAME
    assert len(manifest.bindings) == 1
    assert manifest.bindings[0].operation == pb2.SUPERVISOR_MIDDLEWARE_OPERATION_HTTP_REQUEST
    assert manifest.bindings[0].phase == pb2.SUPERVISOR_MIDDLEWARE_PHASE_PRE_CREDENTIALS
    assert manifest.bindings[0].max_payload_bytes == MAX_PAYLOAD_BYTES
    assert manifest.extension.protocol_version.major == 1
    assert manifest.extension.protocol_version.minor == 0
    assert manifest.extension.implementation_name == SERVICE_NAME
    assert CONTRACT_CAPABILITY in manifest.extension.supported_capabilities
    assert HTTP_HOOKS_CAPABILITY in manifest.extension.supported_capabilities
    assert HTTP_HOOKS_CAPABILITY in manifest.extension.required_capabilities


def test_compatible_caller_has_no_protocol_mismatch() -> None:
    assert find_protocol_mismatch(caller_metadata()) is None


@pytest.mark.parametrize(
    ("request_message", "reason"),
    [
        (pb2.MiddlewareDescribeRequest(), "did not provide protocol metadata"),
        (caller_metadata(major=2), "unsupported protocol major 2"),
        (caller_metadata(supported=(CONTRACT_CAPABILITY,)), HTTP_HOOKS_CAPABILITY),
        (caller_metadata(required=("example.unknown",)), "example.unknown"),
    ],
)
def test_incompatible_caller_is_explained(
    request_message: pb2.MiddlewareDescribeRequest, reason: str
) -> None:
    mismatch = find_protocol_mismatch(request_message)

    assert mismatch is not None
    assert reason in mismatch


def test_default_config_is_valid() -> None:
    response = validate_config(pb2.ValidateConfigRequest())

    assert response.valid is True
    assert response.reason == ""


def test_valid_request_continues_without_mutation() -> None:
    result = evaluate_http_request_preflight(
        pb2.HttpRequestHookPreflight(
            phase=pb2.SUPERVISOR_MIDDLEWARE_PHASE_PRE_CREDENTIALS,
        )
    )

    assert result.WhichOneof("result") == "preflight_result"
    assert result.preflight_result.WhichOneof("decision") == "continue_without_body"
    assert result.preflight_result.header_mutations == []


def test_unsupported_phase_is_denied() -> None:
    result = evaluate_http_request_preflight(pb2.HttpRequestHookPreflight())

    assert result.WhichOneof("result") == "deny"
    assert result.deny.diagnostics.reason_code == "unsupported_phase"


def test_whole_body_is_kept_unchanged() -> None:
    result = evaluate_http_request_body(pb2.HttpWholeBody(data=b"request body"))

    assert result.WhichOneof("result") == "whole_body_result"
    assert result.whole_body_result.WhichOneof("body") == "unchanged"


def test_events_without_results_are_ignored() -> None:
    assert evaluate_http_request_event(pb2.HttpRequestHookEvent(begin=pb2.HttpBegin())) is None
    assert (
        evaluate_http_request_event(
            pb2.HttpRequestHookEvent(session_end=pb2.MiddlewareSessionEnd())
        )
        is None
    )


def test_describe_negotiates_with_the_caller() -> None:
    async def exercise(channel: grpc.aio.Channel) -> None:
        stub = pb2_grpc.SupervisorMiddlewareStub(channel)

        manifest = await stub.Describe(caller_metadata())
        assert manifest.name == SERVICE_NAME

        with pytest.raises(grpc.aio.AioRpcError) as error:
            await stub.Describe(pb2.MiddlewareDescribeRequest())
        assert error.value.code() == grpc.StatusCode.FAILED_PRECONDITION

    run_with_server(exercise)


def test_hook_stream_answers_preflight_and_maximum_body() -> None:
    async def events() -> AsyncIterator[pb2.HttpRequestHookEvent]:
        yield pb2.HttpRequestHookEvent(
            preflight=pb2.HttpRequestHookPreflight(
                phase=pb2.SUPERVISOR_MIDDLEWARE_PHASE_PRE_CREDENTIALS,
                context=pb2.RequestContext(
                    request_id="max-body-request",
                    sandbox_id="max-body-sandbox",
                ),
                middleware_name=SERVICE_NAME,
                request=pb2.HttpRequestPreflightHead(
                    headers=[pb2.HttpHeader(name="content-type", value="application/octet-stream")],
                ),
            )
        )
        yield pb2.HttpRequestHookEvent(begin=pb2.HttpBegin())
        yield pb2.HttpRequestHookEvent(whole_body=pb2.HttpWholeBody(data=b"x" * MAX_PAYLOAD_BYTES))
        yield pb2.HttpRequestHookEvent(session_end=pb2.MiddlewareSessionEnd())

    async def exercise(channel: grpc.aio.Channel) -> None:
        stub = pb2_grpc.HttpHooksStub(channel)
        results = [result async for result in stub.EvaluateHttpRequestHook(events())]

        assert [result.WhichOneof("result") for result in results] == [
            "preflight_result",
            "whole_body_result",
        ]

    run_with_server(exercise)
