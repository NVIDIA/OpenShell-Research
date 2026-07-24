"""Loopback gRPC server and configuration-discovery CLI."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Annotated

import grpc
import typer

from privacy_guard.bindings import supervisor_middleware_pb2_grpc as pb2_grpc
from privacy_guard.constants import MAX_CONCURRENT_RPCS, MAX_RECEIVE_MESSAGE_BYTES
from privacy_guard.engine_registry import EngineRegistry
from privacy_guard.engines import EntityProcessingStrategy, RegexEngine
from privacy_guard.errors import ErrorCode, PrivacyGuardError
from privacy_guard.service.servicer import PrivacyGuardMiddleware

app = typer.Typer(
    name="privacy-guard",
    help="Run Privacy Guard or inspect its registered entity-processing engines.",
    no_args_is_help=True,
    add_completion=False,
)


def create_default_registry() -> EngineRegistry:
    """Build the operator registry shipped by the base package."""
    registry = EngineRegistry()
    registry.register(RegexEngine)
    registry.finalize()
    return registry


class MiddlewareServer:
    """High-level server owning registry, middleware, gRPC, and shutdown."""

    def __init__(
        self,
        *,
        registry: EngineRegistry | None = None,
        log_request_content: bool = False,
    ) -> None:
        self._servicer = PrivacyGuardMiddleware(
            registry or create_default_registry(),
            log_request_content=log_request_content,
        )

    def serve(self, listen: str = "127.0.0.1:50051") -> None:
        """Serve until termination through a managed synchronous entry point."""
        asyncio.run(serve(self._servicer, listen))


def create_server(servicer: PrivacyGuardMiddleware) -> grpc.aio.Server:
    """Build an unstarted bounded gRPC server."""
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
    """Bind, start, and serve until termination."""
    server = create_server(servicer)
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
        await server.stop(grace=0)
        await servicer.close()


@app.callback()
def main(
    context: typer.Context,
    debug: Annotated[
        bool,
        typer.Option(help="Enable content-safe processing diagnostics."),
    ] = False,
    debug_log_content: Annotated[
        bool,
        typer.Option(
            help="DANGEROUS: log complete input and processed text.",
        ),
    ] = False,
) -> None:
    """Configure Privacy Guard command logging."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("privacy_guard").setLevel(
        logging.DEBUG if debug or debug_log_content else logging.INFO
    )
    context.obj = debug_log_content
    if debug_log_content:
        _LOGGER.warning(
            "privacy_guard_request_content_logging_enabled "
            "complete_request_text_may_contain_secrets"
        )


@app.command("serve")
def run_server(
    context: typer.Context,
    listen: Annotated[
        str,
        typer.Option(help="Address on which the middleware server listens."),
    ] = "127.0.0.1:50051",
) -> None:
    """Run the service; entity behavior comes from prepared policy config."""
    _LOGGER.info("privacy_guard_server_starting listen=%s", listen)
    MiddlewareServer(log_request_content=context.obj is True).serve(listen)


@app.command("schema")
def show_schema() -> None:
    """Print the exact finalized policy JSON Schema."""
    typer.echo(
        json.dumps(
            create_default_registry().configuration_json_schema(),
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
        )
    )


@app.command("engines")
def show_engines() -> None:
    """List installed engines and every supported processing strategy."""
    for description in create_default_registry().describe_engines():
        strategies = ",".join(
            strategy.value
            for strategy in EntityProcessingStrategy
            if strategy in description.supported_strategies
        )
        typer.echo(f"{description.engine}\t{strategies}\t{description.description}")


_LOGGER = logging.getLogger(__name__)


if __name__ == "__main__":
    app()


__all__ = [
    "MiddlewareServer",
    "app",
    "create_default_registry",
    "create_server",
    "serve",
]
