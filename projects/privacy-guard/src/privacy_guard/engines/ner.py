"""Resource-backed general named-entity recognition engine."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from pydantic import Field, field_validator

from privacy_guard.base import StrictDomainModel
from privacy_guard.constants import (
    MAX_DETECTIONS_PER_STAGE,
    MAX_NER_LABEL_BYTES,
    MAX_NER_LABELS,
    MAX_NER_LABELS_BYTES,
)
from privacy_guard.engines._replacement import (
    render_bounded_replacement,
    validate_replacement_template,
)
from privacy_guard.engines.base import (
    EngineConfig,
    EngineResources,
    EntityDetection,
    EntityProcessingEngine,
    EntityProcessingStrategy,
    TextProcessingResult,
)
from privacy_guard.engines.ner_model import NERModel, NERModelEntity
from privacy_guard.errors import (
    EngineConfigurationError,
    EngineContractError,
    EngineLimitExceededError,
)
from privacy_guard.string_validators import validate_scalar_string
from privacy_guard.timeout import Timeout


class NEROverlapMode(StrEnum):
    """Select whether the model may return overlapping nested entities."""

    NESTED = "nested"
    FLAT = "flat"


class NERReplacement(StrictDomainModel):
    """A constrained entity-label template replacement."""

    strategy: Literal["template"] = "template"
    template: str = Field(default="[{entity}]", repr=False)

    @field_validator("template", mode="before")
    @classmethod
    def _template_is_safe_and_bounded(cls, value: object) -> str:
        return validate_replacement_template(value)


class NEREngineConfig(EngineConfig):
    """Exact policy configuration for general named-entity recognition."""

    engine: Literal["ner"] = "ner"
    labels: tuple[str, ...]
    threshold: float = Field(ge=0, le=1, allow_inf_nan=False)
    overlap_mode: NEROverlapMode = NEROverlapMode.NESTED
    replacement: NERReplacement | None = None

    @field_validator("labels", mode="before")
    @classmethod
    def _labels_are_an_ordered_tuple(cls, value: object) -> object:
        if not isinstance(value, list | tuple) or not value:
            raise ValueError("labels must be a non-empty list")
        return tuple(value)

    @field_validator("labels")
    @classmethod
    def _labels_are_bounded_and_unambiguous(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        if len(value) > MAX_NER_LABELS:
            raise ValueError("labels exceed the count limit")
        total_bytes = 0
        normalized: set[str] = set()
        for raw_label in value:
            label = validate_scalar_string(raw_label)
            label_bytes = len(label.encode("utf-8"))
            if (
                not label
                or not label.isprintable()
                or label != label.strip()
                or label_bytes > MAX_NER_LABEL_BYTES
            ):
                raise ValueError("NER label is invalid")
            casefolded = label.casefold()
            if casefolded in normalized:
                raise ValueError("NER labels must be unique ignoring case")
            normalized.add(casefolded)
            total_bytes += label_bytes
        if total_bytes > MAX_NER_LABELS_BYTES:
            raise ValueError("labels exceed the total size limit")
        return value

    @field_validator("overlap_mode", mode="before")
    @classmethod
    def _parse_overlap_mode(cls, value: object) -> NEROverlapMode:
        return NEROverlapMode(validate_scalar_string(value))


@dataclass(frozen=True)
class NERResources(EngineResources):
    """Operator-owned NER model facade shared by engine instances."""

    model: NERModel


class NEREngine(EntityProcessingEngine[NEREngineConfig, NERResources]):
    """Detect named entities with an operator-supplied model facade."""

    supported_strategies = frozenset(
        {
            EntityProcessingStrategy.DETECT,
            EntityProcessingStrategy.REPLACE,
        }
    )

    @classmethod
    def _validate_config(
        cls,
        config: NEREngineConfig,
        resources: NERResources,
    ) -> None:
        if not isinstance(resources.model, NERModel):
            raise EngineConfigurationError("NER model resources are invalid")

    @classmethod
    def _validate_run_config(
        cls,
        config: NEREngineConfig,
        resources: NERResources,
        *,
        strategy: EntityProcessingStrategy,
    ) -> None:
        if strategy is EntityProcessingStrategy.REPLACE and config.replacement is None:
            raise EngineConfigurationError("NER replacement configuration is required")

    def _initialize(self) -> None:
        self._model = self.resources.model
        self._canonical_labels = {
            label.casefold(): (index, label)
            for index, label in enumerate(self.config.labels)
        }

    def _run(
        self,
        text: str,
        *,
        strategy: EntityProcessingStrategy,
        timeout: Timeout,
    ) -> TextProcessingResult:
        raw_entities = self._model.predict_entities(
            text,
            labels=self.config.labels,
            threshold=self.config.threshold,
            flat_ner=self.config.overlap_mode is NEROverlapMode.FLAT,
            timeout=timeout,
        )
        if not isinstance(raw_entities, tuple):
            raise EngineContractError("NER model output is invalid")
        unique: dict[tuple[int, int, str], NERModelEntity] = {}
        for index, entity in enumerate(raw_entities):
            if index >= MAX_DETECTIONS_PER_STAGE:
                raise EngineLimitExceededError("NER detection count exceeds the limit")
            normalized = self._validate_entity(entity, text)
            key = (normalized.start, normalized.end, normalized.label)
            current = unique.get(key)
            if current is None or normalized.score > current.score:
                unique[key] = normalized
            if len(unique) > MAX_DETECTIONS_PER_STAGE:
                raise EngineLimitExceededError("NER detection count exceeds the limit")
        entities = tuple(
            sorted(
                unique.values(),
                key=lambda item: (
                    item.start,
                    item.end,
                    self._canonical_labels[item.label.casefold()][0],
                ),
            )
        )
        detections = tuple(
            EntityDetection(
                entity=entity.label,
                start=entity.start,
                end=entity.end,
            )
            for entity in entities
        )
        output_text = text
        if strategy is EntityProcessingStrategy.REPLACE and entities:
            replacement = self.config.replacement
            if replacement is None:
                raise EngineConfigurationError(
                    "NER replacement configuration is required"
                )
            winners = self._resolve_replacement_winners(entities)
            output_text = render_bounded_replacement(
                text,
                tuple(
                    EntityDetection(
                        entity=winner.label,
                        start=winner.start,
                        end=winner.end,
                    )
                    for winner in winners
                ),
                replacement.template,
                limit_message="NER replacement exceeds the size limit",
            )
        return TextProcessingResult(text=output_text, detections=detections)

    def _validate_entity(self, value: object, text: str) -> NERModelEntity:
        if not isinstance(value, NERModelEntity):
            raise EngineContractError("NER model entity is invalid")
        canonical = self._canonical_labels.get(value.label.casefold())
        if canonical is None or value.end > len(text):
            raise EngineContractError("NER model entity is invalid")
        return NERModelEntity(
            label=canonical[1],
            start=value.start,
            end=value.end,
            score=value.score,
        )

    def _resolve_replacement_winners(
        self,
        entities: tuple[NERModelEntity, ...],
    ) -> tuple[NERModelEntity, ...]:
        winners: list[NERModelEntity] = []
        ranked = sorted(
            entities,
            key=lambda item: (
                -item.score,
                -(item.end - item.start),
                item.start,
                self._canonical_labels[item.label.casefold()][0],
                item.end,
            ),
        )
        for candidate in ranked:
            if all(
                candidate.end <= winner.start or candidate.start >= winner.end
                for winner in winners
            ):
                winners.append(candidate)
        return tuple(
            sorted(
                winners,
                key=lambda item: (
                    item.start,
                    item.end,
                    self._canonical_labels[item.label.casefold()][0],
                ),
            )
        )


__all__ = [
    "NEREngine",
    "NEREngineConfig",
    "NEROverlapMode",
    "NERReplacement",
    "NERResources",
]
