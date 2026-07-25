"""Programmatic Privacy Guard gRPC server lifecycle."""

from __future__ import annotations

import asyncio
import logging

import grpc

from privacy_guard.bindings import supervisor_middleware_pb2_grpc as pb2_grpc
from privacy_guard.constants import MAX_CONCURRENT_RPCS, MAX_RECEIVE_MESSAGE_BYTES
from privacy_guard.engine_registry import EngineRegistry
from privacy_guard.errors import ErrorCode, PrivacyGuardError
from privacy_guard.service.servicer import PrivacyGuardMiddleware

DEFAULT_LISTEN_ADDRESS = "127.0.0.1:50051"


class PrivacyGuardServer:
    """One-shot programmatic server for a finalized engine registry."""

    def __init__(
        self,
        registry: EngineRegistry,
        *,
        log_request_content: bool = False,
    ) -> None:
        self._middleware = PrivacyGuardMiddleware(
            registry,
            log_request_content=log_request_content,
        )

    def run(self, listen: str = DEFAULT_LISTEN_ADDRESS) -> None:
        """Run synchronously until termination."""
        try:
            asyncio.run(self.serve(listen))
        except KeyboardInterrupt:
            return

    async def serve(self, listen: str = DEFAULT_LISTEN_ADDRESS) -> None:
        """Serve asynchronously until termination, then close owned resources."""
        server = _create_grpc_server(self._middleware)
        _LOGGER.info("privacy_guard_server_starting listen=%s", listen)
        try:
            try:
                bound_port = server.add_insecure_port(listen)
            except RuntimeError:
                raise PrivacyGuardError(ErrorCode.SERVER_BIND_FAILED) from None
            if bound_port == 0:
                raise PrivacyGuardError(ErrorCode.SERVER_BIND_FAILED)
            await server.start()
            await server.wait_for_termination()
        finally:
            try:
                await _stop_grpc_server(server)
            finally:
                await self._middleware.close()


_LOGGER = logging.getLogger(__name__)


def _create_grpc_server(
    middleware: PrivacyGuardMiddleware,
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


__all__ = [
    "DEFAULT_LISTEN_ADDRESS",
    "PrivacyGuardServer",
]
