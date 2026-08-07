"""Egress Gate command-line application."""

from __future__ import annotations

import base64
import binascii
import importlib
import ipaddress
import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Annotated, Literal, Self

import typer
import yaml
from pydantic import ValidationError, field_validator, model_validator
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from yaml.constructor import ConstructorError
from yaml.events import AliasEvent
from yaml.nodes import MappingNode
from yaml.resolver import BaseResolver

from egress_gate.base import StrictDomainModel
from egress_gate.constants import (
    DEFAULT_GATEWAY_REGISTRATION_TIMEOUT,
    DEFAULT_TIMEOUT_MIDDLEWARE_PROCESSING,
    MAX_BODY_BYTES,
    MAX_EVALUATION_CASE_NAME_BYTES,
    MAX_EVALUATION_CASES,
    MAX_EVALUATION_FILE_BYTES,
    MAX_EVALUATION_TAGS,
    MAX_PROTO_FINDING_GROUPS,
)
from egress_gate.errors import EgressGateError, GateRegistryError
from egress_gate.gates.base import GateCapability
from egress_gate.gates.registry import (
    GateRegistry,
    PolicyValidationError,
    create_builtin_registry,
)
from egress_gate.gateway_config import (
    MAX_MIDDLEWARE_REGISTRATION_NAME_BYTES,
    GatewayConfigError,
    GatewayConfigRemoval,
    GatewayConfigUpdate,
    GatewayMiddlewareRegistration,
    default_gateway_config_path,
    list_gateway_registrations,
    read_remembered_gateway_timeout,
    remember_gateway_registration,
    remove_gateway_config,
    update_gateway_config,
    validate_middleware_name,
)
from egress_gate.logging import LoggingConfig, configure_logging, get_logger
from egress_gate.request import HttpHeader, HttpRequest, HttpTarget, RequestContext
from egress_gate.result import EgressResult, GateDecisionSource
from egress_gate.string_validators import BoundedMetadataString
from egress_gate.timeout import (
    Timeout,
    parse_duration,
    parse_timeout_duration,
    validate_timeout_middleware_processing,
)

_DURATION_FORMAT_HELP = (
    "Use an integer followed by s for seconds or ms for milliseconds, such as "
    "10s or 500ms."
)
_TIMEOUT_DURATION_HELP = f"{_DURATION_FORMAT_HELP} Minimum 10ms."
_LOG = get_logger(__name__)

app = typer.Typer(
    name="egress-gate",
    help=(
        "Run the OpenShell middleware, test policies offline, manage the OpenShell "
        "gateway registration, and inspect installed gates."
    ),
    invoke_without_command=True,
    no_args_is_help=False,
    add_completion=False,
    rich_markup_mode=None,
)
gates_app = typer.Typer(
    help="Inspect installed gates and the policy schema they accept.",
    no_args_is_help=True,
    rich_markup_mode=None,
)
app.add_typer(
    gates_app,
    name="gates",
    short_help="Inspect installed gates and policy schema.",
)


@app.callback()
def configure_cli(
    context: typer.Context,
    version_requested: Annotated[
        bool,
        typer.Option(
            "--version",
            help="Show the installed Egress Gate version and exit.",
            is_eager=True,
        ),
    ] = False,
    registry: Annotated[
        str | None,
        typer.Option(
            "--registry",
            help=(
                "Load a trusted MODULE:ATTRIBUTE containing a GateRegistry or a "
                "zero-argument registry factory. This option applies to every command."
            ),
        ),
    ] = None,
    debug: Annotated[
        bool,
        typer.Option(
            "--debug",
            help="Log content-safe startup and request diagnostics.",
        ),
    ] = False,
) -> None:
    """Configure the command application and its gate inventory."""
    if version_requested:
        _CONSOLE.print(f"egress-gate {_package_version()}")
        raise typer.Exit
    configure_logging(LoggingConfig(level="DEBUG" if debug else "INFO"))
    context.obj = _CommandOptions(registry=_load_registry(registry))
    if context.invoked_subcommand is None:
        _CONSOLE.print(context.get_help())
        raise typer.Exit


@app.command("serve", short_help="Start the Egress Gate gRPC service.")
def serve(
    context: typer.Context,
    listen: Annotated[
        str,
        typer.Option(
            help=(
                "Listen address in HOST:PORT form. Use 0.0.0.0 only when sandbox "
                "supervisors must connect across a network namespace."
            ),
        ),
    ] = "127.0.0.1:50051",
    timeout: Annotated[
        str,
        typer.Option(
            "--timeout",
            help=(
                "Internal processing budget for one request. "
                f"{_TIMEOUT_DURATION_HELP} The OpenShell gateway applies its "
                "separately configured RPC timeout."
            ),
        ),
    ] = f"{DEFAULT_TIMEOUT_MIDDLEWARE_PROCESSING:g}s",
) -> None:
    """Start the Egress Gate gRPC service and run until shutdown."""
    options = _command_options(context)
    from egress_gate.service.server import EgressGateServer

    try:
        timeout_middleware_processing = parse_timeout_duration(timeout)
    except ValueError as error:
        raise typer.BadParameter(
            str(error),
            param_hint="--timeout",
        ) from None
    try:
        remembered_timeout = read_remembered_gateway_timeout()
    except GatewayConfigError as error:
        _render_cli_error(
            "Gateway timeout could not be validated",
            code="gateway_config_error",
            message=str(error),
        )
        raise typer.Exit(code=1) from None
    if remembered_timeout is not None:
        remembered, timeout_gateway_ceiling = remembered_timeout
        if timeout_middleware_processing >= timeout_gateway_ceiling:
            raise typer.BadParameter(
                "The middleware processing timeout must be less than the "
                f"{timeout_gateway_ceiling:g}s gateway timeout configured for "
                f"{remembered.middleware_name!r} in {remembered.config_path}.",
                param_hint="--timeout",
            )
        _LOG.info(
            "Validated timeout_middleware_processing=%ss against "
            "timeout_gateway_ceiling=%ss registration=%s gateway_config=%s",
            timeout_middleware_processing,
            timeout_gateway_ceiling,
            remembered.middleware_name,
            remembered.config_path,
        )
    try:
        EgressGateServer(
            options.registry,
            timeout_middleware_processing=timeout_middleware_processing,
        ).serve_sync(listen)
    except EgressGateError as error:
        _render_egress_error("Egress Gate could not start", error)
        raise typer.Exit(code=1) from None


@app.command(
    "add-gateway-registration",
    short_help="Register Egress Gate with OpenShell.",
)
def add_gateway_registration(
    host_ip: Annotated[
        str,
        typer.Option(
            help=(
                "Non-loopback IPv4 address that the OpenShell gateway and sandbox "
                "supervisors can use to reach this Egress Gate service."
            ),
        ),
    ],
    config: Annotated[
        Path | None,
        typer.Option(
            help=(
                "OpenShell gateway TOML file to update. By default, use "
                "OPENSHELL_GATEWAY_CONFIG, then the standard per-user file."
            ),
        ),
    ] = None,
    name: Annotated[
        str,
        typer.Option(
            help=(
                "Registration name used by an OpenShell policy's middleware field. "
                "OpenShell allows "
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
                "Port to advertise to OpenShell. It must match the port in the "
                "egress-gate serve --listen address."
            ),
        ),
    ] = 50051,
    timeout: Annotated[
        str,
        typer.Option(
            "--timeout",
            help=(
                "Gateway RPC timeout to write in the registration. "
                f"{_DURATION_FORMAT_HELP}"
            ),
        ),
    ] = DEFAULT_GATEWAY_REGISTRATION_TIMEOUT,
) -> None:
    """Add or update Egress Gate with a configurable gateway RPC timeout."""
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
    try:
        parse_duration(timeout)
    except ValueError as error:
        raise typer.BadParameter(
            str(error),
            param_hint="--timeout",
        ) from None
    config_path = config or default_gateway_config_path()
    try:
        result = update_gateway_config(
            config_path,
            middleware_name=validated_name,
            host_ip=str(address),
            port=port,
            timeout_gateway_ceiling=timeout,
        )
        remember_gateway_registration(
            config_path,
            middleware_name=validated_name,
        )
    except GatewayConfigError as error:
        _render_cli_error(
            "Gateway registration could not be saved",
            code="gateway_config_error",
            message=str(error),
        )
        raise typer.Exit(code=1) from None

    change = {
        GatewayConfigUpdate.CREATED: "Created the gateway configuration file",
        GatewayConfigUpdate.ADDED: "Added the registration",
        GatewayConfigUpdate.UPDATED: "Updated the registration",
        GatewayConfigUpdate.UNCHANGED: "Registration was already current",
    }[result]
    _render_registration(
        title="Gateway registration is ready",
        config_path=config_path,
        name=validated_name,
        endpoint=f"http://{address}:{port}",
        timeout_gateway_ceiling=timeout,
        change=change,
        next_step=(
            "Start Egress Gate, then restart the OpenShell gateway to load this "
            "registration."
        ),
    )


@app.command(
    "list-gateway-registrations",
    short_help="List OpenShell middleware registrations.",
)
def list_gateway_registrations_command(
    config: Annotated[
        Path | None,
        typer.Option(
            help=(
                "OpenShell gateway TOML file to inspect. By default, use "
                "OPENSHELL_GATEWAY_CONFIG, then the standard per-user file."
            ),
        ),
    ] = None,
) -> None:
    """List the names and endpoints of registered OpenShell middleware."""
    config_path = config or default_gateway_config_path()
    try:
        registrations = list_gateway_registrations(config_path)
    except GatewayConfigError as error:
        _render_cli_error(
            "Gateway registrations could not be listed",
            code="gateway_config_error",
            message=str(error),
        )
        raise typer.Exit(code=1) from None

    _render_gateway_registrations(config_path, registrations)


@app.command(
    "remove-gateway-registration",
    short_help="Remove an OpenShell registration.",
)
def remove_gateway_registration(
    name: Annotated[
        str,
        typer.Option(
            help="Exact registration name to remove from the gateway config.",
        ),
    ],
    config: Annotated[
        Path | None,
        typer.Option(
            help=(
                "OpenShell gateway TOML file to update. By default, use "
                "OPENSHELL_GATEWAY_CONFIG, then the standard per-user file."
            ),
        ),
    ] = None,
) -> None:
    """Remove a named registration from an OpenShell gateway TOML file."""
    config_path = config or default_gateway_config_path()
    try:
        result = remove_gateway_config(
            config_path,
            middleware_name=name,
        )
    except GatewayConfigError as error:
        _render_cli_error(
            "Gateway registration could not be removed",
            code="gateway_config_error",
            message=str(error),
        )
        raise typer.Exit(code=1) from None

    if result is GatewayConfigRemoval.REMOVED:
        _render_registration(
            title="Gateway registration was removed",
            config_path=config_path,
            name=name,
            next_step=("Restart the OpenShell gateway to unload this registration."),
        )
    else:
        _render_registration(
            title="Gateway registration was not found",
            config_path=config_path,
            name=name,
            status_style="bold yellow",
        )


@gates_app.command("list")
def list_gates(context: typer.Context) -> None:
    """Show what each installed gate can read, change, decide, and report."""
    _render_gates(_command_options(context).registry)


@gates_app.command("schema")
def gate_schema(context: typer.Context) -> None:
    """Print the complete policy JSON Schema for the installed gates."""
    schema = json.dumps(
        _command_options(context).registry.configuration_json_schema(),
        indent=2,
        ensure_ascii=False,
        sort_keys=True,
    )
    _CONSOLE.print(
        Syntax(
            schema,
            "json",
            theme="ansi_dark",
            word_wrap=False,
        ),
        soft_wrap=True,
    )


@app.command("validate", short_help="Check a policy against installed gates.")
def validate_policy(
    context: typer.Context,
    policy: Annotated[
        Path,
        typer.Option(
            "--policy",
            help="Path to the YAML policy to check.",
        ),
    ],
) -> None:
    """Check a policy without preparing gates or activating the policy."""
    options = _command_options(context)
    try:
        values = _load_policy(policy)
        options.registry.validate_config(values)
    except _EvaluationCorpusError:
        _render_cli_error(
            "Policy validation failed",
            code="invalid_input",
            message="The policy file could not be read as a supported YAML policy.",
        )
        raise typer.Exit(code=1) from None
    except PolicyValidationError as error:
        _render_cli_error(
            "Policy validation failed",
            code=error.code.value,
            message=(f"Policy field {error.formatted_path}: {error.category.value}."),
            hint="Run egress-gate gates schema and correct that field.",
        )
        raise typer.Exit(code=1) from None
    except EgressGateError:
        _render_cli_error(
            "Policy validation failed",
            code="config_invalid",
            message="The policy does not match the schema for the installed gates.",
            hint=(
                "Run egress-gate gates schema, then check the gate names, kinds, "
                "required fields, and pattern catalog."
            ),
        )
        raise typer.Exit(code=1) from None
    _CONSOLE.print("[bold green]✓[/bold green] Policy is valid")


@app.command("evaluate", short_help="Test policy cases without starting the service.")
def evaluate(
    context: typer.Context,
    policy: Annotated[
        Path,
        typer.Option(
            "--policy",
            help="Path to the YAML policy to test.",
        ),
    ],
    cases: Annotated[
        Path,
        typer.Option(
            "--cases",
            help="Path to the YAML file of saved request cases and expected results.",
        ),
    ],
    timeout: Annotated[
        str,
        typer.Option(
            "--timeout",
            help=(
                "Timeout for policy preparation and, separately, each case. "
                f"{_TIMEOUT_DURATION_HELP}"
            ),
        ),
    ] = f"{DEFAULT_TIMEOUT_MIDDLEWARE_PROCESSING:g}s",
) -> None:
    """Test saved requests against a policy without starting the service."""
    options = _command_options(context)
    try:
        timeout_seconds = parse_timeout_duration(timeout)
    except ValueError as error:
        raise typer.BadParameter(
            str(error),
            param_hint="--timeout",
        ) from None
    try:
        policy_values = _load_policy(policy)
    except _EvaluationCorpusError:
        _render_cli_error(
            "Evaluation could not start",
            code="invalid_policy_file",
            message="The policy file could not be read as a supported YAML policy.",
        )
        raise typer.Exit(code=2) from None
    try:
        corpus = _load_corpus(cases)
    except _EvaluationCorpusError:
        _render_cli_error(
            "Evaluation could not start",
            code="invalid_cases_file",
            message=(
                "The cases file could not be read as a valid version 1 YAML test suite."
            ),
        )
        raise typer.Exit(code=2) from None
    try:
        summary = _run_corpus(
            options.registry,
            policy_values,
            corpus,
            timeout_seconds=timeout_seconds,
        )
    except _CaseExecutionError as error:
        if error.completed:
            _render_evaluation(
                _EvaluationSummary(cases=error.completed),
                title="Completed before failure",
            )
        failure_title = f"Evaluation failed for case {error.case_name}"
        if isinstance(error.cause, EgressGateError):
            _render_egress_error(failure_title, error.cause)
        else:
            _render_cli_error(
                failure_title,
                code="execution_failed",
                message="An unexpected error stopped the evaluation.",
                hint="Check the configured gate and its resources, then retry.",
            )
        raise typer.Exit(code=2) from None
    except EgressGateError as error:
        _render_egress_error("Evaluation failed", error)
        raise typer.Exit(code=2) from None
    except Exception:
        _render_cli_error(
            "Evaluation failed",
            code="execution_failed",
            message="An unexpected error stopped the evaluation.",
            hint=(
                "Check custom gate and application-owned resource setup, then retry."
            ),
        )
        raise typer.Exit(code=2) from None

    _render_evaluation(summary)
    if summary.failed:
        raise typer.Exit(code=1)


_CONSOLE = Console()
_ERROR_CONSOLE = Console(stderr=True)


@dataclass(frozen=True)
class _CommandOptions:
    registry: GateRegistry


class _EvaluationCorpusError(Exception):
    """A content-safe offline policy or corpus input failure."""


class _CaseExecutionError(Exception):
    """One case failure plus safe results completed before it."""

    def __init__(
        self,
        *,
        case_name: str,
        completed: tuple[_CaseEvaluation, ...],
        cause: Exception,
    ) -> None:
        self.case_name = case_name
        self.completed = completed
        self.cause = cause
        super().__init__("corpus case execution failed")


class _CorpusProvenance(StrictDomainModel):
    """Required origin and redaction declaration for one corpus case."""

    kind: Literal["synthetic", "captured"]
    redacted: bool


class _CorpusBody(StrictDomainModel):
    """One bounded UTF-8 or standard base64 body representation."""

    encoding: Literal["utf8", "base64"]
    value: str

    @model_validator(mode="after")
    def _value_is_bounded_and_decodable(self) -> Self:
        if self.encoding == "utf8":
            try:
                if len(self.value.encode("utf-8", errors="strict")) > MAX_BODY_BYTES:
                    raise ValueError
            except UnicodeEncodeError:
                raise ValueError("body value is not valid UTF-8") from None
            return self

        try:
            encoded = self.value.encode("ascii")
            decoded = base64.b64decode(encoded, validate=True)
        except (UnicodeEncodeError, binascii.Error, ValueError, OverflowError):
            raise ValueError("body value is not valid base64") from None
        if len(decoded) > MAX_BODY_BYTES:
            raise ValueError("decoded body exceeds the size limit")
        if base64.b64encode(decoded) != encoded:
            raise ValueError("body base64 is not canonical")
        return self

    def decode(self) -> bytes:
        """Decode the bounded body without exposing input in an error."""
        if self.encoding == "utf8":
            return self.value.encode("utf-8")
        return base64.b64decode(self.value.encode("ascii"), validate=True)


class _CorpusRequest(StrictDomainModel):
    """The exact protobuf-free request fields accepted by a corpus case."""

    context: RequestContext
    target: HttpTarget
    headers: tuple[HttpHeader, ...]
    body: _CorpusBody

    @field_validator("context", mode="before")
    @classmethod
    def _normalize_context_sequences(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        context = dict(value)
        process = context.get("originating_process")
        if isinstance(process, Mapping):
            process_values = dict(process)
            ancestors = process_values.get("ancestors")
            if isinstance(ancestors, list):
                process_values["ancestors"] = tuple(ancestors)
            context["originating_process"] = process_values
        return context

    @field_validator("headers", mode="before")
    @classmethod
    def _headers_are_a_tuple(cls, value: object) -> object:
        if isinstance(value, list):
            return tuple(value)
        return value

    def to_http_request(self) -> HttpRequest:
        """Build the same immutable request value used by the service."""
        return HttpRequest(
            context=self.context,
            target=self.target,
            headers=self.headers,
            body=self.body.decode(),
        )


class _CorpusExpected(StrictDomainModel):
    """Required decision and optional content-safe result projections."""

    decision: Literal["allow", "deny"]
    decision_source_kind: (
        Literal["gate", "pipeline_default", "runtime_limit"] | None
    ) = None
    gate_name: BoundedMetadataString | None = None
    gate_type: BoundedMetadataString | None = None
    finding_types: tuple[BoundedMetadataString, ...] | None = None

    @field_validator("finding_types", mode="before")
    @classmethod
    def _finding_types_are_a_tuple(cls, value: object) -> object:
        if isinstance(value, list):
            return tuple(value)
        return value

    @model_validator(mode="after")
    def _optional_fields_are_omitted_or_present(self) -> Self:
        for field_name in (
            "decision_source_kind",
            "gate_name",
            "gate_type",
            "finding_types",
        ):
            if (
                field_name in self.model_fields_set
                and getattr(self, field_name) is None
            ):
                raise ValueError(f"{field_name} must be omitted when not expected")
        if (
            self.finding_types is not None
            and len(self.finding_types) > MAX_PROTO_FINDING_GROUPS
        ):
            raise ValueError("too many expected finding types")
        gate_fields_present = any(
            field_name in self.model_fields_set
            for field_name in ("gate_name", "gate_type")
        )
        if gate_fields_present and self.decision_source_kind != "gate":
            raise ValueError("gate source fields require a gate decision source")
        return self


class _EvaluationCase(StrictDomainModel):
    """One named request, provenance declaration, and expected projection."""

    name: BoundedMetadataString
    tags: tuple[BoundedMetadataString, ...] = ()
    provenance: _CorpusProvenance
    request: _CorpusRequest
    expected: _CorpusExpected

    @field_validator("name")
    @classmethod
    def _name_is_bounded_for_reporting(cls, value: str) -> str:
        if len(value.encode("utf-8")) > MAX_EVALUATION_CASE_NAME_BYTES:
            raise ValueError("case name exceeds the size limit")
        return value

    @field_validator("tags", mode="before")
    @classmethod
    def _tags_are_a_tuple(cls, value: object) -> object:
        if isinstance(value, list):
            return tuple(value)
        return value

    @model_validator(mode="after")
    def _tags_are_bounded(self) -> Self:
        if len(self.tags) > MAX_EVALUATION_TAGS:
            raise ValueError("case has too many tags")
        return self


class _EvaluationCorpus(StrictDomainModel):
    """Version-one strict bounded corpus document."""

    version: Literal[1]
    cases: tuple[_EvaluationCase, ...]

    @field_validator("cases", mode="before")
    @classmethod
    def _cases_are_bounded_tuple(cls, value: object) -> object:
        if not isinstance(value, list | tuple) or not value:
            raise ValueError("corpus cases must be a non-empty list")
        if len(value) > MAX_EVALUATION_CASES:
            raise ValueError("corpus has too many cases")
        return tuple(value)

    @model_validator(mode="after")
    def _case_names_are_unique(self) -> Self:
        names = tuple(case.name for case in self.cases)
        if len(names) != len(set(names)):
            raise ValueError("corpus case names must be unique")
        return self


@dataclass(frozen=True)
class _FieldDifference:
    """One stable expected-versus-actual projection difference."""

    field: str
    expected: object
    actual: object


@dataclass(frozen=True)
class _CaseEvaluation:
    """Content-safe result for one corpus case."""

    name: str
    differences: tuple[_FieldDifference, ...]

    @property
    def matched(self) -> bool:
        return not self.differences


@dataclass(frozen=True)
class _EvaluationSummary:
    """Stable aggregate for an offline corpus run."""

    cases: tuple[_CaseEvaluation, ...]

    @property
    def total(self) -> int:
        return len(self.cases)

    @property
    def passed(self) -> int:
        return sum(case.matched for case in self.cases)

    @property
    def failed(self) -> int:
        return self.total - self.passed


class _StrictEvaluationLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects aliases and duplicate mapping keys."""

    def compose_node(
        self,
        parent: object,
        index: object,
    ) -> yaml.Node:
        if self.check_event(AliasEvent):
            raise ConstructorError(
                None,
                None,
                "YAML aliases are not supported",
                self.peek_event().start_mark,
            )
        return super().compose_node(parent, index)


def _construct_unique_mapping(
    loader: _StrictEvaluationLoader,
    node: MappingNode,
    deep: bool = False,
) -> dict[object, object]:
    loader.flatten_mapping(node)
    mapping: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable key",
                key_node.start_mark,
            ) from None
        if duplicate:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found a duplicate key",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_StrictEvaluationLoader.add_constructor(
    BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _load_policy(path: Path) -> Mapping[str, object]:
    """Load one bounded strict YAML policy."""
    values = _load_yaml(path)
    if not isinstance(values, Mapping):
        raise _EvaluationCorpusError
    policy_values: dict[str, object] = {}
    for key, value in values.items():
        if not isinstance(key, str):
            raise _EvaluationCorpusError
        policy_values[key] = value
    return policy_values


def _load_corpus(path: Path) -> _EvaluationCorpus:
    """Load and validate one version-one strict YAML corpus."""
    values = _load_yaml(path)
    try:
        return _EvaluationCorpus.model_validate(values)
    except (TypeError, ValueError, ValidationError):
        raise _EvaluationCorpusError from None


def _run_corpus(
    registry: GateRegistry,
    policy_values: Mapping[str, object],
    corpus: _EvaluationCorpus,
    *,
    timeout_seconds: float,
) -> _EvaluationSummary:
    """Prepare once, then evaluate every case with a fresh shared timeout."""
    validated_timeout = validate_timeout_middleware_processing(timeout_seconds)
    validated_config = registry.validate_config(policy_values)
    processor = registry.prepare_processor(
        validated_config,
        timeout=Timeout.from_seconds(validated_timeout),
    )
    evaluations: list[_CaseEvaluation] = []
    for case in corpus.cases:
        try:
            result = processor.process(
                case.request.to_http_request(),
                timeout=Timeout.from_seconds(validated_timeout),
            )
        except Exception as error:
            raise _CaseExecutionError(
                case_name=case.name,
                completed=tuple(evaluations),
                cause=error,
            ) from None
        evaluations.append(
            _CaseEvaluation(
                name=case.name,
                differences=_compare_result(case.expected, result),
            )
        )
    return _EvaluationSummary(cases=tuple(evaluations))


def _render_evaluation(
    summary: _EvaluationSummary,
    *,
    title: str = "Policy evaluation",
) -> None:
    """Render content-safe case results and their aggregate."""
    table = Table(
        title=title,
        box=None,
        pad_edge=False,
        padding=(0, 2),
        header_style="bold cyan",
        title_style="bold",
        title_justify="left",
    )
    table.add_column("Status", no_wrap=True)
    table.add_column("Case", ratio=2)
    table.add_column("Details", ratio=3)

    for evaluation in summary.cases:
        if evaluation.matched:
            status = Text("PASS", style="bold green")
            details = Text("All checks matched", style="dim")
        else:
            status = Text("FAIL", style="bold red")
            details = Text()
            for index, difference in enumerate(evaluation.differences):
                if index:
                    details.append("\n")
                details.append(f"{difference.field}: ", style="bold")
                details.append("expected ", style="dim")
                details.append(_format_value(difference.expected))
                details.append(" · actual ", style="dim")
                details.append(_format_value(difference.actual))
        table.add_row(status, Text(evaluation.name), details)

    _CONSOLE.print(table)
    _CONSOLE.print(
        Text.assemble(
            (f"{summary.passed} passed", "bold green"),
            " · ",
            (
                f"{summary.failed} failed",
                "bold red" if summary.failed else "dim",
            ),
            " · ",
            (f"{summary.total} total", "dim"),
        )
    )


def _render_gates(registry: GateRegistry) -> None:
    """Render the installed gate inventory for a person."""
    _CONSOLE.print("[bold]Installed gates[/bold]")
    for description in registry.describe_gates():
        finding_types = (
            ", ".join(item.type for item in description.finding_types)
            or "None declared"
        )
        request_access = ", ".join(
            label
            for capability, label in _REQUEST_ACCESS_LABELS.items()
            if capability in description.capabilities
        )
        possible_result_labels = [
            label
            for capability, label in _MUTATION_CAPABILITY_LABELS.items()
            if capability in description.capabilities
        ]
        if description.finding_types:
            possible_result_labels.append("findings")
        possible_result_labels.extend(
            label
            for capability, label in _DECISION_CAPABILITY_LABELS.items()
            if capability in description.capabilities
        )
        possible_results = ", ".join(possible_result_labels)
        details = Table.grid(padding=(0, 2))
        details.add_column(style="bold cyan", no_wrap=True)
        details.add_column()
        details.add_row("Description", Text(description.description))
        details.add_row("Request access", Text(request_access or "None declared"))
        details.add_row(
            "Possible results",
            Text(possible_results or "None declared"),
        )
        details.add_row("Finding types", Text(finding_types))
        details.add_row("Python config", Text(description.config_type))
        if description.resource_type is not None:
            details.add_row("Python resources", Text(description.resource_type))
        _CONSOLE.print(
            Panel(
                details,
                title=Text(description.gate_type, style="bold green"),
                title_align="left",
                border_style="bright_blue",
            )
        )


def _render_registration(
    *,
    title: str,
    config_path: Path,
    name: str,
    endpoint: str | None = None,
    timeout_gateway_ceiling: str | None = None,
    change: str | None = None,
    next_step: str | None = None,
    status_style: str = "bold green",
) -> None:
    """Render one gateway registration outcome and its relevant values."""
    _CONSOLE.print(Text(title, style=status_style))
    details = Table.grid(padding=(0, 2))
    details.add_column(style="bold cyan", no_wrap=True)
    details.add_column(overflow="fold")
    details.add_row("Gateway file", Text(str(config_path)))
    details.add_row("Registration", Text(name))
    if endpoint is not None:
        details.add_row("Endpoint", Text(endpoint))
    if timeout_gateway_ceiling is not None:
        details.add_row(
            "Gateway RPC ceiling",
            Text(timeout_gateway_ceiling),
        )
    if change is not None:
        details.add_row("Change", Text(change))
    _CONSOLE.print(details)
    if next_step is not None:
        _CONSOLE.print(Text.assemble(("Next: ", "bold"), next_step))


def _render_gateway_registrations(
    config_path: Path,
    registrations: tuple[GatewayMiddlewareRegistration, ...],
) -> None:
    """Render the middleware names that can be passed to the remove command."""
    _CONSOLE.print("[bold]OpenShell middleware registrations[/bold]")
    _CONSOLE.print(Text.assemble(("Gateway file: ", "bold cyan"), str(config_path)))
    if not registrations:
        _CONSOLE.print("No middleware registrations found.")
        return

    table = Table(box=None, pad_edge=False, padding=(0, 2), header_style="bold cyan")
    table.add_column("Name", style="bold", no_wrap=True)
    table.add_column("Endpoint", overflow="fold")
    table.add_column("Gateway RPC ceiling", no_wrap=True)
    for registration in registrations:
        table.add_row(
            registration.name,
            registration.endpoint or "Not set",
            registration.timeout_gateway_ceiling or "Not set",
        )
    _CONSOLE.print(table)
    _CONSOLE.print(
        Text.assemble(
            ("Remove one: ", "bold"),
            "egress-gate remove-gateway-registration --name NAME",
        )
    )


def _render_egress_error(title: str, error: EgressGateError) -> None:
    """Render one cataloged error without internal component terminology."""
    _render_cli_error(
        title,
        code=error.code.value,
        message=error.summary,
        hint=error.hint,
    )


def _render_cli_error(
    title: str,
    *,
    code: str,
    message: str,
    hint: str | None = None,
) -> None:
    """Render a concise content-safe CLI failure."""
    heading = Text(title, style="bold red")
    heading.append(f" [{code}]", style="dim")
    _ERROR_CONSOLE.print(heading)
    _ERROR_CONSOLE.print(Text(message))
    if hint is not None:
        _ERROR_CONSOLE.print(Text.assemble(("Next: ", "bold"), hint))


_REQUEST_ACCESS_LABELS = {
    GateCapability.READ_TARGET: "target",
    GateCapability.READ_CONTEXT: "request context",
    GateCapability.READ_HEADERS: "headers",
    GateCapability.READ_BODY: "body",
}
_MUTATION_CAPABILITY_LABELS = {
    GateCapability.REPLACE_BODY: "body replacement",
    GateCapability.MUTATE_HEADERS: "header changes",
}
_DECISION_CAPABILITY_LABELS = {
    GateCapability.ALLOW: "allow decision",
    GateCapability.DENY: "deny decision",
}


def _load_yaml(path: Path) -> object:
    try:
        with path.open("rb") as source:
            contents = source.read(MAX_EVALUATION_FILE_BYTES + 1)
        if len(contents) > MAX_EVALUATION_FILE_BYTES:
            raise ValueError
        text = contents.decode("utf-8", errors="strict")
        return yaml.load(text, Loader=_StrictEvaluationLoader)
    except (
        OSError,
        RecursionError,
        UnicodeError,
        ValueError,
        yaml.YAMLError,
    ):
        raise _EvaluationCorpusError from None


def _compare_result(
    expected: _CorpusExpected,
    result: EgressResult,
) -> tuple[_FieldDifference, ...]:
    source = result.decision_source
    actual: dict[str, object] = {
        "decision": result.decision.value,
        "decision_source_kind": source.kind.value,
        "gate_name": source.gate_name
        if isinstance(source, GateDecisionSource)
        else None,
        "gate_type": source.gate_type
        if isinstance(source, GateDecisionSource)
        else None,
        "finding_types": tuple(item.finding.type for item in result.findings),
    }
    expected_values: dict[str, object] = {"decision": expected.decision}
    for field_name in (
        "decision_source_kind",
        "gate_name",
        "gate_type",
        "finding_types",
    ):
        if field_name in expected.model_fields_set:
            expected_values[field_name] = getattr(expected, field_name)

    differences: list[_FieldDifference] = []
    for field_name in (
        "decision",
        "decision_source_kind",
        "gate_name",
        "gate_type",
        "finding_types",
    ):
        if field_name not in expected_values:
            continue
        expected_value = expected_values[field_name]
        actual_value = actual[field_name]
        if expected_value != actual_value:
            differences.append(
                _FieldDifference(
                    field=field_name,
                    expected=expected_value,
                    actual=actual_value,
                )
            )
    return tuple(differences)


def _format_value(value: object) -> str:
    if isinstance(value, tuple):
        value = list(value)
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _load_registry(reference: str | None) -> GateRegistry:
    if reference is None:
        registry = create_builtin_registry()
        registry.configuration_json_schema()
        return registry
    module_name, separator, attribute_name = reference.partition(":")
    if not separator or not module_name or not attribute_name:
        raise typer.BadParameter(
            "Use MODULE:ATTRIBUTE, for example my_gates:registry.",
            param_hint="--registry",
        )
    working_directory = str(Path.cwd())
    if working_directory not in sys.path:
        sys.path.insert(0, working_directory)
    try:
        module = importlib.import_module(module_name)
    except Exception:
        raise typer.BadParameter(
            "Could not import the registry module. Check MODULE:ATTRIBUTE and the "
            "module's dependencies.",
            param_hint="--registry",
        ) from None
    try:
        candidate = getattr(module, attribute_name)
    except Exception:
        raise typer.BadParameter(
            "Could not find the registry attribute. Check the attribute name in "
            "MODULE:ATTRIBUTE.",
            param_hint="--registry",
        ) from None
    if isinstance(candidate, GateRegistry):
        registry = candidate
    elif callable(candidate):
        try:
            registry = candidate()
        except Exception:
            raise typer.BadParameter(
                "The registry factory raised an exception. Run it directly to inspect "
                "the startup failure.",
                param_hint="--registry",
            ) from None
    else:
        raise typer.BadParameter(
            "The registry attribute must be a GateRegistry or a zero-argument factory.",
            param_hint="--registry",
        )
    if not isinstance(registry, GateRegistry):
        raise typer.BadParameter(
            "The registry factory must return a GateRegistry.",
            param_hint="--registry",
        )
    try:
        registry.configuration_json_schema()
    except GateRegistryError:
        raise typer.BadParameter(
            "The registry could not prepare its policy schema. Register at least one "
            "valid gate before loading it.",
            param_hint="--registry",
        ) from None
    return registry


def _package_version() -> str:
    try:
        return version("egress-gate")
    except PackageNotFoundError:
        return "unknown"


def _command_options(context: typer.Context) -> _CommandOptions:
    options = context.obj
    if not isinstance(options, _CommandOptions):
        raise RuntimeError("Egress Gate command context is unavailable")
    return options


if __name__ == "__main__":
    app()


__all__ = ["app"]
