"""Content-safe failures shared across Privacy Guard trust boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from typing_extensions import override


class ErrorKind(StrEnum):
    """Whether a failure is attributable to input or middleware internals."""

    INVALID_INPUT = "invalid_input"
    INTERNAL = "internal"


class ErrorComponent(StrEnum):
    """The Privacy Guard component responsible for a failure."""

    CONFIG = "config"
    FORMAT_HANDLER = "format_handler"
    SCANNER = "scanner"
    PROCESSOR = "processor"
    SERVICE = "service"
    SERVER = "server"


class ErrorCode(StrEnum):
    """Stable identifiers for cataloged production failures."""

    CONFIG_INVALID = "config_invalid"
    REQUEST_PHASE_INVALID = "request_phase_invalid"
    REQUEST_BODY_TOO_LARGE = "request_body_too_large"
    REQUEST_SHAPE_LIMIT_EXCEEDED = "request_shape_limit_exceeded"
    BODY_FORMAT_UNSUPPORTED = "body_format_unsupported"
    BODY_ENCODING_INVALID = "body_encoding_invalid"
    BODY_JSON_INVALID = "body_json_invalid"
    BODY_RECONSTRUCTION_INVALID = "body_reconstruction_invalid"
    FORMAT_HANDLER_OUTPUT_INVALID = "format_handler_output_invalid"
    FORMAT_HANDLER_EXECUTION_FAILED = "format_handler_execution_failed"
    SCANNER_OUTPUT_INVALID = "scanner_output_invalid"
    SCANNER_EXECUTION_FAILED = "scanner_execution_failed"
    FINDING_LIMIT_EXCEEDED = "finding_limit_exceeded"
    RESULT_LIMIT_EXCEEDED = "result_limit_exceeded"
    SERVER_BIND_FAILED = "server_bind_failed"
    UNEXPECTED_SERVICE_FAILURE = "unexpected_service_failure"


@dataclass(frozen=True)
class ErrorSpec:
    """Immutable, developer-authored classification and remediation text."""

    kind: ErrorKind
    component: ErrorComponent
    operation: str
    summary: str
    hint: str


class PrivacyGuardError(Exception):
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

    @override
    def __str__(self) -> str:
        return (
            f"[{self.code.value}] {self.component.value}.{self.operation}: "
            f"{self.summary} Hint: {self.hint}"
        )


_ERROR_SPECS: dict[ErrorCode, ErrorSpec] = {
    ErrorCode.CONFIG_INVALID: ErrorSpec(
        ErrorKind.INVALID_INPUT,
        ErrorComponent.CONFIG,
        "parse",
        "Policy configuration is invalid.",
        "Check allowed fields, strict string types, finding action, confidence, "
        "entity filters, and redact template syntax.",
    ),
    ErrorCode.REQUEST_PHASE_INVALID: ErrorSpec(
        ErrorKind.INVALID_INPUT,
        ErrorComponent.SERVICE,
        "validate_phase",
        "Request evaluation phase is invalid.",
        "Use the advertised pre-credentials phase.",
    ),
    ErrorCode.REQUEST_BODY_TOO_LARGE: ErrorSpec(
        ErrorKind.INVALID_INPUT,
        ErrorComponent.SERVICE,
        "validate_body_size",
        "Request body exceeds the advertised size limit.",
        "Reduce the request body to the maximum size in the middleware manifest.",
    ),
    ErrorCode.REQUEST_SHAPE_LIMIT_EXCEEDED: ErrorSpec(
        ErrorKind.INVALID_INPUT,
        ErrorComponent.FORMAT_HANDLER,
        "normalize",
        "Request body exceeds a structural scanning limit.",
        "Reduce JSON nesting, text fields, or total text content.",
    ),
    ErrorCode.BODY_FORMAT_UNSUPPORTED: ErrorSpec(
        ErrorKind.INVALID_INPUT,
        ErrorComponent.FORMAT_HANDLER,
        "select",
        "Request body format is unsupported.",
        "Register or select a supported body format.",
    ),
    ErrorCode.BODY_ENCODING_INVALID: ErrorSpec(
        ErrorKind.INVALID_INPUT,
        ErrorComponent.FORMAT_HANDLER,
        "normalize",
        "Request body encoding is invalid.",
        "Supply a valid UTF-8 request body.",
    ),
    ErrorCode.BODY_JSON_INVALID: ErrorSpec(
        ErrorKind.INVALID_INPUT,
        ErrorComponent.FORMAT_HANDLER,
        "normalize",
        "Request body is not valid JSON.",
        "Check UTF-8 JSON syntax, duplicate keys, non-finite numbers, Unicode "
        "scalars, and the configured body format.",
    ),
    ErrorCode.BODY_RECONSTRUCTION_INVALID: ErrorSpec(
        ErrorKind.INTERNAL,
        ErrorComponent.FORMAT_HANDLER,
        "reconstruct",
        "Request body reconstruction failed validation.",
        "Check processor-generated paths and replacement text.",
    ),
    ErrorCode.FORMAT_HANDLER_OUTPUT_INVALID: ErrorSpec(
        ErrorKind.INTERNAL,
        ErrorComponent.PROCESSOR,
        "validate_handler",
        "Format handler output is invalid.",
        "Check the FormatHandler ABC and normalized-body contract.",
    ),
    ErrorCode.FORMAT_HANDLER_EXECUTION_FAILED: ErrorSpec(
        ErrorKind.INTERNAL,
        ErrorComponent.FORMAT_HANDLER,
        "call",
        "Format handler execution failed.",
        "Run handler unit tests for normalization and reconstruction.",
    ),
    ErrorCode.SCANNER_OUTPUT_INVALID: ErrorSpec(
        ErrorKind.INTERNAL,
        ErrorComponent.PROCESSOR,
        "validate_scanner",
        "Scanner output is invalid.",
        "Check scanner identity, supported entities, result tuple, labels, spans, "
        "confidence, and paths.",
    ),
    ErrorCode.SCANNER_EXECUTION_FAILED: ErrorSpec(
        ErrorKind.INTERNAL,
        ErrorComponent.SCANNER,
        "scan",
        "Scanner execution failed.",
        "Run the scanner single-block unit tests.",
    ),
    ErrorCode.FINDING_LIMIT_EXCEEDED: ErrorSpec(
        ErrorKind.INTERNAL,
        ErrorComponent.PROCESSOR,
        "scan",
        "Scanner finding limit was exceeded.",
        "Tune scanner cardinality or the policy before enabling traffic.",
    ),
    ErrorCode.RESULT_LIMIT_EXCEEDED: ErrorSpec(
        ErrorKind.INTERNAL,
        ErrorComponent.SERVICE,
        "serialize_result",
        "A safe middleware result could not be represented.",
        "Tune redaction size or scanner finding cardinality.",
    ),
    ErrorCode.SERVER_BIND_FAILED: ErrorSpec(
        ErrorKind.INTERNAL,
        ErrorComponent.SERVER,
        "bind",
        "Server could not bind its listen address.",
        "Check the listen address and port availability.",
    ),
    ErrorCode.UNEXPECTED_SERVICE_FAILURE: ErrorSpec(
        ErrorKind.INTERNAL,
        ErrorComponent.SERVICE,
        "evaluate_http_request",
        "The middleware encountered an unexpected failure.",
        "Reproduce with focused service and processor tests.",
    ),
}
