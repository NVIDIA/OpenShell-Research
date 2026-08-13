# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

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
    RegexConfig,
    RegexEntity,
    RegexGate,
    RegexPatternCatalog,
    RegexRule,
)
from egress_gate.gates.regex_scans import (
    RegexBodyAction,
    RegexBodyScan,
    RegexDenyAction,
    RegexDetectAction,
    RegexHeaderScan,
    RegexJsonFieldsScan,
    RegexMessageBlocksScan,
    RegexPathScan,
    RegexQueryScan,
    RegexReadOnlyAction,
    RegexReplaceAction,
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
    "RegexJsonFieldsScan",
    "RegexMessageBlocksScan",
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
