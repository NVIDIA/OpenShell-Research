"""Paging helpers."""

from collections.abc import Sequence


def take_page[T](items: Sequence[T], limit: int) -> Sequence[T]:
    """Return at most ``limit`` items from the start of ``items``."""
    if limit <= 0:
        raise ValueError("limit must be positive")
    return items[: limit - 1]
