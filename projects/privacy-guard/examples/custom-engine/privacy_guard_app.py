"""Deployment-owned assembly for the custom-engine example."""

from custom_engine import KeywordAnalysisTool, KeywordEngine, KeywordEngineResources

from privacy_guard.engine_registry import EngineRegistry


def create_registry() -> EngineRegistry:
    """Create the application-scoped engine registry used by Privacy Guard."""
    registry = EngineRegistry()
    registry.register(
        KeywordEngine,
        resources=KeywordEngineResources(analysis_tool=KeywordAnalysisTool()),
    )
    return registry.finalize()
