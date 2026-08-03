"""Programmatic Egress Gate gRPC server lifecycle."""

from __future__ import annotations

import asyncio

import grpc

from egress_gate.bindings import supervisor_middleware_pb2_grpc as pb2_grpc
from egress_gate.constants import (
    DEFAULT_TIMEOUT_SECONDS,
    MAX_CONCURRENT_RPCS,
    MAX_RECEIVE_MESSAGE_BYTES,
)
from egress_gate.engines.registry import EngineRegistry
from egress_gate.errors import EgressGateError, ErrorCode
from egress_gate.logging import get_logger
from egress_gate.service.servicer import EgressGateMiddleware

DEFAULT_LISTEN_ADDRESS = "127.0.0.1:50051"


class EgressGateServer:
    """One-shot programmatic server for a finalized engine registry."""

    def __init__(
        self,
        registry: EngineRegistry,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        log_request_content: bool = False,
    ) -> None:
        self._middleware = EgressGateMiddleware(
            registry,
            timeout_seconds=timeout_seconds,
            log_request_content=log_request_content,
        )

    def serve_sync(self, listen: str = DEFAULT_LISTEN_ADDRESS) -> None:
        """Serve synchronously until termination."""
        try:
            asyncio.run(self.serve_async(listen))
        except KeyboardInterrupt:
            return

    async def serve_async(self, listen: str = DEFAULT_LISTEN_ADDRESS) -> None:
        """Serve asynchronously until termination, then close owned resources."""
        server = _create_grpc_server(self._middleware)
        try:
            try:
                requested_port = _validated_listen_port(listen)
                bound_port = server.add_insecure_port(listen)
                if bound_port != requested_port:
                    raise EgressGateError(ErrorCode.SERVER_BIND_FAILED)
                _LOGGER.info("egress_gate_server_bound listen=%r", listen)
                await server.start()
            except RuntimeError:
                raise EgressGateError(ErrorCode.SERVER_BIND_FAILED) from None
            await server.wait_for_termination()
        finally:
            try:
                await _stop_grpc_server(server)
            finally:
                await self._middleware.close()


_LOGGER = get_logger(__name__)


def _create_grpc_server(
    middleware: EgressGateMiddleware,
) -> grpc.aio.Server:
    server = grpc.aio.server(
        maximum_concurrent_rpcs=MAX_CONCURRENT_RPCS,
        options=(("grpc.max_receive_message_length", MAX_RECEIVE_MESSAGE_BYTES),),
    )
    pb2_grpc.add_SupervisorMiddlewareServicer_to_server(middleware, server)
    return server


async def _stop_grpc_server(server: grpc.aio.Server) -> None:
    shutdown = asyncio.create_task(server.stop(grace=0))
    try:
        await asyncio.shield(shutdown)
    except asyncio.CancelledError:
        if not shutdown.done():
            await shutdown
        raise


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
