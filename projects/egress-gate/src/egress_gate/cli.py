"""Egress Gate command-line application."""

from __future__ import annotations

import importlib
import ipaddress
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer

from egress_gate.constants import DEFAULT_TIMEOUT_SECONDS, MAX_TIMEOUT_SECONDS
from egress_gate.errors import EgressGateError
from egress_gate.gates.registry import GateRegistry, create_builtin_registry
from egress_gate.gateway_config import (
    MAX_MIDDLEWARE_REGISTRATION_NAME_BYTES,
    GatewayConfigError,
    GatewayConfigRemoval,
    GatewayConfigUpdate,
    default_gateway_config_path,
    remove_gateway_config,
    update_gateway_config,
    validate_middleware_name,
)
from egress_gate.logging import LoggingConfig, configure_logging, get_logger
from egress_gate.service.server import DEFAULT_LISTEN_ADDRESS, EgressGateServer
from egress_gate.timeout import validate_timeout_seconds

app = typer.Typer(
    name="egress-gate",
    help=(
        "Run Egress Gate, manage local OpenShell gateway registrations, and "
        "inspect installed request-level gates."
    ),
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
                "Load gates from a trusted Python callable, formatted as "
                "module:factory. The callable must return a finalized GateRegistry."
            ),
        ),
    ] = None,
    debug: Annotated[
        bool,
        typer.Option(
            "--debug",
            help=(
                "Log content-safe diagnostic details for startup and request handling."
            ),
        ),
    ] = False,
    debug_log_content: Annotated[
        bool,
        typer.Option(
            "--debug-log-content",
            help=(
                "DANGEROUS: log complete request and processed text, which may "
                "contain secrets or personal data."
            ),
        ),
    ] = False,
) -> None:
    """Configure the command application and its gate inventory."""
    configure_logging(
        LoggingConfig(level="DEBUG" if debug or debug_log_content else "INFO")
    )
    context.obj = _CommandOptions(
        registry=_load_registry(registry_factory),
        log_request_content=debug_log_content,
    )
    if debug_log_content:
        _LOGGER.warning(
            "egress_gate_request_content_logging_enabled "
            "complete_request_text_may_contain_secrets"
        )


@app.command("serve")
def serve(
    context: typer.Context,
    listen: Annotated[
        str,
        typer.Option(
            help=(
                "Host and port on which Egress Gate listens, formatted as "
                "host:port. Use 0.0.0.0 when sandbox supervisors must reach it."
            ),
        ),
    ] = DEFAULT_LISTEN_ADDRESS,
    timeout_seconds: Annotated[
        float,
        typer.Option(
            help=(
                "Maximum seconds shared by all processing gates in one request; "
                f"must be greater than 0 and at most {MAX_TIMEOUT_SECONDS:g}."
            ),
        ),
    ] = DEFAULT_TIMEOUT_SECONDS,
) -> None:
    """Run Egress Gate until the process receives a shutdown signal."""
    options = _command_options(context)
    try:
        validated_timeout_seconds = validate_timeout_seconds(timeout_seconds)
    except ValueError as error:
        raise typer.BadParameter(
            str(error),
            param_hint="--timeout-seconds",
        ) from None
    try:
        EgressGateServer(
            options.registry,
            timeout_seconds=validated_timeout_seconds,
            log_request_content=options.log_request_content,
        ).serve_sync(listen)
    except EgressGateError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from None


@app.command("add-gateway-registration")
def add_gateway_registration(
    host_ip: Annotated[
        str,
        typer.Option(
            help=(
                "Non-loopback IPv4 address of this host that both the OpenShell "
                "gateway and sandbox supervisors can reach."
            ),
        ),
    ],
    config: Annotated[
        Path | None,
        typer.Option(
            help=(
                "Gateway TOML to update. Defaults to "
                "`$OPENSHELL_GATEWAY_CONFIG` when set, otherwise `gateway.toml` "
                "under `$XDG_CONFIG_HOME/openshell`."
            ),
        ),
    ] = None,
    name: Annotated[
        str,
        typer.Option(
            help=(
                "Gateway registration name referenced by the policy's middleware "
                "field. OpenShell allows "
                f"1-{MAX_MIDDLEWARE_REGISTRATION_NAME_BYTES} ASCII bytes."
            ),
        ),
    ] = "egress-gate",
    port: Annotated[
        int,
        typer.Option(
            min=1,
            max=65535,
            help=(
                "Egress Gate port. Use the same port in `egress-gate serve --listen`."
            ),
        ),
    ] = 50051,
) -> None:
    """Add or update Egress Gate in an OpenShell gateway TOML file."""
    try:
        address = ipaddress.IPv4Address(host_ip)
    except ipaddress.AddressValueError:
        raise typer.BadParameter(
            "Pass one IPv4 address, for example --host-ip 192.168.1.20.",
            param_hint="--host-ip",
        ) from None
    if address.is_loopback or address.is_unspecified:
        raise typer.BadParameter(
            "Pass a non-loopback host IPv4 address reachable by sandbox "
            "supervisors; do not use 127.0.0.1 or 0.0.0.0.",
            param_hint="--host-ip",
        )
    try:
        validated_name = validate_middleware_name(name)
    except GatewayConfigError as error:
        raise typer.BadParameter(
            str(error),
            param_hint="--name",
        ) from None

    config_path = config or default_gateway_config_path()
    try:
        result = update_gateway_config(
            config_path,
            middleware_name=validated_name,
            host_ip=str(address),
            port=port,
        )
    except GatewayConfigError as error:
        typer.echo(
            f"Could not add or update the OpenShell gateway registration: {error}",
            err=True,
        )
        raise typer.Exit(code=1) from None

    action = {
        GatewayConfigUpdate.CREATED: "Created",
        GatewayConfigUpdate.ADDED: "Added the registration to",
        GatewayConfigUpdate.UPDATED: "Updated",
        GatewayConfigUpdate.UNCHANGED: "No changes needed in",
    }[result]
    typer.echo(f"{action} {config_path}")
    typer.echo(f"Registered {validated_name} at http://{address}:{port}")
    typer.echo(
        "Next: start Egress Gate, then restart the OpenShell gateway so it "
        "loads this registration."
    )


@app.command("remove-gateway-registration")
def remove_gateway_registration(
    name: Annotated[
        str,
        typer.Option(
            help=(
                "Gateway registration name to remove. OpenShell allows "
                f"1-{MAX_MIDDLEWARE_REGISTRATION_NAME_BYTES} ASCII bytes."
            ),
        ),
    ],
    config: Annotated[
        Path | None,
        typer.Option(
            help=(
                "Gateway TOML to update. Defaults to "
                "`$OPENSHELL_GATEWAY_CONFIG` when set, otherwise `gateway.toml` "
                "under `$XDG_CONFIG_HOME/openshell`."
            ),
        ),
    ] = None,
) -> None:
    """Remove a named registration from an OpenShell gateway TOML file."""
    try:
        validated_name = validate_middleware_name(name)
    except GatewayConfigError as error:
        raise typer.BadParameter(
            str(error),
            param_hint="--name",
        ) from None

    config_path = config or default_gateway_config_path()
    try:
        result = remove_gateway_config(
            config_path,
            middleware_name=validated_name,
        )
    except GatewayConfigError as error:
        typer.echo(
            f"Could not remove the OpenShell gateway registration: {error}",
            err=True,
        )
        raise typer.Exit(code=1) from None

    if result is GatewayConfigRemoval.REMOVED:
        typer.echo(f"Removed {validated_name} from {config_path}")
        typer.echo(
            "Next: restart the OpenShell gateway so it unloads this registration."
        )
    else:
        typer.echo(f"No registration named {validated_name} found in {config_path}")


@app.command("configuration-schema")
def configuration_schema(context: typer.Context) -> None:
    """Print the policy configuration JSON Schema for the installed gates."""
    typer.echo(
        json.dumps(
            _command_options(context).registry.configuration_json_schema(),
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
        )
    )


@app.command("gates")
def gates(context: typer.Context) -> None:
    """List installed gates, capabilities, and declared finding types."""
    for description in _command_options(context).registry.describe_gates():
        finding_types = ",".join(item.type for item in description.finding_types)
        typer.echo(
            f"{description.gate_type}\t{finding_types or '-'}\t"
            f"{description.description}"
        )


_LOGGER = get_logger(__name__)


@dataclass(frozen=True)
class _CommandOptions:
    registry: GateRegistry
    log_request_content: bool


def _load_registry(factory_reference: str | None) -> GateRegistry:
    if factory_reference is None:
        return create_builtin_registry()
    module_name, separator, factory_name = factory_reference.partition(":")
    if not separator or not module_name or not factory_name:
        raise typer.BadParameter(
            "Use module:factory, for example my_gates:create_registry.",
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
    except Exception:
        raise typer.BadParameter(
            "Registry factory could not be resolved. Verify the module:factory "
            "reference and exported callable, then access it directly with "
            "content-safe diagnostics.",
            param_hint="--registry-factory",
        ) from None
    if not callable(factory):
        raise typer.BadParameter(
            "Registry factory is not callable. Export a callable that returns a "
            "finalized GateRegistry.",
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
    if not isinstance(registry, GateRegistry):
        raise typer.BadParameter(
            "Registry factory returned an invalid object. Return a GateRegistry.",
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
        raise RuntimeError("Egress Gate command context is unavailable")
    return options


if __name__ == "__main__":
    app()


__all__ = ["app"]
