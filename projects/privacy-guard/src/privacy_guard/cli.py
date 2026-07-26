"""Privacy Guard command-line application."""

from __future__ import annotations

import importlib
import json
import logging
from dataclasses import dataclass
from typing import Annotated

import typer

from privacy_guard.constants import DEFAULT_TIMEOUT_SECONDS, MAX_TIMEOUT_SECONDS
from privacy_guard.engines import EntityProcessingStrategy
from privacy_guard.engines.registry import EngineRegistry, create_builtin_registry
from privacy_guard.service.server import DEFAULT_LISTEN_ADDRESS, PrivacyGuardServer
from privacy_guard.timeout import validate_timeout_seconds

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
    timeout_seconds: Annotated[
        float,
        typer.Option(
            help=(
                "Maximum seconds shared by all processing stages in one request; "
                f"must be greater than 0 and at most {MAX_TIMEOUT_SECONDS:g}."
            ),
        ),
    ] = DEFAULT_TIMEOUT_SECONDS,
) -> None:
    """Run the middleware server with the selected engine inventory."""
    options = _command_options(context)
    try:
        validated_timeout_seconds = validate_timeout_seconds(timeout_seconds)
    except ValueError as error:
        raise typer.BadParameter(
            str(error),
            param_hint="--timeout-seconds",
        ) from None
    PrivacyGuardServer(
        options.registry,
        timeout_seconds=validated_timeout_seconds,
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
            "Use module:factory, for example my_engines:create_registry.",
            param_hint="--registry-factory",
        )
    try:
        module = importlib.import_module(module_name)
    except Exception:
        raise typer.BadParameter(
            "Registry module could not be imported. Verify the module:factory "
            "reference, then import the module directly with content-safe "
            "diagnostics to find missing dependencies or startup failures.",
            param_hint="--registry-factory",
        ) from None
    try:
        factory = getattr(module, factory_name)
    except AttributeError:
        raise typer.BadParameter(
            "Registry module does not export the named factory. Correct the "
            "module:factory reference or export that callable.",
            param_hint="--registry-factory",
        ) from None
    except Exception:
        raise typer.BadParameter(
            "Registry factory could not be resolved. Import the module and access "
            "the factory directly with content-safe diagnostics, then fix its "
            "dynamic attribute lookup.",
            param_hint="--registry-factory",
        ) from None
    if not callable(factory):
        raise typer.BadParameter(
            "Registry factory is not callable. Export a callable that returns a "
            "finalized EngineRegistry.",
            param_hint="--registry-factory",
        )
    try:
        registry = factory()
    except Exception:
        raise typer.BadParameter(
            "Registry factory failed. Run the factory directly with content-safe "
            "diagnostics and fix its startup error.",
            param_hint="--registry-factory",
        ) from None
    if not isinstance(registry, EngineRegistry):
        raise typer.BadParameter(
            "Registry factory returned an invalid object. Return an EngineRegistry.",
            param_hint="--registry-factory",
        )
    if not registry.is_finalized:
        raise typer.BadParameter(
            "Registry factory returned an unfinalized registry. Call finalize() "
            "before returning it.",
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
