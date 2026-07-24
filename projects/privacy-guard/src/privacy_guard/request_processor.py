"""Sequential entity-processing orchestration for one text input."""

from __future__ import annotations

import logging
import math
from collections import OrderedDict
from collections.abc import Sequence
from enum import StrEnum
from typing import Protocol

from pydantic import Field

from privacy_guard.base import StrictDomainModel
from privacy_guard.config import FinalizedPrivacyGuardConfig, PolicyAction
from privacy_guard.constants import (
    BLOCK_REASON_CODE,
    DEFAULT_TIMEOUT_SECONDS,
    LIMIT_REASON_CODE,
    MAX_BODY_BYTES,
    MAX_DETECTIONS_PER_REQUEST,
    MAX_SCANNED_CHARACTERS,
    MAX_TIMEOUT_SECONDS,
)
from privacy_guard.engines import (
    DetectionConfidence,
    EntityProcessingStrategy,
    TextProcessingResult,
)
from privacy_guard.errors import (
    EngineContractError,
    EngineLimitExceeded,
    EntityProcessingError,
    ErrorCode,
    PrivacyGuardError,
)
from privacy_guard.string_validators import validate_scalar_string
from privacy_guard.timeout import Timeout, TimeoutExpired


class RequestDecision(StrEnum):
    """Whether OpenShell should continue or stop the request."""

    ALLOW = "allow"
    DENY = "deny"


class EntityDetectionSummary(StrictDomainModel):
    """One bounded aggregate suitable for user-facing audit output."""

    entity: str
    source_stage: str
    confidence: DetectionConfidence | None = None
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
        config: FinalizedPrivacyGuardConfig,
        stages: Sequence[tuple[str, _RunnableEngine]],
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        log_request_content: bool = False,
    ) -> None:
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, int | float)
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
            or timeout_seconds > MAX_TIMEOUT_SECONDS
        ):
            raise ValueError("timeout must be finite, positive, and bounded")
        configured_stages = tuple(stages)
        if len(configured_stages) != len(config.entity_processing.stages):
            raise ValueError("configured stages do not match the policy")
        if not configured_stages:
            raise ValueError("at least one configured stage is required")
        sources = tuple(source for source, _ in configured_stages)
        if any(not source for source in sources) or len(sources) != len(set(sources)):
            raise ValueError("stage sources must be non-empty and unique")
        self._config = config
        self._stages = configured_stages
        self._timeout_seconds = float(timeout_seconds)
        self._log_request_content = log_request_content

    @property
    def config(self) -> FinalizedPrivacyGuardConfig:
        """Return the exact validated configuration retained by this processor."""
        return self._config

    def process(self, text: str) -> RequestProcessingResult:
        """Process one complete request text and apply the user-facing action."""
        try:
            input_text = validate_scalar_string(text)
        except ValueError:
            raise PrivacyGuardError(ErrorCode.BODY_ENCODING_INVALID) from None
        if (
            len(input_text) > MAX_SCANNED_CHARACTERS
            or len(input_text.encode("utf-8")) > MAX_BODY_BYTES
        ):
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
            for source, engine in self._stages:
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
                if (
                    len(result.text) > MAX_SCANNED_CHARACTERS
                    or len(result.text.encode("utf-8")) > MAX_BODY_BYTES
                ):
                    raise EngineLimitExceeded("intermediate text exceeds the limit")
                if (
                    sum(len(item.detections) for _, item in stage_results)
                    + len(result.detections)
                    > MAX_DETECTIONS_PER_REQUEST
                ):
                    raise EngineLimitExceeded("request detections exceed the limit")
                stage_results.append((source, result))
                current_text = result.text
            timeout.raise_if_expired()
        except (EngineLimitExceeded, TimeoutExpired):
            return RequestProcessingResult(
                decision=RequestDecision.DENY,
                reason_code=LIMIT_REASON_CODE,
            )
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
    groups: OrderedDict[
        tuple[str, str, DetectionConfidence | None],
        int,
    ] = OrderedDict()
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


class _RunnableEngine(Protocol):
    """The engine behavior needed by request orchestration."""

    def run(
        self,
        text: str,
        *,
        strategy: EntityProcessingStrategy,
        timeout: Timeout,
    ) -> TextProcessingResult: ...


_LOGGER = logging.getLogger(__name__)


__all__ = [
    "EntityDetectionSummary",
    "RequestDecision",
    "RequestProcessingResult",
    "RequestProcessor",
]
