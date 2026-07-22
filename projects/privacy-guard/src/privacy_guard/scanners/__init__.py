"""Scan individual text blocks into safe findings without copying matches."""

from __future__ import annotations

from privacy_guard.scanners.base import (
    Confidence,
    Finding,
    RequestBodyFinding,
    Scanner,
    ScannerConfig,
    ScannerContractError,
    ScannerFindingLimitExceeded,
    parse_scanner_output,
)
from privacy_guard.scanners.passthrough import PassthroughScanner

__all__ = [
    "Confidence",
    "Finding",
    "PassthroughScanner",
    "RequestBodyFinding",
    "Scanner",
    "ScannerConfig",
    "ScannerContractError",
    "ScannerFindingLimitExceeded",
    "parse_scanner_output",
]
