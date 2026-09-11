# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Programmatic Egress Gate gRPC server lifecycle."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol, runtime_checkable

import grpc
import jwt
from aiohttp import web
from google.protobuf.message import DecodeError

from egress_gate.bindings import supervisor_middleware_pb2_grpc as pb2_grpc
from egress_gate.constants import (
    DEFAULT_TIMEOUT_MIDDLEWARE_PROCESSING,
    MAX_CONCURRENT_RPCS,
    MAX_RECEIVE_MESSAGE_BYTES,
)
from egress_gate.errors import EgressGateError, ErrorCode
from egress_gate.gates.registry import GateRegistry
from egress_gate.logging import get_logger
from egress_gate.service.admission import (
    AdmissionServerConfig,
    admission_tls_context,
    create_admission_application,
)
from egress_gate.service.authentication import GatewayAuthentication
from egress_gate.service.servicer import EgressGateMiddleware

DEFAULT_LISTEN_ADDRESS = "127.0.0.1:50051"


class EgressGateServer:
    """One-shot programmatic server for an application gate registry."""

    def __init__(
        self,
        registry: GateRegistry,
        *,
        timeout_middleware_processing: float = DEFAULT_TIMEOUT_MIDDLEWARE_PROCESSING,
        admission: AdmissionServerConfig | None = None,
    ) -> None:
        self._admission = admission
        self._authentication = (
            GatewayAuthentication(
                admission.gateway_public_key.read_bytes(),
                admission.gateway_issuer,
                admission.gateway_audience,
            )
            if admission is not None
            else None
        )
        self._middleware = EgressGateMiddleware(
            registry,
            timeout_middleware_processing=timeout_middleware_processing,
            require_agent_attestation=admission is not None,
            expected_audience=admission.gateway_audience if admission else "",
        )

    def serve_sync(self, listen: str = DEFAULT_LISTEN_ADDRESS) -> None:
        """Serve synchronously until termination."""
        try:
            asyncio.run(self.serve_async(listen))
        except KeyboardInterrupt:
            return

    async def serve_async(self, listen: str = DEFAULT_LISTEN_ADDRESS) -> None:
        """Serve asynchronously until termination, then close owned resources."""
        server = _create_grpc_server(self._middleware, self._authentication)
        runner: web.AppRunner | None = None
        try:
            try:
                requested_port = _validated_listen_port(listen)
                if self._admission is None:
                    bound_port = server.add_insecure_port(listen)
                else:
                    config = self._admission
                    bound_port = server.add_secure_port(
                        listen,
                        grpc.ssl_server_credentials(
                            [
                                (
                                    config.tls_private_key.read_bytes(),
                                    config.tls_certificate.read_bytes(),
                                )
                            ]
                        ),
                    )
                    runner = web.AppRunner(
                        create_admission_application(self._middleware, config),
                        access_log=None,
                    )
                    await runner.setup()
                    http_port = _validated_listen_port(config.listen)
                    http_host = config.listen.rsplit(":", 1)[0].strip("[]")
                    await web.TCPSite(
                        runner,
                        http_host,
                        http_port,
                        ssl_context=admission_tls_context(config),
                    ).start()
                if bound_port != requested_port:
                    raise EgressGateError(ErrorCode.SERVER_BIND_FAILED)
                _LOGGER.info(
                    "egress_gate_server_bound listen=%r "
                    "timeout_middleware_processing=%s",
                    listen,
                    self._middleware.timeout_middleware_processing,
                )
                await server.start()
            except RuntimeError:
                raise EgressGateError(ErrorCode.SERVER_BIND_FAILED) from None
            await server.wait_for_termination()
        finally:
            try:
                await _stop_grpc_server(server)
            finally:
                try:
                    if runner is not None:
                        await runner.cleanup()
                finally:
                    await self._middleware.close()


_LOGGER = get_logger(__name__)


def _create_grpc_server(
    middleware: EgressGateMiddleware,
    authentication: GatewayAuthentication | None = None,
) -> grpc.aio.Server:
    server = grpc.aio.server(
        interceptors=(_MalformedProtobufInterceptor(authentication),),
        maximum_concurrent_rpcs=MAX_CONCURRENT_RPCS,
        options=(("grpc.max_receive_message_length", MAX_RECEIVE_MESSAGE_BYTES),),
    )
    pb2_grpc.add_SupervisorMiddlewareServicer_to_server(middleware, server)
    return server


class _MalformedProtobufInterceptor(grpc.aio.ServerInterceptor):
    """Authenticate RPCs and map decoding failures to content-safe statuses."""

    def __init__(self, authentication: GatewayAuthentication | None = None) -> None:
        self._authentication = authentication

    async def intercept_service(
        self,
        continuation: Callable[
            [grpc.HandlerCallDetails], Awaitable[grpc.RpcMethodHandler]
        ],
        handler_call_details: grpc.HandlerCallDetails,
    ) -> grpc.RpcMethodHandler:
        generic_handler = await continuation(handler_call_details)
        if not isinstance(generic_handler, _UnaryUnaryRpcMethodHandler):
            return generic_handler
        handler = generic_handler
        if handler.request_deserializer is None or handler.unary_unary is None:
            return generic_handler

        deserialize = handler.request_deserializer
        unary_unary = handler.unary_unary

        def deserialize_safely(data: bytes) -> object:
            try:
                return deserialize(data)
            except DecodeError:
                return _MALFORMED_PROTOBUF

        async def invoke_safely(
            request: object,
            context: grpc.aio.ServicerContext[object, object],
        ) -> object:
            if self._authentication is not None:
                try:
                    self._authentication.verify(
                        context.invocation_metadata() or (), request
                    )
                except (ValueError, jwt.PyJWTError):
                    await context.abort(
                        grpc.StatusCode.UNAUTHENTICATED, "authentication failed"
                    )
            if request is _MALFORMED_PROTOBUF:
                await context.abort(
                    grpc.StatusCode.INVALID_ARGUMENT,
                    str(EgressGateError(ErrorCode.REQUEST_PROTOBUF_INVALID)),
                )
            return await unary_unary(request, context)

        return grpc.unary_unary_rpc_method_handler(
            invoke_safely,
            request_deserializer=deserialize_safely,
            response_serializer=handler.response_serializer,
        )


async def _stop_grpc_server(server: grpc.aio.Server) -> None:
    shutdown = asyncio.create_task(server.stop(grace=0))
    try:
        await asyncio.shield(shutdown)
    except asyncio.CancelledError:
        if not shutdown.done():
            await shutdown
        raise


_MALFORMED_PROTOBUF = object()


@runtime_checkable
class _UnaryUnaryRpcMethodHandler(Protocol):
    request_deserializer: Callable[[bytes], object] | None
    response_serializer: Callable[[object], bytes] | None
    unary_unary: (
        Callable[[object, grpc.aio.ServicerContext[object, object]], Awaitable[object]]
        | None
    )


def _validated_listen_port(listen: str) -> int:
    if not isinstance(listen, str):
        raise EgressGateError(ErrorCode.SERVER_BIND_FAILED)
    if listen.startswith("["):
        closing_bracket = listen.rfind("]")
        if (
            closing_bracket < 2
            or listen[closing_bracket + 1 : closing_bracket + 2] != ":"
        ):
            raise EgressGateError(ErrorCode.SERVER_BIND_FAILED)
        host = listen[1:closing_bracket]
        port_text = listen[closing_bracket + 2 :]
    else:
        host, separator, port_text = listen.rpartition(":")
        if not separator or not host or ":" in host:
            raise EgressGateError(ErrorCode.SERVER_BIND_FAILED)
    if (
        not host
        or not port_text
        or len(port_text) > 5
        or not port_text.isascii()
        or not port_text.isdecimal()
    ):
        raise EgressGateError(ErrorCode.SERVER_BIND_FAILED)
    port = int(port_text)
    if not 1 <= port <= 65_535:
        raise EgressGateError(ErrorCode.SERVER_BIND_FAILED)
    return port


__all__ = [
    "DEFAULT_LISTEN_ADDRESS",
    "EgressGateServer",
]
