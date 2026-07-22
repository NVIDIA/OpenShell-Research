#!/usr/bin/env python3
"""Development-only deterministic Privacy Guard server for the manual E2E."""

from __future__ import annotations

import argparse
import re

from privacy_guard.scanners import (
    Confidence,
    Finding,
    Scanner,
    ScannerConfig,
)
from privacy_guard.service import MiddlewareServer


class ExampleEmailScanner(Scanner[ScannerConfig]):
    """Detect example email addresses deterministically; not a production scanner."""

    _EMAIL = re.compile(
        r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![\w.-])"
    )

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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen", default="127.0.0.1:50051")
    arguments = parser.parse_args()
    scanner = ExampleEmailScanner(
        ScannerConfig(name="example_regex", entity_types=frozenset({"email"}))
    )
    server = MiddlewareServer(scanner=scanner)
    server.serve(arguments.listen)


if __name__ == "__main__":
    main()
