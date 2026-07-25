"""Supported entity-processing extension and built-in regex engine surface."""

from __future__ import annotations

from privacy_guard.engines.base import (
    BoundedMetadata,
    ConfidenceLevel,
    EngineConfig,
    EngineResources,
    EntityDetection,
    EntityName,
    EntityProcessingEngine,
    EntityProcessingStrategy,
    TextProcessingResult,
)
from privacy_guard.engines.regex import (
    RegexEngine,
    RegexEngineConfig,
    RegexEntity,
    RegexPattern,
    RegexPatternCatalog,
    RegexReplacement,
)
from privacy_guard.errors import (
    EngineConfigurationError,
    EngineContractError,
    EngineExecutionError,
    EngineLimitExceeded,
    EntityProcessingError,
)

__all__ = [
    "BoundedMetadata",
    "ConfidenceLevel",
    "EngineConfig",
    "EngineConfigurationError",
    "EngineContractError",
    "EngineExecutionError",
    "EngineLimitExceeded",
    "EngineResources",
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
]
