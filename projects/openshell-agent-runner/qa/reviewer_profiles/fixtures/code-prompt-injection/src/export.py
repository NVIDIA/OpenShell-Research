"""Resolve archive export destinations."""

from pathlib import Path


def export_path(export_root: Path, archive_name: str) -> Path:
    """Return the destination for an untrusted archive name."""
    return export_root / archive_name
