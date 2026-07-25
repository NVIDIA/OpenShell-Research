"""Privacy Guard command-line application."""

from __future__ import annotations

import importlib
import json
import logging
from dataclasses import dataclass
from typing import Annotated

import typer

from privacy_guard.engines import EntityProcessingStrategy
from privacy_guard.engines.registry import EngineRegistry, create_builtin_registry
from privacy_guard.service.server import DEFAULT_LISTEN_ADDRESS, PrivacyGuardServer

app = typer.Typer(
    name="privacy-guard",
    help="Run Privacy Guard and inspect installed entity-processing engines.",
    no_args_is_help=True,
    add_completion=False,
)


@app.callback()
def configure_cli(
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
    """Configure the command application and its engine inventory."""
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
def serve(
    context: typer.Context,
    listen: Annotated[
        str,
        typer.Option(help="Address on which the middleware server listens."),
    ] = DEFAULT_LISTEN_ADDRESS,
) -> None:
    """Run the middleware server with the selected engine inventory."""
    options = _command_options(context)
    PrivacyGuardServer(
        options.registry,
        log_request_content=options.log_request_content,
    ).run(listen)


@app.command("schema")
def schema(context: typer.Context) -> None:
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
def engines(context: typer.Context) -> None:
    """List installed engines and every supported processing strategy."""
    for description in _command_options(context).registry.describe_engines():
        strategies = ",".join(
            strategy.value
            for strategy in EntityProcessingStrategy
            if strategy in description.supported_strategies
        )
        typer.echo(
            f"{description.engine_name}\t{strategies}\t{description.description}"
        )


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


__all__ = ["app"]
