"""Provider-neutral model facade for named-entity recognition."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from threading import Lock
from typing import Protocol, Self, runtime_checkable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from pydantic import Field, model_validator

from privacy_guard.base import StrictDomainModel
from privacy_guard.constants import (
    MAX_DETECTIONS_PER_STAGE,
    MAX_NER_ENDPOINT_RESPONSE_BYTES,
)
from privacy_guard.errors import EngineExecutionError, EngineLimitExceededError
from privacy_guard.string_validators import ScalarString, validate_scalar_string
from privacy_guard.timeout import Timeout


class NERModelEntity(StrictDomainModel):
    """One normalized model detection using Unicode code-point offsets."""

    label: ScalarString
    start: int = Field(ge=0)
    end: int
    score: float = Field(ge=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def _span_is_non_empty(self) -> Self:
        if self.end <= self.start:
            raise ValueError("NER model entity span must be non-empty")
        return self


@runtime_checkable
class NERModel(Protocol):
    """Complete execution facade required by the built-in NER engine."""

    def predict_entities(
        self,
        text: str,
        *,
        labels: tuple[str, ...],
        threshold: float,
        flat_ner: bool,
        timeout: Timeout,
    ) -> tuple[NERModelEntity, ...]:
        """Return normalized entities for the complete input text."""
        ...


@dataclass(frozen=True)
class NERExtractEndpointModel:
    """Call the explicit GLiNER-compatible ``POST /v1/extract`` contract."""

    endpoint: str = field(repr=False)
    model: str = field(repr=False)
    chunk_length: int = 384
    overlap: int = 128
    max_response_bytes: int = MAX_NER_ENDPOINT_RESPONSE_BYTES

    def __post_init__(self) -> None:
        try:
            endpoint = validate_scalar_string(self.endpoint)
            model = validate_scalar_string(self.model)
        except ValueError:
            raise ValueError("NER endpoint configuration is invalid") from None
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path != "/v1/extract"
        ):
            raise ValueError("NER extract endpoint is invalid")
        if not model or not model.isprintable():
            raise ValueError("NER model identifier is invalid")
        if (
            isinstance(self.chunk_length, bool)
            or not isinstance(self.chunk_length, int)
            or self.chunk_length <= 0
            or isinstance(self.overlap, bool)
            or not isinstance(self.overlap, int)
            or self.overlap < 0
            or self.overlap >= self.chunk_length
        ):
            raise ValueError("NER chunk configuration is invalid")
        if (
            isinstance(self.max_response_bytes, bool)
            or not isinstance(self.max_response_bytes, int)
            or self.max_response_bytes <= 0
            or self.max_response_bytes > MAX_NER_ENDPOINT_RESPONSE_BYTES
        ):
            raise ValueError("NER endpoint response limit is invalid")

    def predict_entities(
        self,
        text: str,
        *,
        labels: tuple[str, ...],
        threshold: float,
        flat_ner: bool,
        timeout: Timeout,
    ) -> tuple[NERModelEntity, ...]:
        payload = json.dumps(
            {
                "text": text,
                "labels": labels,
                "model": self.model,
                "threshold": threshold,
                "chunk_length": self.chunk_length,
                "overlap": self.overlap,
                "flat_ner": flat_ner,
            },
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        request = Request(
            self.endpoint,
            data=payload,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json; charset=utf-8",
            },
            method="POST",
        )
        try:
            with timeout.enforce():
                with urlopen(
                    request,
                    timeout=timeout.remaining_seconds(),
                ) as response:
                    response_bytes = response.read(self.max_response_bytes + 1)
        except (HTTPError, URLError, OSError, TimeoutError):
            raise EngineExecutionError("NER endpoint request failed") from None
        if len(response_bytes) > self.max_response_bytes:
            raise EngineLimitExceededError(
                "NER endpoint response exceeds the size limit"
            )
        try:
            decoded = json.loads(response_bytes)
            return _normalize_model_output(decoded)
        except (UnicodeError, json.JSONDecodeError, TypeError, ValueError):
            raise EngineExecutionError("NER endpoint response is invalid") from None


class LocalNERPredictor(Protocol):
    """Narrow structural contract implemented by a loaded GLiNER model."""

    def predict_entities(
        self,
        text: str,
        labels: list[str],
        *,
        threshold: float,
        flat_ner: bool,
    ) -> object:
        """Return the loaded model's entity payload."""
        ...


class LocalNERModel:
    """Serialize calls to an already-loaded model that processes complete text."""

    def __init__(
        self,
        *,
        model: LocalNERPredictor,
        chunk_length: int = 384,
        overlap: int = 128,
    ) -> None:
        if (
            isinstance(chunk_length, bool)
            or not isinstance(chunk_length, int)
            or chunk_length <= 0
            or isinstance(overlap, bool)
            or not isinstance(overlap, int)
            or overlap < 0
            or overlap >= chunk_length
        ):
            raise ValueError("NER chunk configuration is invalid")
        self._model = model
        self._chunk_length = chunk_length
        self._overlap = overlap
        self._lock = Lock()

    @property
    def chunk_length(self) -> int:
        """Return the operator-selected model chunk length."""
        return self._chunk_length

    @property
    def overlap(self) -> int:
        """Return the operator-selected model chunk overlap."""
        return self._overlap

    def predict_entities(
        self,
        text: str,
        *,
        labels: tuple[str, ...],
        threshold: float,
        flat_ner: bool,
        timeout: Timeout,
    ) -> tuple[NERModelEntity, ...]:
        acquired = self._lock.acquire(timeout=timeout.remaining_seconds())
        if not acquired:
            timeout.raise_if_expired()
            raise EngineExecutionError("NER local model is unavailable")
        try:
            unique: dict[tuple[int, int, str], NERModelEntity] = {}
            for start, chunk in _iter_text_chunks(
                text,
                chunk_length=self._chunk_length,
                overlap=self._overlap,
            ):
                with timeout.enforce():
                    output = self._model.predict_entities(
                        chunk,
                        list(labels),
                        threshold=threshold,
                        flat_ner=flat_ner,
                    )
                for entity in _normalize_model_output(output):
                    if (
                        entity.start < 0
                        or entity.end <= entity.start
                        or entity.end > len(chunk)
                    ):
                        raise ValueError("local entity span is invalid")
                    rebased = NERModelEntity(
                        label=entity.label,
                        start=start + entity.start,
                        end=start + entity.end,
                        score=entity.score,
                    )
                    key = (rebased.start, rebased.end, rebased.label)
                    current = unique.get(key)
                    if current is None or rebased.score > current.score:
                        unique[key] = rebased
                    if len(unique) > MAX_DETECTIONS_PER_STAGE:
                        raise EngineLimitExceededError(
                            "NER model returned too many detections"
                        )
            entities = tuple(unique.values())
            if flat_ner:
                entities = _select_flat_entities(entities)
            return tuple(
                sorted(
                    entities,
                    key=lambda item: (item.start, item.end, item.label),
                )
            )
        except (TypeError, ValueError, RuntimeError, OSError):
            raise EngineExecutionError("NER local model inference failed") from None
        finally:
            self._lock.release()


def _normalize_model_output(value: object) -> tuple[NERModelEntity, ...]:
    if isinstance(value, Mapping):
        value = value.get("entities")
    if not isinstance(value, list | tuple):
        raise ValueError("entity output must be a list")
    if len(value) > MAX_DETECTIONS_PER_STAGE:
        raise EngineLimitExceededError("NER model returned too many detections")
    normalized: list[NERModelEntity] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError("entity item must be an object")
        normalized.append(
            NERModelEntity.model_validate(
                {
                    "label": item.get("label"),
                    "start": item.get("start"),
                    "end": item.get("end"),
                    "score": item.get("score"),
                }
            )
        )
    return tuple(normalized)


def _iter_text_chunks(
    text: str,
    *,
    chunk_length: int,
    overlap: int,
) -> Iterator[tuple[int, str]]:
    if len(text) <= chunk_length:
        yield 0, text
        return
    start = 0
    while start < len(text):
        end = min(start + chunk_length, len(text))
        yield start, text[start:end]
        if end == len(text):
            break
        start = end - overlap


def _select_flat_entities(
    entities: tuple[NERModelEntity, ...],
) -> tuple[NERModelEntity, ...]:
    winners: list[NERModelEntity] = []
    for candidate in sorted(
        entities,
        key=lambda item: (
            -item.score,
            -(item.end - item.start),
            item.start,
            item.end,
            item.label,
        ),
    ):
        if all(
            candidate.end <= winner.start or candidate.start >= winner.end
            for winner in winners
        ):
            winners.append(candidate)
    return tuple(winners)


__all__ = [
    "LocalNERModel",
    "LocalNERPredictor",
    "NERExtractEndpointModel",
    "NERModel",
    "NERModelEntity",
]
