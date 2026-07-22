"""Pass-through OpenShell supervisor middleware server."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence

import grpc

from __PACKAGE_NAME__.bindings import supervisor_middleware_pb2 as pb2
from __PACKAGE_NAME__.bindings import supervisor_middleware_pb2_grpc as pb2_grpc

SERVICE_NAME = "__SERVICE_NAME__"
SERVICE_VERSION = "0.1.0"
MAX_BODY_BYTES = 4 * 1024 * 1024
MAX_MESSAGE_BYTES = MAX_BODY_BYTES + 1024 * 1024


def build_manifest() -> pb2.MiddlewareManifest:
    """Describe the operation and phase supported by this service."""
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


def validate_config(request: pb2.ValidateConfigRequest) -> pb2.ValidateConfigResponse:
    """Validate service-specific config before OpenShell admits a policy."""
    del request
    return pb2.ValidateConfigResponse(valid=True)


def evaluate_http_request(request: pb2.HttpRequestEvaluation) -> pb2.HttpRequestResult:
    """Allow a valid pre-credentials request without mutation."""
    if request.phase != pb2.SUPERVISOR_MIDDLEWARE_PHASE_PRE_CREDENTIALS:
        return pb2.HttpRequestResult(
            decision=pb2.DECISION_DENY,
            reason="unsupported middleware phase",
            reason_code="unsupported_phase",
        )
    return pb2.HttpRequestResult(decision=pb2.DECISION_ALLOW)


class Middleware(pb2_grpc.SupervisorMiddlewareServicer):
    """Adapt gRPC calls to the middleware's application functions."""

    async def Describe(
        self,
        request: object,
        context: grpc.aio.ServicerContext,
    ) -> pb2.MiddlewareManifest:
        del request, context
        return build_manifest()

    async def ValidateConfig(
        self,
        request: pb2.ValidateConfigRequest,
        context: grpc.aio.ServicerContext,
    ) -> pb2.ValidateConfigResponse:
        del context
        return validate_config(request)

    async def EvaluateHttpRequest(
        self,
        request: pb2.HttpRequestEvaluation,
        context: grpc.aio.ServicerContext,
    ) -> pb2.HttpRequestResult:
        del context
        return evaluate_http_request(request)


def create_server() -> grpc.aio.Server:
    """Create an unstarted server that accepts a maximum-sized body envelope."""
    server = grpc.aio.server(
        options=(
            ("grpc.max_receive_message_length", MAX_MESSAGE_BYTES),
            ("grpc.max_send_message_length", MAX_MESSAGE_BYTES),
        )
    )
    pb2_grpc.add_SupervisorMiddlewareServicer_to_server(Middleware(), server)
    return server


async def serve(listen: str) -> None:
    """Serve the middleware until termination."""
    server = create_server()
    if server.add_insecure_port(listen) == 0:
        raise RuntimeError(f"could not bind middleware server to {listen}")
    await server.start()
    try:
        await server.wait_for_termination()
    finally:
        await server.stop(grace=0)


def main(argv: Sequence[str] | None = None) -> None:
    """Run the middleware server."""
    parser = argparse.ArgumentParser(description="Run the __PROJECT_NAME__ middleware")
    parser.add_argument("--listen", default="127.0.0.1:50051")
    arguments = parser.parse_args(argv)
    asyncio.run(serve(arguments.listen))


if __name__ == "__main__":
    main()
