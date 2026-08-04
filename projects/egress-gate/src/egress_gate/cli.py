"""Egress Gate command-line application."""

from __future__ import annotations

import base64
import binascii
import importlib
import ipaddress
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal, Self

import typer
import yaml
from pydantic import ValidationError, field_validator, model_validator
from yaml.constructor import ConstructorError
from yaml.events import AliasEvent
from yaml.nodes import MappingNode
from yaml.resolver import BaseResolver

from egress_gate.base import StrictDomainModel
from egress_gate.constants import (
    DEFAULT_TIMEOUT_SECONDS,
    MAX_BODY_BYTES,
    MAX_EVALUATION_CASE_NAME_BYTES,
    MAX_EVALUATION_CASES,
    MAX_EVALUATION_FILE_BYTES,
    MAX_EVALUATION_TAGS,
    MAX_PROTO_FINDING_GROUPS,
    MAX_TIMEOUT_SECONDS,
)
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
from egress_gate.request import HttpHeader, HttpRequest, HttpTarget, RequestContext
from egress_gate.result import EgressResult
from egress_gate.string_validators import BoundedMetadataString
from egress_gate.timeout import Timeout, validate_timeout_seconds

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
    ] = "127.0.0.1:50051",
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
    from egress_gate.service.server import EgressGateServer

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


@app.command("validate")
def validate_policy(
    context: typer.Context,
    policy: Annotated[
        Path,
        typer.Option(
            "--policy",
            help="Strict YAML pipeline policy to validate without preparing gates.",
        ),
    ],
) -> None:
    """Validate policy configuration and registered resources without side effects."""
    options = _command_options(context)
    try:
        values = _load_policy(policy)
        options.registry.validate_config(values)
    except _EvaluationCorpusError:
        typer.echo("VALIDATE_ERROR invalid_input", err=True)
        raise typer.Exit(code=1) from None
    except EgressGateError:
        typer.echo("VALIDATE_ERROR config_invalid", err=True)
        raise typer.Exit(code=1) from None
    typer.echo("VALID")


@app.command("evaluate")
def evaluate(
    context: typer.Context,
    policy: Annotated[
        Path,
        typer.Option(
            "--policy",
            help="Strict YAML pipeline policy to prepare and evaluate.",
        ),
    ],
    cases: Annotated[
        Path,
        typer.Option(
            "--cases",
            help="Strict YAML version-one evaluation corpus.",
        ),
    ],
    timeout_seconds: Annotated[
        float,
        typer.Option(
            help=(
                "Maximum seconds for preparation and each case; "
                f"must be greater than 0 and at most {MAX_TIMEOUT_SECONDS:g}."
            ),
        ),
    ] = DEFAULT_TIMEOUT_SECONDS,
) -> None:
    """Evaluate a policy corpus offline through the production processor."""
    options = _command_options(context)
    try:
        validated_timeout_seconds = validate_timeout_seconds(timeout_seconds)
        policy_values = _load_policy(policy)
        corpus = _load_corpus(cases)
        summary = _run_corpus(
            options.registry,
            policy_values,
            corpus,
            timeout_seconds=validated_timeout_seconds,
        )
    except _EvaluationCorpusError:
        typer.echo("EVALUATE_ERROR invalid_input", err=True)
        raise typer.Exit(code=2) from None
    except EgressGateError:
        typer.echo("EVALUATE_ERROR egress_gate_failure", err=True)
        raise typer.Exit(code=2) from None
    except Exception:
        typer.echo("EVALUATE_ERROR execution_failed", err=True)
        raise typer.Exit(code=2) from None

    for case in summary.cases:
        for line in _format_case_evaluation(case):
            typer.echo(line)
    typer.echo(_format_summary(summary))
    if summary.failed:
        raise typer.Exit(code=1)


@app.command("gates")
def gates(context: typer.Context) -> None:
    """List installed gates, capabilities, and declared finding types."""
    for description in _command_options(context).registry.describe_gates():
        finding_types = ",".join(item.type for item in description.finding_types)
        capabilities = ",".join(
            name
            for name, enabled in description.capabilities.model_dump().items()
            if enabled
        )
        typer.echo(
            f"{description.gate_type}\tfindings={finding_types or '-'}\t"
            f"capabilities={capabilities or '-'}\t"
            f"resources={description.resource_type or '-'}\t"
            f"config={description.config_type}\t{description.description}"
        )


_LOGGER = get_logger(__name__)


@dataclass(frozen=True)
class _CommandOptions:
    registry: GateRegistry
    log_request_content: bool


class _EvaluationCorpusError(Exception):
    """A content-safe offline policy or corpus input failure."""


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
    """Load one bounded strict YAML pipeline policy."""
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
    validated_timeout = validate_timeout_seconds(timeout_seconds)
    validated_config = registry.validate_config(policy_values)
    processor = registry.prepare_processor(
        validated_config,
        timeout=Timeout.from_seconds(validated_timeout),
        log_request_content=False,
    )
    evaluations: list[_CaseEvaluation] = []
    for case in corpus.cases:
        result = processor.process(
            case.request.to_http_request(),
            timeout=Timeout.from_seconds(validated_timeout),
        )
        evaluations.append(
            _CaseEvaluation(
                name=case.name,
                differences=_compare_result(case.expected, result),
            )
        )
    return _EvaluationSummary(cases=tuple(evaluations))


def _format_case_evaluation(evaluation: _CaseEvaluation) -> tuple[str, ...]:
    """Render one content-safe case result as stable text lines."""
    if evaluation.matched:
        return (f"PASS case={_format_value(evaluation.name)}",)
    return tuple(
        "FAIL "
        f"case={_format_value(evaluation.name)} "
        f"field={difference.field} "
        f"expected={_format_value(difference.expected)} "
        f"actual={_format_value(difference.actual)}"
        for difference in evaluation.differences
    )


def _format_summary(summary: _EvaluationSummary) -> str:
    """Render the stable aggregate line for one corpus run."""
    return (
        f"SUMMARY total={summary.total} passed={summary.passed} failed={summary.failed}"
    )


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
    actual: dict[str, object] = {
        "decision": result.decision.value,
        "decision_source_kind": result.decision_source.kind.value,
        "gate_name": result.decision_source.gate_name,
        "gate_type": result.decision_source.gate_type,
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
