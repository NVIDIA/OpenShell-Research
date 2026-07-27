"""Tests for the built-in general NER engine."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import pytest
from pydantic import ValidationError

import privacy_guard.engines._replacement as replacement_module
import privacy_guard.engines.ner as ner_module
from privacy_guard.engines import (
    EngineConfigurationError,
    EngineContractError,
    EngineLimitExceededError,
    EntityProcessingStrategy,
    NEREngine,
    NEREngineConfig,
    NERModelEntity,
    NERResources,
)
from privacy_guard.engines.registry import create_builtin_registry
from privacy_guard.timeout import Timeout


@dataclass
class FakeNERModel:
    entities: tuple[NERModelEntity, ...]
    on_call: Callable[..., None] | None = None

    def predict_entities(
        self,
        text: str,
        *,
        labels: tuple[str, ...],
        threshold: float,
        flat_ner: bool,
        timeout: Timeout,
    ) -> tuple[NERModelEntity, ...]:
        if self.on_call is not None:
            self.on_call(text, labels, threshold, flat_ner, timeout)
        return self.entities


def _config(
    *,
    labels: list[str] | None = None,
    threshold: object = 0.5,
    overlap_mode: str = "nested",
    replacement: dict[str, object] | None = None,
) -> NEREngineConfig:
    values: dict[str, object] = {
        "engine": "ner",
        "labels": ["person", "email"] if labels is None else labels,
        "threshold": threshold,
        "overlap_mode": overlap_mode,
    }
    if replacement is not None:
        values["replacement"] = replacement
    return NEREngineConfig.model_validate(values)


def _run(
    model: FakeNERModel,
    config: NEREngineConfig,
    text: str,
    *,
    strategy: EntityProcessingStrategy = EntityProcessingStrategy.DETECT,
) -> tuple[str, list[tuple[str, int, int]]]:
    result = NEREngine(config, NERResources(model=model)).run(
        text,
        strategy=strategy,
        timeout=Timeout.from_seconds(1),
    )
    return result.text, [
        (detection.entity, detection.start, detection.end)
        for detection in result.detections
    ]


def test_forwards_policy_behavior_and_returns_deterministic_detections() -> None:
    calls: list[tuple[object, ...]] = []

    def record_call(*values: object) -> None:
        calls.append(values)

    model = FakeNERModel(
        entities=(
            NERModelEntity(label="EMAIL", start=16, end=19, score=0.8),
            NERModelEntity(label="PERSON", start=0, end=5, score=0.7),
            NERModelEntity(label="person", start=0, end=5, score=0.9),
        ),
        on_call=record_call,
    )
    config = _config(threshold=0.3, overlap_mode="flat")

    output, detections = _run(model, config, "Alice contacted x@y")

    assert output == "Alice contacted x@y"
    assert detections == [("person", 0, 5), ("email", 16, 19)]
    assert calls
    assert calls[0][0:4] == (
        "Alice contacted x@y",
        ("person", "email"),
        0.3,
        True,
    )


def test_nested_mode_maps_to_flat_ner_false() -> None:
    flat_values: list[bool] = []

    def record_call(
        text: str,
        labels: tuple[str, ...],
        threshold: float,
        flat_ner: bool,
        timeout: Timeout,
    ) -> None:
        del text, labels, threshold, timeout
        flat_values.append(flat_ner)

    _run(FakeNERModel(entities=(), on_call=record_call), _config(), "text")

    assert flat_values == [False]


def test_replacement_keeps_all_detections_and_ranks_overlap_winners() -> None:
    model = FakeNERModel(
        entities=(
            NERModelEntity(label="person", start=0, end=5, score=0.6),
            NERModelEntity(label="email", start=1, end=4, score=0.9),
            NERModelEntity(label="person", start=6, end=10, score=0.8),
        )
    )
    config = _config(replacement={"strategy": "template", "template": "<{entity}>"})

    output, detections = _run(
        model,
        config,
        "abcdefghij",
        strategy=EntityProcessingStrategy.REPLACE,
    )

    assert output == "a<email>ef<person>"
    assert len(detections) == 3


def test_replace_requires_replacement_configuration() -> None:
    with pytest.raises(EngineConfigurationError):
        NEREngine.validate_run_config(
            _config(),
            NERResources(model=FakeNERModel(entities=())),
            strategy=EntityProcessingStrategy.REPLACE,
        )


def test_registry_validation_is_pure_and_does_not_run_model() -> None:
    calls = 0

    def record_call(*values: object) -> None:
        nonlocal calls
        del values
        calls += 1

    registry = create_builtin_registry(
        ner_resources=NERResources(model=FakeNERModel(entities=(), on_call=record_call))
    )

    registry.validate_config(
        {
            "entity_processing": {
                "stages": [
                    {
                        "config": {
                            "engine": "ner",
                            "labels": ["person"],
                            "threshold": 0.5,
                        }
                    }
                ]
            },
            "on_detection": {"action": "detect"},
        }
    )

    assert calls == 0


@pytest.mark.parametrize(
    "labels",
    [
        [],
        ["person", "PERSON"],
        [""],
        [" person"],
        ["line\nbreak"],
        ["x" * 257],
    ],
)
def test_invalid_labels_are_rejected(labels: list[str]) -> None:
    with pytest.raises(ValidationError):
        _config(labels=labels)


@pytest.mark.parametrize(
    "threshold",
    [-0.1, 1.1, float("nan"), float("inf"), True, "0.5"],
)
def test_invalid_thresholds_are_rejected(threshold: object) -> None:
    with pytest.raises(ValidationError):
        _config(threshold=threshold)


@pytest.mark.parametrize(
    "replacement",
    [
        {"strategy": "template", "template": "{unknown}"},
        {"strategy": "template", "template": "{entity!r}"},
        {"strategy": "template", "template": "{entity:>10}"},
        {"strategy": "template", "template": "{"},
    ],
)
def test_replacement_template_is_constrained(
    replacement: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        _config(replacement=replacement)


@pytest.mark.parametrize(
    "entity",
    [
        NERModelEntity(label="unknown", start=0, end=1, score=0.5),
        NERModelEntity(label="person", start=0, end=5, score=0.5),
    ],
)
def test_engine_rejects_entities_invalid_for_policy_or_input(
    entity: NERModelEntity,
) -> None:
    with pytest.raises(EngineContractError):
        _run(FakeNERModel(entities=(entity,)), _config(), "abc")


@pytest.mark.parametrize(
    "values",
    [
        {"label": "person", "start": -1, "end": 1, "score": 0.5},
        {"label": "person", "start": 1, "end": 1, "score": 0.5},
        {"label": "person", "start": 0, "end": 1, "score": -0.1},
        {"label": "person", "start": 0, "end": 1, "score": 1.1},
        {"label": "person", "start": 0, "end": 1, "score": float("nan")},
        {"label": "person", "start": True, "end": 1, "score": 0.5},
    ],
)
def test_normalized_entity_invariants_are_pydantic_validated(
    values: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        NERModelEntity.model_validate(values)


def test_detection_count_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ner_module, "MAX_DETECTIONS_PER_STAGE", 1)
    entities = (
        NERModelEntity(label="person", start=0, end=1, score=0.5),
        NERModelEntity(label="person", start=1, end=2, score=0.5),
    )

    with pytest.raises(EngineLimitExceededError):
        _run(FakeNERModel(entities=entities), _config(), "ab")


def test_replacement_size_is_projected_before_rendering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(replacement_module, "MAX_BODY_BYTES", 4)
    config = _config(replacement={"strategy": "template", "template": "[{entity}]"})
    model = FakeNERModel(
        entities=(NERModelEntity(label="person", start=0, end=1, score=0.5),)
    )

    with pytest.raises(EngineLimitExceededError):
        _run(model, config, "x", strategy=EntityProcessingStrategy.REPLACE)
