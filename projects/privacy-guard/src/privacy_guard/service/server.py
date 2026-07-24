"""Loopback gRPC server and configuration-discovery CLI."""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
from dataclasses import dataclass
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


def create_builtin_registry() -> EngineRegistry:
    """Build the finalized registry shipped by the base package."""
    registry = EngineRegistry()
    registry.register(RegexEngine)
    return registry.finalize()


class MiddlewareServer:
    """High-level server owning registry, middleware, gRPC, and shutdown."""

    def __init__(
        self,
        registry: EngineRegistry,
        *,
        log_request_content: bool = False,
    ) -> None:
        self._servicer = PrivacyGuardMiddleware(
            registry,
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
    registry_factory: Annotated[
        str | None,
        typer.Option(
            help=(
                "Python module and callable that return a finalized engine registry, "
                "formatted as module:factory."
            ),
        ),
    ] = None,
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
    context.obj = _CommandOptions(
        registry=_load_registry(registry_factory),
        log_request_content=debug_log_content,
    )
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
    options = _command_options(context)
    _LOGGER.info("privacy_guard_server_starting listen=%s", listen)
    MiddlewareServer(
        options.registry,
        log_request_content=options.log_request_content,
    ).serve(listen)


@app.command("schema")
def show_schema(context: typer.Context) -> None:
    """Print the exact finalized policy JSON Schema."""
    typer.echo(
        json.dumps(
            _command_options(context).registry.configuration_json_schema(),
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
        )
    )


@app.command("engines")
def show_engines(context: typer.Context) -> None:
    """List installed engines and every supported processing strategy."""
    for description in _command_options(context).registry.describe_engines():
        strategies = ",".join(
            strategy.value
            for strategy in EntityProcessingStrategy
            if strategy in description.supported_strategies
        )
        typer.echo(f"{description.engine}\t{strategies}\t{description.description}")


_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class _CommandOptions:
    registry: EngineRegistry
    log_request_content: bool


def _load_registry(factory_reference: str | None) -> EngineRegistry:
    if factory_reference is None:
        return create_builtin_registry()
    module_name, separator, factory_name = factory_reference.partition(":")
    if not separator or not module_name or not factory_name:
        raise typer.BadParameter(
            "registry factory must use module:factory",
            param_hint="--registry-factory",
        )
    try:
        module = importlib.import_module(module_name)
        factory = getattr(module, factory_name)
    except Exception:
        raise typer.BadParameter(
            "registry factory could not be loaded",
            param_hint="--registry-factory",
        ) from None
    if not callable(factory):
        raise typer.BadParameter(
            "registry factory is not callable",
            param_hint="--registry-factory",
        )
    try:
        registry = factory()
    except Exception:
        raise typer.BadParameter(
            "registry factory failed",
            param_hint="--registry-factory",
        ) from None
    if not isinstance(registry, EngineRegistry):
        raise typer.BadParameter(
            "registry factory returned an invalid object",
            param_hint="--registry-factory",
        )
    if not registry.is_finalized:
        raise typer.BadParameter(
            "registry factory returned an unfinalized registry",
            param_hint="--registry-factory",
        )
    return registry


def _command_options(context: typer.Context) -> _CommandOptions:
    options = context.obj
    if not isinstance(options, _CommandOptions):
        raise RuntimeError("Privacy Guard command context is unavailable")
    return options


if __name__ == "__main__":
    app()


__all__ = [
    "MiddlewareServer",
    "app",
    "create_builtin_registry",
    "create_server",
    "serve",
]
