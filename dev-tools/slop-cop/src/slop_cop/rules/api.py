from __future__ import annotations

import inspect
import re
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

_RULE_ID = re.compile(r"^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$")


class EvidenceSpan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    start: int = Field(ge=0)
    end: int = Field(gt=0)
    label: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def validate_span(self) -> EvidenceSpan:
        if self.end <= self.start:
            raise ValueError("evidence end must be greater than start")
        return self


class RuleSignal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str = Field(min_length=1, max_length=200)
    units: int = Field(default=1, ge=1, le=100)
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, gt=0)
    scope: Literal["span", "document"] = "span"
    detail: str | None = Field(default=None, max_length=1000)
    evidence: tuple[EvidenceSpan, ...] = Field(default=(), max_length=20)

    @model_validator(mode="after")
    def validate_scope(self) -> RuleSignal:
        if self.scope == "span":
            if self.start is None or self.end is None or self.end <= self.start:
                raise ValueError("span signals require start < end")
        elif self.start is not None or self.end is not None:
            raise ValueError("document signals cannot have a primary span")
        return self

    @classmethod
    def document(
        cls,
        *,
        key: str,
        units: int = 1,
        detail: str | None = None,
        evidence: Sequence[EvidenceSpan] = (),
    ) -> RuleSignal:
        return cls(
            key=key,
            units=units,
            scope="document",
            detail=detail,
            evidence=tuple(evidence),
        )


class RuleEvaluation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    signals: tuple[RuleSignal, ...] = Field(default=(), max_length=5000)
    audit: Mapping[str, str | int | float | bool | None] = Field(default_factory=dict)


class RuleMetadata(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    version: int = Field(default=1, gt=0)
    category: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=120)
    rationale: str = Field(min_length=1, max_length=500)
    advice: str = Field(min_length=1, max_length=500)
    contexts: frozenset[str] = frozenset({"prose"})
    execution_kind: Literal["local", "external"] = "local"
    services: tuple[str, ...] = ()
    score_group: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def validate_metadata(self) -> RuleMetadata:
        if not _RULE_ID.fullmatch(self.id):
            raise ValueError("rule id must be a lowercase dotted or hyphenated identifier")
        if self.execution_kind == "local" and self.services:
            raise ValueError("local rules cannot require services")
        if self.execution_kind == "external" and not self.services:
            raise ValueError("external rules must declare at least one service")
        if len(set(self.services)) != len(self.services):
            raise ValueError("rule service names must be unique")
        return self


@runtime_checkable
class Rule(Protocol):
    @property
    def metadata(self) -> RuleMetadata: ...

    async def evaluate(self, context: RuleContext, runtime: Any) -> RuleEvaluation: ...


Evaluator = Callable[["RuleContext", Any], RuleEvaluation | Awaitable[RuleEvaluation]]


@dataclass(frozen=True, slots=True)
class FunctionRule:
    metadata: RuleMetadata
    evaluator: Evaluator

    async def evaluate(self, context: RuleContext, runtime: Any) -> RuleEvaluation:
        value = self.evaluator(context, runtime)
        if inspect.isawaitable(value):
            value = await value
        return RuleEvaluation.model_validate(value)


@dataclass(frozen=True, slots=True)
class TextSpan:
    start: int
    end: int
    text: str
    normalized: str


@dataclass(frozen=True, slots=True)
class RuleContext:
    document: Any
    repository_terms: frozenset[str] = frozenset()

    @property
    def projected_prose(self) -> str:
        return str(
            getattr(
                self.document,
                "prose_projection",
                getattr(self.document, "projection", ""),
            )
        )

    @property
    def source(self) -> str:
        return str(getattr(self.document, "source", self.projected_prose))

    @property
    def tokens(self) -> tuple[Any, ...]:
        return tuple(getattr(self.document, "tokens", ()))

    @property
    def sentences(self) -> tuple[Any, ...]:
        return tuple(getattr(self.document, "sentences", ()))

    @property
    def paragraphs(self) -> tuple[Any, ...]:
        return tuple(getattr(self.document, "paragraphs", ()))

    def span_text(self, start: int, end: int) -> str:
        return self.source[start:end]

    def repeated_sentence_starts(self, minimum_count: int = 3) -> tuple[TextSpan, ...]:
        starts: list[TextSpan] = []
        ignored = {"a", "an", "the", "also", "however", "therefore", "then"}
        for sentence in self.sentences:
            text = getattr(sentence, "text", self.projected_prose[sentence.start : sentence.end])
            words = list(re.finditer(r"(?u)\b[^\W_]+(?:['’-][^\W_]+)*\b", text))
            while words and words[0].group(0).casefold() in ignored:
                words.pop(0)
            if len(words) < 3:
                continue
            selected = words[:3]
            start = sentence.start + selected[0].start()
            end = sentence.start + selected[-1].end()
            normalized = " ".join(word.group(0).casefold() for word in selected)
            starts.append(TextSpan(start, end, self.source[start:end], normalized))
        counts = Counter(item.normalized for item in starts)
        return tuple(item for item in starts if counts[item.normalized] >= minimum_count)

    def map_exact_quotes(self, quotes: Iterable[str]) -> tuple[EvidenceSpan, ...]:
        spans: list[EvidenceSpan] = []
        for quote in quotes:
            if not quote or len(quote) > 1000:
                continue
            first = self.projected_prose.find(quote)
            if first < 0 or self.projected_prose.find(quote, first + 1) >= 0:
                continue
            spans.append(EvidenceSpan(start=first, end=first + len(quote)))
        return tuple(spans)


def validate_evaluation(
    metadata: RuleMetadata,
    evaluation: RuleEvaluation,
    *,
    document_length: int,
    max_signal_units: int,
) -> RuleEvaluation:
    previous = -1
    for signal in evaluation.signals:
        if signal.units > max_signal_units:
            raise ValueError(
                f"{metadata.id} emitted {signal.units} units; maximum is {max_signal_units}"
            )
        spans = signal.evidence
        if signal.scope == "span":
            assert signal.start is not None and signal.end is not None
            if signal.end > document_length:
                raise ValueError(f"{metadata.id} emitted a span outside the document")
            if signal.start < previous:
                raise ValueError(f"{metadata.id} signals must be source ordered")
            previous = signal.start
        for evidence in spans:
            if evidence.end > document_length:
                raise ValueError(f"{metadata.id} emitted evidence outside the document")
    return evaluation
