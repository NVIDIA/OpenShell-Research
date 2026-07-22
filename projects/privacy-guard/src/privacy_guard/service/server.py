"""gRPC server process that hosts Privacy Guard at a configured endpoint.

This is pure transport lifecycle -- it has no counterpart in an in-process
(built-in) middleware. It exists only because Privacy Guard runs out-of-process
and the supervisor reaches it over gRPC. The default endpoint is loopback.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence

import grpc

from privacy_guard.bindings import supervisor_middleware_pb2_grpc as pb2_grpc
from privacy_guard.constants import MAX_CONCURRENT_RPCS, MAX_RECEIVE_MESSAGE_BYTES
from privacy_guard.errors import ErrorCode, PrivacyGuardError
from privacy_guard.processor import RequestProcessor
from privacy_guard.scanners import RegexScanner, Scanner, ScannerConfig
from privacy_guard.service.servicer import PrivacyGuardMiddleware


class MiddlewareServer:
    """High-level server that wires a scanner into the Privacy Guard service."""

    def __init__(self, *, scanner: Scanner[ScannerConfig]) -> None:
        self._servicer = PrivacyGuardMiddleware(RequestProcessor([scanner]))

    def serve(self, listen: str = "127.0.0.1:50051") -> None:
        """Serve until termination using a managed synchronous entry point."""
        asyncio.run(serve(self._servicer, listen))


def create_server(servicer: PrivacyGuardMiddleware) -> grpc.aio.Server:
    """Build an unstarted gRPC server with the servicer mounted (no port bound).

    The receive limit reserves bounded space around the advertised body maximum
    for the protobuf envelope; the servicer enforces the body limit itself.
    """
    server = grpc.aio.server(
        maximum_concurrent_rpcs=MAX_CONCURRENT_RPCS,
        options=(("grpc.max_receive_message_length", MAX_RECEIVE_MESSAGE_BYTES),),
    )
    pb2_grpc.add_SupervisorMiddlewareServicer_to_server(servicer, server)
    return server


async def serve(
    servicer: PrivacyGuardMiddleware,
    listen: str = "127.0.0.1:50051",
) -> None:
    """Bind ``listen``, start the server, and serve until terminated."""
    server = create_server(servicer)
    try:
        bound_port = server.add_insecure_port(listen)
        if bound_port == 0:
            raise PrivacyGuardError(ErrorCode.SERVER_BIND_FAILED)
        await server.start()
        await server.wait_for_termination()
    finally:
        await server.stop(grace=0)
        await servicer.close()


def main(argv: Sequence[str] | None = None) -> int:
    """Load the required regex catalog, then run the middleware server."""
    parser = argparse.ArgumentParser(description="Run the Privacy Guard middleware")
    parser.add_argument("--scanner-config", required=True)
    parser.add_argument("--profile")
    parser.add_argument("--scanner-name", default="regex")
    parser.add_argument("--listen", default="127.0.0.1:50051")
    arguments = parser.parse_args(argv)
    scanner = RegexScanner.from_yaml(
        arguments.scanner_config,
        arguments.profile,
        scanner_name=arguments.scanner_name,
    )
    MiddlewareServer(scanner=scanner).serve(arguments.listen)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
