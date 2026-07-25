from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from time import monotonic
from typing import Literal

import pytest
from pydantic import ValidationError

from privacy_guard.base import StrictDomainModel
from privacy_guard.engines import (
    ConfidenceLevel,
    EngineConfig,
    EngineContractError,
    EngineLimitExceeded,
    EngineResources,
    EntityDetection,
    EntityProcessingEngine,
    EntityProcessingStrategy,
    TextProcessingResult,
)
from privacy_guard.timeout import Timeout, TimeoutExpired


class _Replacement(StrictDomainModel):
    strategy: Literal["token"] = "token"


class _Config(EngineConfig):
    engine: Literal["test"] = "test"
    replacement: _Replacement | None = None


@dataclass(frozen=True)
class _Resources(EngineResources):
    prefix: str


class _CustomEngine(EntityProcessingEngine[_Config, _Resources]):
    supported_strategies = frozenset(
        {
            EntityProcessingStrategy.DETECT,
            EntityProcessingStrategy.REPLACE,
        }
    )

    def _run(
        self,
        text: str,
        *,
        strategy: EntityProcessingStrategy,
        timeout: Timeout,
    ) -> TextProcessingResult:
        detection = EntityDetection(
            entity="token",
            start=0,
            end=len(text),
            confidence=ConfidenceLevel.HIGH,
            metadata={"provider": "custom"},
        )
        output = (
            f"{self.resources.prefix}token"
            if strategy is EntityProcessingStrategy.REPLACE
            else text
        )
        return TextProcessingResult(text=output, detections=(detection,))


def test_custom_engine_infers_types_and_needs_no_custom_init() -> None:
    config = _Config(replacement=_Replacement())
    resources = _Resources(prefix="[")

    engine = _CustomEngine(config, resources)

    assert _CustomEngine.get_config_type() is _Config
    assert _CustomEngine.get_resources_type() is _Resources
    assert engine.config is config
    assert engine.resources is resources
    assert (
        engine.run(
            "secret",
            strategy=EntityProcessingStrategy.DETECT,
            timeout=Timeout.from_seconds(1),
        ).text
        == "secret"
    )
    assert (
        engine.run(
            "secret",
            strategy=EntityProcessingStrategy.REPLACE,
            timeout=Timeout.from_seconds(1),
        ).text
        == "[token"
    )


def test_detection_confidence_and_metadata_are_strict_bounded_values() -> None:
    categorical = EntityDetection.model_validate(
        {
            "entity": "email",
            "start": 0,
            "end": 1,
            "confidence": "high",
            "metadata": {"pattern": "email.patterns[0]"},
        }
    )
    assert categorical.confidence is ConfidenceLevel.HIGH
    assert type(categorical.metadata).__name__ == "mappingproxy"
    with pytest.raises(ValidationError):
        EntityDetection.model_validate(
            {
                "entity": "email",
                "start": 0,
                "end": 1,
                "confidence": 0.25,
            }
        )


def test_processing_result_bounds_a_lazy_detection_stream() -> None:
    produced = 0

    def detections() -> Iterator[EntityDetection]:
        nonlocal produced
        for index in range(1_000):
            produced += 1
            yield EntityDetection(entity="token", start=index, end=index + 1)

    with pytest.raises(EngineLimitExceeded):
        TextProcessingResult.from_detections(
            text="x" * 1_000,
            detections=detections(),
        )

    assert produced == 257


class _DetectOnlyEngine(EntityProcessingEngine[_Config]):
    supported_strategies = frozenset({EntityProcessingStrategy.DETECT})

    def _run(
        self,
        text: str,
        *,
        strategy: EntityProcessingStrategy,
        timeout: Timeout,
    ) -> TextProcessingResult:
        return TextProcessingResult(text=text, detections=())


def test_detect_only_engine_rejects_replacement_before_running() -> None:
    engine = _DetectOnlyEngine(_Config(), None)

    assert _DetectOnlyEngine.get_resources_type() is None
    assert engine.resources is None
    with pytest.raises(EngineContractError):
        engine.run(
            "text",
            strategy=EntityProcessingStrategy.REPLACE,
            timeout=Timeout.from_seconds(1),
        )


class _ReplaceOnlyEngine(EntityProcessingEngine[_Config]):
    supported_strategies = frozenset({EntityProcessingStrategy.REPLACE})

    def _run(
        self,
        text: str,
        *,
        strategy: EntityProcessingStrategy,
        timeout: Timeout,
    ) -> TextProcessingResult:
        del strategy, timeout
        return TextProcessingResult(text=text, detections=())


def test_replace_only_engine_rejects_detection_before_running() -> None:
    engine = _ReplaceOnlyEngine(_Config(replacement=_Replacement()), None)

    with pytest.raises(EngineContractError):
        engine.run(
            "text",
            strategy=EntityProcessingStrategy.DETECT,
            timeout=Timeout.from_seconds(1),
        )


class _MutatingDetectEngine(EntityProcessingEngine[_Config]):
    supported_strategies = frozenset(
        {
            EntityProcessingStrategy.DETECT,
            EntityProcessingStrategy.REPLACE,
        }
    )

    def _run(
        self,
        text: str,
        *,
        strategy: EntityProcessingStrategy,
        timeout: Timeout,
    ) -> TextProcessingResult:
        return TextProcessingResult(
            text="changed",
            detections=(EntityDetection(entity="token", start=0, end=len(text)),),
        )


def test_detection_strategy_rejects_mutated_text() -> None:
    engine = _MutatingDetectEngine(_Config(), None)

    with pytest.raises(EngineContractError):
        engine.run(
            "text",
            strategy=EntityProcessingStrategy.DETECT,
            timeout=Timeout.from_seconds(1),
        )


class _InvalidSpanEngine(EntityProcessingEngine[_Config]):
    supported_strategies = frozenset({EntityProcessingStrategy.DETECT})

    def _run(
        self,
        text: str,
        *,
        strategy: EntityProcessingStrategy,
        timeout: Timeout,
    ) -> TextProcessingResult:
        return TextProcessingResult(
            text=text,
            detections=(EntityDetection(entity="token", start=0, end=len(text) + 1),),
        )


def test_engine_boundary_rejects_spans_outside_stage_input() -> None:
    engine = _InvalidSpanEngine(_Config(), None)

    with pytest.raises(EngineContractError):
        engine.run(
            "text",
            strategy=EntityProcessingStrategy.DETECT,
            timeout=Timeout.from_seconds(1),
        )


@pytest.mark.parametrize("seconds", [True, 0, -1, float("inf"), 31])
def test_timeout_duration_is_strict_positive_and_bounded(
    seconds: bool | int | float,
) -> None:
    with pytest.raises(ValueError):
        Timeout.from_seconds(seconds)


def test_expired_timeout_raises_typed_signal() -> None:
    timeout = Timeout(deadline=monotonic() - 1)

    with pytest.raises(TimeoutExpired):
        timeout.raise_if_expired()
