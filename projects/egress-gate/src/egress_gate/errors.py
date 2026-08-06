"""Content-safe failures shared across Egress Gate trust boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from egress_gate.constants import MAX_PROTO_CONFIG_BYTES, TIMEOUT_GATEWAY_CEILING


class ErrorKind(StrEnum):
    """Whether a failure is attributable to input or middleware internals."""

    INVALID_INPUT = "invalid_input"
    INTERNAL = "internal"


class ErrorComponent(StrEnum):
    """The Egress Gate component responsible for a failure."""

    CONFIG = "config"
    GATE = "gate"
    PROCESSOR = "processor"
    SERVICE = "service"
    SERVER = "server"


class ErrorCode(StrEnum):
    """Stable identifiers for cataloged production failures."""

    CONFIG_INVALID = "config_invalid"
    CONFIG_PREPARATION_FAILED = "config_preparation_failed"
    REQUEST_PROTOBUF_INVALID = "request_protobuf_invalid"
    REQUEST_PHASE_INVALID = "request_phase_invalid"
    REQUEST_ENVELOPE_INVALID = "request_envelope_invalid"
    REQUEST_BODY_TOO_LARGE = "request_body_too_large"
    BODY_ENCODING_INVALID = "body_encoding_invalid"
    GATE_OUTPUT_INVALID = "gate_output_invalid"
    GATE_EXECUTION_FAILED = "gate_execution_failed"
    SERVER_BIND_FAILED = "server_bind_failed"
    UNEXPECTED_SERVICE_FAILURE = "unexpected_service_failure"


class EgressGateError(Exception):
    """A catalog-only failure whose public representation is content-safe."""

    def __init__(self, code: ErrorCode) -> None:
        self.code = code
        self._spec = _ERROR_SPECS[code]
        super().__init__(str(self))

    @property
    def kind(self) -> ErrorKind:
        return self._spec.kind

    @property
    def component(self) -> ErrorComponent:
        return self._spec.component

    @property
    def operation(self) -> str:
        return self._spec.operation

    @property
    def summary(self) -> str:
        return self._spec.summary

    @property
    def hint(self) -> str:
        return self._spec.hint

    def __str__(self) -> str:
        return (
            f"[{self.code.value}] {self.component.value}.{self.operation}: "
            f"{self.summary} Hint: {self.hint}"
        )


class GateError(Exception):
    """Base for content-safe gate lifecycle failures."""


class GateConfigurationError(GateError):
    """A gate class or configured instance is invalid."""


class GateContractError(GateError):
    """A gate invocation or returned result violated the public contract."""


class GateExecutionError(GateError):
    """A gate's configured runtime failed to complete one request."""


class GateLimitExceededError(GateError):
    """A gate exceeded a bounded configuration or output limit."""


class GateInputError(GateError):
    """A gate could not interpret a bounded request input."""


class TimeoutExpiredError(Exception):
    """The shared request-processing timeout expired."""

    def __init__(self) -> None:
        super().__init__(
            "Egress Gate processing timed out. Reduce the request size or simplify "
            "the configured gates and rules, or increase egress-gate serve "
            f"--timeout to at most {TIMEOUT_GATEWAY_CEILING:g}s, then retry."
        )


class GateRegistryError(Exception):
    """A content-safe gate registration or registry lifecycle failure."""


@dataclass(frozen=True)
class _ErrorSpec:
    """Immutable, developer-authored classification and remediation text."""

    kind: ErrorKind
    component: ErrorComponent
    operation: str
    summary: str
    hint: str


_ERROR_SPECS: dict[ErrorCode, _ErrorSpec] = {
    ErrorCode.CONFIG_INVALID: _ErrorSpec(
        ErrorKind.INVALID_INPUT,
        ErrorComponent.CONFIG,
        "parse",
        "Policy configuration is invalid.",
        "Keep the encoded configuration at or below "
        f"{MAX_PROTO_CONFIG_BYTES // 1024} KiB, compare it with "
        "`egress-gate gates schema`, then check the gates, "
        "pattern catalogs, replacements, and default decision.",
    ),
    ErrorCode.CONFIG_PREPARATION_FAILED: _ErrorSpec(
        ErrorKind.INVALID_INPUT,
        ErrorComponent.CONFIG,
        "prepare",
        "A configured gate could not be prepared.",
        "Check the configured gate's rules and resources. For the built-in regex "
        "gate, remove named groups, inline flags, invalid expressions, and patterns "
        "that can match empty input, then retry.",
    ),
    ErrorCode.REQUEST_PROTOBUF_INVALID: _ErrorSpec(
        ErrorKind.INVALID_INPUT,
        ErrorComponent.SERVICE,
        "decode_protobuf",
        "Request protobuf encoding is invalid.",
        "Encode a complete request with the published OpenShell middleware "
        "protobuf contract, then retry.",
    ),
    ErrorCode.REQUEST_PHASE_INVALID: _ErrorSpec(
        ErrorKind.INVALID_INPUT,
        ErrorComponent.SERVICE,
        "validate_phase",
        "Request evaluation phase is invalid.",
        "Use the advertised pre-credentials phase.",
    ),
    ErrorCode.REQUEST_ENVELOPE_INVALID: _ErrorSpec(
        ErrorKind.INVALID_INPUT,
        ErrorComponent.SERVICE,
        "validate_envelope",
        "Request transport metadata exceeds an advertised limit.",
        "Reduce the request context, target, or headers to the OpenShell "
        "middleware contract limits.",
    ),
    ErrorCode.REQUEST_BODY_TOO_LARGE: _ErrorSpec(
        ErrorKind.INVALID_INPUT,
        ErrorComponent.SERVICE,
        "validate_body_size",
        "Request body exceeds the advertised size limit.",
        "Reduce the request body to the maximum size in the middleware manifest.",
    ),
    ErrorCode.BODY_ENCODING_INVALID: _ErrorSpec(
        ErrorKind.INVALID_INPUT,
        ErrorComponent.SERVICE,
        "decode_text",
        "Request body encoding is invalid.",
        "Supply a valid UTF-8 request body.",
    ),
    ErrorCode.GATE_OUTPUT_INVALID: _ErrorSpec(
        ErrorKind.INTERNAL,
        ErrorComponent.PROCESSOR,
        "validate_gate",
        "A gate returned an invalid result.",
        "Gate authors should check the evaluate contract, capabilities, findings, "
        "mutations, and output limits.",
    ),
    ErrorCode.GATE_EXECUTION_FAILED: _ErrorSpec(
        ErrorKind.INTERNAL,
        ErrorComponent.GATE,
        "evaluate",
        "A configured gate failed.",
        "Check the request ID and error code in service logs, then run the "
        "configured gate's focused configuration and request tests.",
    ),
    ErrorCode.SERVER_BIND_FAILED: _ErrorSpec(
        ErrorKind.INTERNAL,
        ErrorComponent.SERVER,
        "start",
        "Server could not start on its listen address.",
        "Choose an available listen address and port, then retry.",
    ),
    ErrorCode.UNEXPECTED_SERVICE_FAILURE: _ErrorSpec(
        ErrorKind.INTERNAL,
        ErrorComponent.SERVICE,
        "evaluate_http_request",
        "The middleware encountered an unexpected failure.",
        "Retry once; if it recurs, report the request ID and error code from the "
        "service log.",
    ),
}
