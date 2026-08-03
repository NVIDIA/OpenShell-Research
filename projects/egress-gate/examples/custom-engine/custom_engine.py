"""Complete custom entity-processing engine example."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import Field

from egress_gate.engines import (
    ConfidenceLevel,
    EngineConfig,
    EntityDetection,
    EntityName,
    EntityProcessingEngine,
    EntityProcessingStrategy,
    TextProcessingResult,
)
from egress_gate.engines.registry import EngineRegistry
from egress_gate.timeout import Timeout


class KeywordEngineConfig(EngineConfig):
    """Policy-owned configuration for keyword detection."""

    engine: Literal["keyword-tool"] = "keyword-tool"
    entity: EntityName
    keyword: str = Field(min_length=1, max_length=256, repr=False)


class KeywordEngine(EntityProcessingEngine[KeywordEngineConfig]):
    """Detect every occurrence of one configured keyword."""

    supported_strategies = frozenset({EntityProcessingStrategy.DETECT})

    def _run(
        self,
        text: str,
        *,
        strategy: EntityProcessingStrategy,
        timeout: Timeout,
    ) -> TextProcessingResult:
        matches = re.finditer(re.escape(self.config.keyword), text)
        return TextProcessingResult.from_detections(
            text=text,
            detections=(
                EntityDetection(
                    entity=self.config.entity,
                    start=match.start(),
                    end=match.end(),
                    confidence=ConfidenceLevel.HIGH,
                )
                for match in matches
            ),
        )


def create_registry() -> EngineRegistry:
    """Create a registry containing the built-in and custom engines."""
    registry = EngineRegistry(include_builtin_engines=True)
    registry.register(KeywordEngine)
    return registry.finalize()
