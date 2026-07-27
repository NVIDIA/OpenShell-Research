from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal

import pytest
from pydantic import ValidationError

from privacy_guard.base import StrictDomainModel
from privacy_guard.constants import MAX_DETECTIONS_PER_STAGE
from privacy_guard.engines import (
    ConfidenceLevel,
    EngineConfig,
    EngineContractError,
    EngineLimitExceededError,
    EngineResources,
    EntityDetection,
    EntityProcessingEngine,
    EntityProcessingStrategy,
    TextProcessingResult,
)
from privacy_guard.timeout import Timeout


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


@pytest.mark.parametrize(
    "unsafe_value",
    [
        "line\nbreak",
        "ansi\x1b[31m",
        "nul\x00byte",
        "right-to-left\u202eoverride",
    ],
)
def test_detection_rejects_non_printable_identifiers_and_metadata(
    unsafe_value: str,
) -> None:
    with pytest.raises(ValidationError):
        EntityDetection(
            entity=unsafe_value,
            start=0,
            end=1,
        )
    with pytest.raises(ValidationError):
        EntityDetection(
            entity="token",
            start=0,
            end=1,
            metadata={unsafe_value: "value"},
        )
    with pytest.raises(ValidationError):
        EntityDetection(
            entity="token",
            start=0,
            end=1,
            metadata={"key": unsafe_value},
        )


def test_detection_accepts_printable_unicode_identifiers_and_metadata() -> None:
    detection = EntityDetection(
        entity="客户资料",
        start=0,
        end=1,
        metadata={"提供者": "自定义 🛡️"},
    )

    assert detection.entity == "客户资料"
    assert detection.metadata == {"提供者": "自定义 🛡️"}


def test_processing_result_bounds_a_lazy_detection_stream() -> None:
    produced = 0

    def detections() -> Iterator[EntityDetection]:
        nonlocal produced
        for index in range(1_000):
            produced += 1
            yield EntityDetection(entity="token", start=index, end=index + 1)

    with pytest.raises(EngineLimitExceededError):
        TextProcessingResult.from_detections(
            text="x" * 1_000,
            detections=detections(),
        )

    assert produced == 257


class _OversizedResultEngine(EntityProcessingEngine[_Config]):
    supported_strategies = frozenset({EntityProcessingStrategy.DETECT})

    def _run(
        self,
        text: str,
        *,
        strategy: EntityProcessingStrategy,
        timeout: Timeout,
    ) -> TextProcessingResult:
        del strategy, timeout
        return TextProcessingResult(
            text=text,
            detections=tuple(
                EntityDetection(entity="token", start=0, end=1)
                for _ in range(MAX_DETECTIONS_PER_STAGE + 1)
            ),
        )


def test_engine_boundary_bounds_results_built_without_lazy_helper() -> None:
    engine = _OversizedResultEngine(_Config(), None)

    with pytest.raises(EngineLimitExceededError):
        engine.run(
            "text",
            strategy=EntityProcessingStrategy.DETECT,
            timeout=Timeout.from_seconds(1),
        )


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
