"""Scanner that deliberately reports no findings for a text block."""

from __future__ import annotations

from typing_extensions import override

from privacy_guard.scanners.base import (
    Finding,
    Scanner,
    ScannerConfig,
)


class PassthroughScanner(Scanner[ScannerConfig]):
    """Scan one text block without reporting sensitive values."""

    def __init__(self) -> None:
        super().__init__(ScannerConfig(name="passthrough", entity_types=frozenset()))

    @override
    def _scan(self, text_block: str) -> tuple[Finding, ...]:
        """Return no findings for the supplied text block."""
        return ()
