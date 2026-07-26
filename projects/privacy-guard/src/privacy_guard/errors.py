"""Content-safe failures shared across Privacy Guard trust boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from privacy_guard.constants import MAX_TIMEOUT_SECONDS


class ErrorKind(StrEnum):
    """Whether a failure is attributable to input or middleware internals."""

    INVALID_INPUT = "invalid_input"
    INTERNAL = "internal"


class ErrorComponent(StrEnum):
    """The Privacy Guard component responsible for a failure."""

    CONFIG = "config"
    ENGINE = "engine"
    PROCESSOR = "processor"
    SERVICE = "service"
    SERVER = "server"


class ErrorCode(StrEnum):
    """Stable identifiers for cataloged production failures."""

    CONFIG_INVALID = "config_invalid"
    REQUEST_PHASE_INVALID = "request_phase_invalid"
    REQUEST_BODY_TOO_LARGE = "request_body_too_large"
    BODY_ENCODING_INVALID = "body_encoding_invalid"
    ENGINE_OUTPUT_INVALID = "engine_output_invalid"
    ENGINE_EXECUTION_FAILED = "engine_execution_failed"
    SERVER_BIND_FAILED = "server_bind_failed"
    UNEXPECTED_SERVICE_FAILURE = "unexpected_service_failure"


@dataclass(frozen=True)
class _ErrorSpec:
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

    def __str__(self) -> str:
        return (
            f"[{self.code.value}] {self.component.value}.{self.operation}: "
            f"{self.summary} Hint: {self.hint}"
        )


class EntityProcessingError(Exception):
    """Base for content-safe entity-processing failures."""


class EngineConfigurationError(EntityProcessingError):
    """An engine class or configured instance is invalid."""


class EngineContractError(EntityProcessingError):
    """An engine invocation or returned result violated the public contract."""


class EngineExecutionError(EntityProcessingError):
    """An engine's configured runtime failed to complete one text input."""


class EngineLimitExceededError(EntityProcessingError):
    """An engine exceeded a bounded configuration or output limit."""


class TimeoutExpiredError(EntityProcessingError):
    """The shared entity-processing timeout expired."""

    def __init__(self) -> None:
        super().__init__(
            "Privacy Guard processing timed out. Reduce the request size or simplify "
            "the configured stages and patterns, or increase the processing timeout "
            f"to at most {MAX_TIMEOUT_SECONDS:g} seconds, then retry."
        )


class EngineRegistryError(Exception):
    """A content-safe engine registration or registry lifecycle failure."""


_ERROR_SPECS: dict[ErrorCode, _ErrorSpec] = {
    ErrorCode.CONFIG_INVALID: _ErrorSpec(
        ErrorKind.INVALID_INPUT,
        ErrorComponent.CONFIG,
        "parse",
        "Policy configuration is invalid.",
        "Check entity-processing stages, engine configuration, pattern catalogs, "
        "replacement recipes, and the on-detection action.",
    ),
    ErrorCode.REQUEST_PHASE_INVALID: _ErrorSpec(
        ErrorKind.INVALID_INPUT,
        ErrorComponent.SERVICE,
        "validate_phase",
        "Request evaluation phase is invalid.",
        "Use the advertised pre-credentials phase.",
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
    ErrorCode.ENGINE_OUTPUT_INVALID: _ErrorSpec(
        ErrorKind.INTERNAL,
        ErrorComponent.PROCESSOR,
        "validate_engine",
        "An entity-processing engine returned an invalid result.",
        "Check the engine run contract, result model, spans, strategy, and limits.",
    ),
    ErrorCode.ENGINE_EXECUTION_FAILED: _ErrorSpec(
        ErrorKind.INTERNAL,
        ErrorComponent.ENGINE,
        "run",
        "An entity-processing engine failed.",
        "Run the engine's focused configuration and single-text tests.",
    ),
    ErrorCode.SERVER_BIND_FAILED: _ErrorSpec(
        ErrorKind.INTERNAL,
        ErrorComponent.SERVER,
        "bind",
        "Server could not bind its listen address.",
        "Check the listen address and port availability.",
    ),
    ErrorCode.UNEXPECTED_SERVICE_FAILURE: _ErrorSpec(
        ErrorKind.INTERNAL,
        ErrorComponent.SERVICE,
        "evaluate_http_request",
        "The middleware encountered an unexpected failure.",
        "Reproduce with focused service and processor tests.",
    ),
}
