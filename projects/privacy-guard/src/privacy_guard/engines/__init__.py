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
from privacy_guard.engines.ner import (
    NEREngine,
    NEREngineConfig,
    NEROverlapMode,
    NERReplacement,
    NERResources,
)
from privacy_guard.engines.ner_model import (
    LocalNERModel,
    LocalNERPredictor,
    NERExtractEndpointModel,
    NERModel,
    NERModelEntity,
)
from privacy_guard.engines.regex import (
    RegexEngine,
    RegexEngineConfig,
    RegexEntity,
    RegexPatternCatalog,
    RegexReplacement,
    RegexRule,
)
from privacy_guard.errors import (
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
    "LocalNERModel",
    "LocalNERPredictor",
    "NEREngine",
    "NEREngineConfig",
    "NERExtractEndpointModel",
    "NERModel",
    "NERModelEntity",
    "NEROverlapMode",
    "NERReplacement",
    "NERResources",
    "RegexEngine",
    "RegexEngineConfig",
    "RegexEntity",
    "RegexPatternCatalog",
    "RegexReplacement",
    "RegexRule",
    "TextProcessingResult",
]
