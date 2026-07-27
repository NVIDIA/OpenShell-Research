"""External consumer fixture checked only against an installed wheel."""

from __future__ import annotations

from typing import Literal, assert_type

from privacy_guard.engines import (
    EngineConfig,
    EngineResources,
    EntityProcessingEngine,
    EntityProcessingStrategy,
    TextProcessingResult,
)
from privacy_guard.timeout import Timeout


class ExternalConfig(EngineConfig):
    engine: Literal["external"] = "external"
    keyword: str


class ExternalClient:
    def find(self, text: str, keyword: str) -> bool:
        return keyword in text


class ExternalResources(EngineResources):
    __slots__ = ("client",)

    def __init__(self, client: ExternalClient) -> None:
        self.client = client


class ExternalEngine(EntityProcessingEngine[ExternalConfig, ExternalResources]):
    supported_strategies = frozenset({EntityProcessingStrategy.DETECT})

    def _run(
        self,
        text: str,
        *,
        strategy: EntityProcessingStrategy,
        timeout: Timeout,
    ) -> TextProcessingResult:
        assert_type(self.config, ExternalConfig)
        assert_type(self.config.keyword, str)
        assert_type(self.resources, ExternalResources)
        assert_type(self.resources.client, ExternalClient)
        self.resources.client.find(text, self.config.keyword)
        result = TextProcessingResult(text=text, detections=())
        assert_type(result, TextProcessingResult)
        return result


engine = ExternalEngine(
    ExternalConfig(keyword="secret"),
    ExternalResources(ExternalClient()),
)
assert_type(engine.config, ExternalConfig)
assert_type(engine.resources, ExternalResources)
processed = engine.run(
    "text",
    strategy=EntityProcessingStrategy.DETECT,
    timeout=Timeout.from_seconds(1),
)
assert_type(processed, TextProcessingResult)
assert_type(processed.text, str)
