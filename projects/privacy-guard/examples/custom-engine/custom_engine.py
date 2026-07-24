"""Example custom entity-processing engine and application registry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import Field

from privacy_guard.base import StrictDomainModel
from privacy_guard.engines import (
    ConfidenceLevel,
    EngineConfig,
    EngineConfigurationError,
    EngineResources,
    EntityDetection,
    EntityName,
    EntityProcessingEngine,
    EntityProcessingStrategy,
    TextProcessingResult,
)
from privacy_guard.timeout import Timeout


class TokenReplacement(StrictDomainModel):
    """Replace every detected keyword with one configured token."""

    strategy: Literal["token"] = "token"
    token: str = Field(min_length=1, max_length=256)


class KeywordEngineConfig(EngineConfig):
    """Policy-owned behavior for the example keyword-analysis tool."""

    engine: Literal["keyword-tool"] = "keyword-tool"
    entity: EntityName
    keyword: str = Field(min_length=1, max_length=256, repr=False)
    replacement: TokenReplacement | None = None


@dataclass(frozen=True)
class KeywordMatch:
    """One match returned by the example third-party-style tool."""

    start: int
    end: int


class KeywordAnalysisTool:
    """Small stand-in for an operator-provided entity-analysis library or client."""

    def find_matches(self, text: str, keyword: str) -> tuple[KeywordMatch, ...]:
        matches: list[KeywordMatch] = []
        start = 0
        while True:
            start = text.find(keyword, start)
            if start < 0:
                return tuple(matches)
            end = start + len(keyword)
            matches.append(KeywordMatch(start=start, end=end))
            start = end


@dataclass(frozen=True)
class KeywordEngineResources(EngineResources):
    """Operator-owned dependencies injected into every configured engine."""

    analysis_tool: KeywordAnalysisTool


class KeywordEngine(
    EntityProcessingEngine[KeywordEngineConfig, KeywordEngineResources],
):
    """Adapt KeywordAnalysisTool results to the Privacy Guard engine contract."""

    supported_strategies = frozenset(
        {
            EntityProcessingStrategy.DETECT,
            EntityProcessingStrategy.REPLACE,
        }
    )

    @classmethod
    def _validate_run_config(
        cls,
        config: KeywordEngineConfig,
        resources: KeywordEngineResources,
        *,
        strategy: EntityProcessingStrategy,
    ) -> None:
        del cls, resources
        if strategy is EntityProcessingStrategy.REPLACE and config.replacement is None:
            raise EngineConfigurationError(
                "keyword replacement configuration is required"
            )

    def _run(
        self,
        text: str,
        *,
        strategy: EntityProcessingStrategy,
        timeout: Timeout,
    ) -> TextProcessingResult:
        timeout.raise_if_expired()
        matches = self.resources.analysis_tool.find_matches(
            text,
            self.config.keyword,
        )
        detections = tuple(
            EntityDetection(
                entity=self.config.entity,
                start=match.start,
                end=match.end,
                confidence=ConfidenceLevel.HIGH,
            )
            for match in matches
        )
        if strategy is EntityProcessingStrategy.DETECT or not matches:
            return TextProcessingResult(text=text, detections=detections)

        replacement = self.config.replacement
        if replacement is None:
            raise EngineConfigurationError(
                "keyword replacement configuration is required"
            )
        output = _replace_matches(text, matches, replacement.token)
        return TextProcessingResult(text=output, detections=detections)


def _replace_matches(
    text: str,
    matches: tuple[KeywordMatch, ...],
    replacement: str,
) -> str:
    output = text
    for match in reversed(matches):
        output = output[: match.start] + replacement + output[match.end :]
    return output
