"""Deterministic scanners used only by Privacy Guard tests."""

from __future__ import annotations

import re

from typing_extensions import override

from privacy_guard.scanners import (
    Confidence,
    Finding,
    Scanner,
    ScannerConfig,
)


class DeterministicEmailScanner(Scanner[ScannerConfig]):
    _EMAIL = re.compile(
        r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![\w.-])"
    )

    @override
    def _scan(self, text_block: str) -> tuple[Finding, ...]:
        return tuple(
            Finding(
                entity="email",
                scanner_name=self.scanner_name,
                start_offset=match.start(),
                end_offset=match.end(),
                confidence=Confidence.HIGH,
            )
            for match in self._EMAIL.finditer(text_block)
        )
