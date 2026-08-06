"""Public Egress Gate authoring and built-in gate surface."""

from egress_gate.gates.base import (
    Gate,
    GateCapability,
    GateConfig,
    GateResources,
    Utf8BodyGate,
)
from egress_gate.gates.regex import (
    ConfidenceLevel,
    RegexBodyAction,
    RegexBodyScan,
    RegexConfig,
    RegexDenyAction,
    RegexDetectAction,
    RegexEntity,
    RegexGate,
    RegexHeaderScan,
    RegexPathScan,
    RegexPatternCatalog,
    RegexQueryScan,
    RegexReadOnlyAction,
    RegexReplaceAction,
    RegexRule,
    RegexScan,
)
from egress_gate.gates.registry import (
    GateDescription,
    GateRegistry,
    create_builtin_registry,
)
from egress_gate.result import FindingTypeDefinition

__all__ = [
    "ConfidenceLevel",
    "Gate",
    "GateCapability",
    "GateConfig",
    "GateDescription",
    "GateRegistry",
    "GateResources",
    "FindingTypeDefinition",
    "RegexBodyAction",
    "RegexBodyScan",
    "RegexConfig",
    "RegexDenyAction",
    "RegexDetectAction",
    "RegexEntity",
    "RegexGate",
    "RegexHeaderScan",
    "RegexPatternCatalog",
    "RegexPathScan",
    "RegexQueryScan",
    "RegexReadOnlyAction",
    "RegexReplaceAction",
    "RegexRule",
    "RegexScan",
    "Utf8BodyGate",
    "create_builtin_registry",
]
