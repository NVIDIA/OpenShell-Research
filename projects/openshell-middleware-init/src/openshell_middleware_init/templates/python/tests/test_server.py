import asyncio

import grpc

from __PACKAGE_NAME__.bindings import supervisor_middleware_pb2 as pb2
from __PACKAGE_NAME__.bindings import supervisor_middleware_pb2_grpc as pb2_grpc
from __PACKAGE_NAME__.server import (
    MAX_BODY_BYTES,
    MAX_MESSAGE_BYTES,
    SERVICE_NAME,
    build_manifest,
    create_server,
    evaluate_http_request,
    validate_config,
)


def test_manifest_advertises_pre_credentials_http() -> None:
    manifest = build_manifest()

    assert manifest.name == SERVICE_NAME
    assert len(manifest.bindings) == 1
    assert manifest.bindings[0].operation == pb2.SUPERVISOR_MIDDLEWARE_OPERATION_HTTP_REQUEST
    assert manifest.bindings[0].phase == pb2.SUPERVISOR_MIDDLEWARE_PHASE_PRE_CREDENTIALS
    assert manifest.bindings[0].max_body_bytes == MAX_BODY_BYTES


def test_default_config_is_valid() -> None:
    response = validate_config(pb2.ValidateConfigRequest())

    assert response.valid is True
    assert response.reason == ""


def test_valid_request_is_allowed_without_mutation() -> None:
    response = evaluate_http_request(
        pb2.HttpRequestEvaluation(
            phase=pb2.SUPERVISOR_MIDDLEWARE_PHASE_PRE_CREDENTIALS,
        )
    )

    assert response.decision == pb2.DECISION_ALLOW
    assert response.has_body is False
    assert response.header_mutations == []


def test_unsupported_phase_is_denied() -> None:
    response = evaluate_http_request(pb2.HttpRequestEvaluation())

    assert response.decision == pb2.DECISION_DENY
    assert response.reason_code == "unsupported_phase"


def test_transport_accepts_maximum_body_inside_full_envelope() -> None:
    async def exercise() -> None:
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
                stub = pb2_grpc.SupervisorMiddlewareStub(channel)
                response = await stub.EvaluateHttpRequest(
                    pb2.HttpRequestEvaluation(
                        phase=pb2.SUPERVISOR_MIDDLEWARE_PHASE_PRE_CREDENTIALS,
                        context=pb2.RequestContext(
                            request_id="max-body-request",
                            sandbox_id="max-body-sandbox",
                        ),
                        headers=[
                            pb2.HttpHeader(name="content-type", value="application/octet-stream")
                        ],
                        body=b"x" * MAX_BODY_BYTES,
                        middleware_name=SERVICE_NAME,
                    )
                )
        finally:
            await server.stop(grace=0)
        assert response.decision == pb2.DECISION_ALLOW

    asyncio.run(exercise())
