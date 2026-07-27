"""Sequential entity-processing orchestration for one text input."""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum

from pydantic import Field

from privacy_guard.base import StrictDomainModel
from privacy_guard.config import PolicyAction, PrivacyGuardConfig
from privacy_guard.constants import (
    BLOCK_REASON_CODE,
    DEFAULT_TIMEOUT_SECONDS,
    LIMIT_REASON_CODE,
    MAX_BODY_BYTES,
    MAX_DETECTIONS_PER_REQUEST,
)
from privacy_guard.engines import (
    ConfidenceLevel,
    EngineConfig,
    EngineResources,
    EntityProcessingEngine,
    EntityProcessingStrategy,
    TextProcessingResult,
)
from privacy_guard.errors import (
    EngineConfigurationError,
    EngineContractError,
    EngineLimitExceededError,
    EntityProcessingError,
    ErrorCode,
    PrivacyGuardError,
    TimeoutExpiredError,
)
from privacy_guard.logging import get_logger
from privacy_guard.string_validators import validate_scalar_string
from privacy_guard.timeout import Timeout, validate_timeout_seconds


class RequestDecision(StrEnum):
    """Whether OpenShell should continue or stop the request."""

    ALLOW = "allow"
    DENY = "deny"


class EntityDetectionSummary(StrictDomainModel):
    """One bounded aggregate suitable for user-facing audit output."""

    entity: str
    source_stage: str
    confidence: ConfidenceLevel | None = None
    count: int = Field(ge=1)


class RequestProcessingResult(StrictDomainModel):
    """The processor's decision, summaries, and optional replacement text."""

    decision: RequestDecision
    replacement_text: str | None = Field(default=None, repr=False)
    detection_summaries: tuple[EntityDetectionSummary, ...] = ()
    reason_code: str | None = None


class RequestProcessor:
    """Run configured entity-processing stages once, in policy order."""

    def __init__(
        self,
        config: PrivacyGuardConfig[EngineConfig],
        configured_engines: Sequence[
            tuple[
                str,
                EntityProcessingEngine[EngineConfig, EngineResources | None],
            ]
        ],
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        log_request_content: bool = False,
    ) -> None:
        engines = tuple(configured_engines)
        if len(engines) != len(config.entity_processing.stages):
            raise ValueError("configured engines do not match the policy")
        if not engines:
            raise ValueError("at least one configured engine is required")
        sources = tuple(source for source, _ in engines)
        if any(not source for source in sources) or len(sources) != len(set(sources)):
            raise ValueError("engine sources must be non-empty and unique")
        self._config = config
        self._engines = engines
        self._timeout_seconds = validate_timeout_seconds(timeout_seconds)
        self._log_request_content = log_request_content

    def process(self, text: str) -> RequestProcessingResult:
        """Process one complete request text and apply the user-facing action."""
        try:
            input_text = validate_scalar_string(text)
        except ValueError:
            raise PrivacyGuardError(ErrorCode.BODY_ENCODING_INVALID) from None
        if len(input_text.encode("utf-8")) > MAX_BODY_BYTES:
            raise PrivacyGuardError(ErrorCode.REQUEST_BODY_TOO_LARGE)
        if self._log_request_content:
            _LOGGER.debug("privacy_guard_text_input text=%r", input_text)

        action = self._config.on_detection.action
        strategy = (
            EntityProcessingStrategy.REPLACE
            if action is PolicyAction.REPLACE
            else EntityProcessingStrategy.DETECT
        )
        timeout = Timeout.from_seconds(self._timeout_seconds)
        current_text = input_text
        stage_results: list[tuple[str, TextProcessingResult]] = []
        try:
            for source, engine in self._engines:
                _LOGGER.debug(
                    "privacy_guard_stage_run source=%s strategy=%s",
                    source,
                    strategy.value,
                )
                result = engine.run(
                    current_text,
                    strategy=strategy,
                    timeout=timeout,
                )
                if len(result.text.encode("utf-8")) > MAX_BODY_BYTES:
                    raise EngineLimitExceededError(
                        "intermediate text exceeds the limit"
                    )
                if (
                    sum(len(item.detections) for _, item in stage_results)
                    + len(result.detections)
                    > MAX_DETECTIONS_PER_REQUEST
                ):
                    raise EngineLimitExceededError(
                        "request detections exceed the limit"
                    )
                stage_results.append((source, result))
                current_text = result.text
            timeout.raise_if_expired()
        except TimeoutExpiredError:
            _LOGGER.info("privacy_guard_processing_limit kind=timeout")
            return RequestProcessingResult(
                decision=RequestDecision.DENY,
                reason_code=LIMIT_REASON_CODE,
            )
        except EngineLimitExceededError:
            _LOGGER.info("privacy_guard_processing_limit kind=resource")
            return RequestProcessingResult(
                decision=RequestDecision.DENY,
                reason_code=LIMIT_REASON_CODE,
            )
        except EngineConfigurationError:
            raise PrivacyGuardError(ErrorCode.CONFIG_INVALID) from None
        except EngineContractError:
            raise PrivacyGuardError(ErrorCode.ENGINE_OUTPUT_INVALID) from None
        except EntityProcessingError:
            raise PrivacyGuardError(ErrorCode.ENGINE_EXECUTION_FAILED) from None
        except PrivacyGuardError:
            raise
        except Exception:
            raise PrivacyGuardError(ErrorCode.ENGINE_EXECUTION_FAILED) from None

        detections = _aggregate_detections(stage_results)
        if action is PolicyAction.BLOCK and detections:
            return RequestProcessingResult(
                decision=RequestDecision.DENY,
                detection_summaries=detections,
                reason_code=BLOCK_REASON_CODE,
            )
        replacement_text = current_text if action is PolicyAction.REPLACE else None
        if self._log_request_content:
            _LOGGER.debug("privacy_guard_text_output text=%r", current_text)
        return RequestProcessingResult(
            decision=RequestDecision.ALLOW,
            replacement_text=replacement_text,
            detection_summaries=detections,
        )


def _aggregate_detections(
    stage_results: Sequence[tuple[str, TextProcessingResult]],
) -> tuple[EntityDetectionSummary, ...]:
    groups: dict[
        tuple[str, str, ConfidenceLevel | None],
        int,
    ] = {}
    for source, result in stage_results:
        for detection in result.detections:
            key = (source, detection.entity, detection.confidence)
            groups[key] = groups.get(key, 0) + 1
    return tuple(
        EntityDetectionSummary(
            source_stage=source,
            entity=entity,
            confidence=confidence,
            count=count,
        )
        for (source, entity, confidence), count in groups.items()
    )


_LOGGER = get_logger(__name__)


__all__ = [
    "EntityDetectionSummary",
    "RequestDecision",
    "RequestProcessingResult",
    "RequestProcessor",
]
