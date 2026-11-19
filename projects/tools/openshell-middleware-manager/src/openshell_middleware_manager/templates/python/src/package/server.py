# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Pass-through OpenShell supervisor middleware server."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import AsyncIterator, Sequence

import grpc

from __PACKAGE_NAME__.bindings import extension_pb2
from __PACKAGE_NAME__.bindings import supervisor_middleware_pb2 as pb2
from __PACKAGE_NAME__.bindings import supervisor_middleware_pb2_grpc as pb2_grpc

SERVICE_NAME = "__SERVICE_NAME__"
SERVICE_VERSION = "0.1.0"
PROTOCOL_MAJOR = 1
PROTOCOL_MINOR = 0
CONTRACT_CAPABILITY = "openshell.supervisor-middleware.contract"
HTTP_HOOKS_CAPABILITY = "openshell.supervisor-middleware.http-hooks"
CAPABILITIES = (CONTRACT_CAPABILITY, HTTP_HOOKS_CAPABILITY)
MAX_PAYLOAD_BYTES = 4 * 1024 * 1024
MAX_MESSAGE_BYTES = MAX_PAYLOAD_BYTES + 1024 * 1024


def build_manifest() -> pb2.MiddlewareManifest:
    """Describe the operation, phase, and protocol contract supported by this service."""
    return pb2.MiddlewareManifest(
        name=SERVICE_NAME,
        bindings=[
            pb2.MiddlewareBinding(
                operation=pb2.SUPERVISOR_MIDDLEWARE_OPERATION_HTTP_REQUEST,
                phase=pb2.SUPERVISOR_MIDDLEWARE_PHASE_PRE_CREDENTIALS,
                max_payload_bytes=MAX_PAYLOAD_BYTES,
            )
        ],
        # Requiring the HTTP hooks capability selects HttpHooks for the HTTP binding.
        extension=extension_pb2.PeerMetadata(
            protocol_version=extension_pb2.ProtocolVersion(
                major=PROTOCOL_MAJOR,
                minor=PROTOCOL_MINOR,
            ),
            implementation_name=SERVICE_NAME,
            implementation_version=SERVICE_VERSION,
            supported_capabilities=CAPABILITIES,
            required_capabilities=CAPABILITIES,
        ),
    )


def find_protocol_mismatch(request: pb2.MiddlewareDescribeRequest) -> str | None:
    """Explain why the calling gateway or supervisor cannot use this service."""
    if not request.HasField("gateway"):
        return "the caller did not provide protocol metadata"
    caller = request.gateway
    if caller.protocol_version.major != PROTOCOL_MAJOR:
        return f"the caller uses unsupported protocol major {caller.protocol_version.major}"
    missing = sorted(set(CAPABILITIES) - set(caller.supported_capabilities))
    if missing:
        return f"the caller does not support required capabilities: {', '.join(missing)}"
    unsupported = sorted(set(caller.required_capabilities) - set(CAPABILITIES))
    if unsupported:
        return f"the caller requires unsupported capabilities: {', '.join(unsupported)}"
    return None


def validate_config(request: pb2.ValidateConfigRequest) -> pb2.ValidateConfigResponse:
    """Validate service-specific config before OpenShell admits a policy."""
    return pb2.ValidateConfigResponse(valid=True)


def evaluate_http_request_preflight(
    preflight: pb2.HttpRequestHookPreflight,
) -> pb2.HttpRequestHookResult:
    """Decide from the request head whether to continue, deny, or inspect the body."""
    if preflight.phase != pb2.SUPERVISOR_MIDDLEWARE_PHASE_PRE_CREDENTIALS:
        return pb2.HttpRequestHookResult(
            deny=pb2.HttpDenial(
                diagnostics=pb2.MiddlewareDiagnostics(
                    reason="unsupported middleware phase",
                    reason_code="unsupported_phase",
                )
            )
        )
    return pb2.HttpRequestHookResult(
        preflight_result=pb2.HttpPreflightResult(continue_without_body=pb2.HttpContinue())
    )


def evaluate_http_request_body(body: pb2.HttpWholeBody) -> pb2.HttpRequestHookResult:
    """Keep, replace, or deny a complete body after preflight selected whole-body inspection."""
    return pb2.HttpRequestHookResult(
        whole_body_result=pb2.HttpWholeBodyResult(unchanged=pb2.HttpUnchanged())
    )


def evaluate_http_request_event(
    event: pb2.HttpRequestHookEvent,
) -> pb2.HttpRequestHookResult | None:
    """Answer one hook stream event, or return None for an event that takes no result."""
    if event.HasField("preflight"):
        return evaluate_http_request_preflight(event.preflight)
    if event.HasField("whole_body"):
        return evaluate_http_request_body(event.whole_body)
    return None


class Middleware(pb2_grpc.SupervisorMiddlewareServicer, pb2_grpc.HttpHooksServicer):
    """Adapt gRPC calls to the middleware's application functions."""

    async def Describe(
        self,
        request: pb2.MiddlewareDescribeRequest,
        context: grpc.aio.ServicerContext,
    ) -> pb2.MiddlewareManifest:
        mismatch = find_protocol_mismatch(request)
        if mismatch is not None:
            await context.abort(grpc.StatusCode.FAILED_PRECONDITION, mismatch)
        return build_manifest()

    async def ValidateConfig(
        self,
        request: pb2.ValidateConfigRequest,
        context: grpc.aio.ServicerContext,
    ) -> pb2.ValidateConfigResponse:
        return validate_config(request)

    async def EvaluateHttpRequestHook(
        self,
        request_iterator: AsyncIterator[pb2.HttpRequestHookEvent],
        context: grpc.aio.ServicerContext,
    ) -> AsyncIterator[pb2.HttpRequestHookResult]:
        async for event in request_iterator:
            result = evaluate_http_request_event(event)
            if result is not None:
                yield result


def create_server() -> grpc.aio.Server:
    """Create an unstarted server that accepts a maximum-sized body envelope."""
    server = grpc.aio.server(
        options=(
            ("grpc.max_receive_message_length", MAX_MESSAGE_BYTES),
            ("grpc.max_send_message_length", MAX_MESSAGE_BYTES),
        )
    )
    middleware = Middleware()
    pb2_grpc.add_SupervisorMiddlewareServicer_to_server(middleware, server)
    pb2_grpc.add_HttpHooksServicer_to_server(middleware, server)
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
