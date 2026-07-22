"""gRPC server process that hosts Privacy Guard at a configured endpoint.

This is pure transport lifecycle -- it has no counterpart in an in-process
(built-in) middleware. It exists only because Privacy Guard runs out-of-process
and the supervisor reaches it over gRPC. The default endpoint is loopback.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import grpc
import typer

from privacy_guard.bindings import supervisor_middleware_pb2_grpc as pb2_grpc
from privacy_guard.constants import MAX_CONCURRENT_RPCS, MAX_RECEIVE_MESSAGE_BYTES
from privacy_guard.errors import ErrorCode, PrivacyGuardError
from privacy_guard.processor import RequestProcessor
from privacy_guard.scanners import RegexScanner, Scanner, ScannerConfig
from privacy_guard.service.servicer import PrivacyGuardMiddleware

app = typer.Typer(
    name="privacy-guard",
    help="Run Privacy Guard with a built-in scanner.",
    no_args_is_help=True,
    add_completion=False,
)


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


@app.callback()
def main(
    context: typer.Context,
    scanner_config: Annotated[
        Path,
        typer.Option(help="Path to the built-in scanner's configuration."),
    ],
    listen: Annotated[
        str,
        typer.Option(help="Address on which the middleware server listens."),
    ] = "127.0.0.1:50051",
) -> None:
    """Collect options shared by every built-in scanner."""
    context.obj = _CliOptions(scanner_config=scanner_config, listen=listen)


@app.command("regex")
def run_regex(
    context: typer.Context,
    profile: Annotated[
        str | None,
        typer.Option(help="Profile required for a multi-profile configuration."),
    ] = None,
    scanner_name: Annotated[
        str,
        typer.Option(help="Scanner identity attached to findings."),
    ] = "regex",
) -> None:
    """Run Privacy Guard with the built-in RegexScanner."""
    options = context.find_object(_CliOptions)
    assert options is not None
    try:
        scanner = RegexScanner.from_yaml(
            options.scanner_config,
            profile,
            scanner_name=scanner_name,
        )
        MiddlewareServer(scanner=scanner).serve(options.listen)
    except PrivacyGuardError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from None


@dataclass(frozen=True)
class _CliOptions:
    scanner_config: Path
    listen: str


if __name__ == "__main__":
    app()
