"""Supported entity-processing extension and built-in regex engine surface."""

from __future__ import annotations

from egress_gate.engines.base import (
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
from egress_gate.engines.regex import (
    RegexEngine,
    RegexEngineConfig,
    RegexEntity,
    RegexPatternCatalog,
    RegexReplacement,
    RegexRule,
)
from egress_gate.errors import (
    EngineConfigurationError,
    EngineContractError,
    EngineExecutionError,
    EngineLimitExceededError,
    EntityProcessingError,
)

__all__ = [
    "BoundedMetadata",
    "ConfidenceLevel",
    "EngineConfig",
    "EngineConfigurationError",
    "EngineContractError",
    "EngineExecutionError",
    "EngineLimitExceededError",
    "EngineResources",
    "EntityDetection",
    "EntityName",
    "EntityProcessingEngine",
    "EntityProcessingError",
    "EntityProcessingStrategy",
    "RegexEngine",
    "RegexEngineConfig",
    "RegexEntity",
    "RegexPatternCatalog",
    "RegexReplacement",
    "RegexRule",
    "TextProcessingResult",
]
