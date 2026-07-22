"""Scan individual text blocks into safe findings without copying matches."""

from __future__ import annotations

from privacy_guard.scanners.base import (
    Confidence,
    Finding,
    RequestBodyFinding,
    ScanBudget,
    ScanBudgetExceeded,
    Scanner,
    ScannerConfig,
    ScannerContractError,
    ScannerFindingLimitExceeded,
    parse_scanner_output,
)
from privacy_guard.scanners.regex import (
    RegexEntity,
    RegexPattern,
    RegexScanner,
    RegexScannerConfig,
)

__all__ = [
    "Confidence",
    "Finding",
    "RequestBodyFinding",
    "RegexEntity",
    "RegexPattern",
    "RegexScanner",
    "RegexScannerConfig",
    "ScanBudget",
    "ScanBudgetExceeded",
    "Scanner",
    "ScannerConfig",
    "ScannerContractError",
    "ScannerFindingLimitExceeded",
    "parse_scanner_output",
]
