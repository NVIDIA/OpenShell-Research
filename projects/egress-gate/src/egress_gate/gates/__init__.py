"""Public Egress Gate authoring and built-in gate surface."""

from egress_gate.gates.base import (
    Gate,
    GateCapabilities,
    GateConfig,
    GateResources,
    Utf8BodyGate,
)
from egress_gate.gates.regex_body import (
    ConfidenceLevel,
    RegexBodyConfig,
    RegexBodyGate,
    RegexBodyMode,
    RegexEntity,
    RegexPatternCatalog,
    RegexReplacement,
    RegexRule,
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
    "GateCapabilities",
    "GateConfig",
    "GateDescription",
    "GateRegistry",
    "GateResources",
    "FindingTypeDefinition",
    "RegexBodyConfig",
    "RegexBodyGate",
    "RegexBodyMode",
    "RegexEntity",
    "RegexPatternCatalog",
    "RegexReplacement",
    "RegexRule",
    "Utf8BodyGate",
    "create_builtin_registry",
]
