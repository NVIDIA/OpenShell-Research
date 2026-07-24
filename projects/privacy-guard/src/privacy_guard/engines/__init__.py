"""Supported entity-processing extension and built-in regex engine surface."""

from __future__ import annotations

from privacy_guard.engines.base import (
    BoundedMetadata,
    ConfidenceLevel,
    DetectionConfidence,
    EngineConfig,
    EngineConfigurationError,
    EngineContractError,
    EngineExecutionError,
    EngineLimitExceeded,
    EntityDetection,
    EntityName,
    EntityProcessingEngine,
    EntityProcessingError,
    EntityProcessingStrategy,
    TextProcessingResult,
    UnitInterval,
)
from privacy_guard.engines.regex import (
    RegexEngine,
    RegexEngineConfig,
    RegexEntity,
    RegexPattern,
    RegexPatternCatalog,
    RegexReplacement,
)

__all__ = [
    "BoundedMetadata",
    "ConfidenceLevel",
    "DetectionConfidence",
    "EngineConfig",
    "EngineConfigurationError",
    "EngineContractError",
    "EngineExecutionError",
    "EngineLimitExceeded",
    "EntityDetection",
    "EntityName",
    "EntityProcessingEngine",
    "EntityProcessingError",
    "EntityProcessingStrategy",
    "RegexEngine",
    "RegexEngineConfig",
    "RegexEntity",
    "RegexPattern",
    "RegexPatternCatalog",
    "RegexReplacement",
    "TextProcessingResult",
    "UnitInterval",
]
