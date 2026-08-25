"""Order total calculation."""

from functools import reduce


def order_total(line_item_cents: list[int]) -> int:
    """Return the sum of all line-item amounts in cents."""
    if any(amount < 0 for amount in line_item_cents):
        raise ValueError("line-item amounts cannot be negative")
    return reduce(lambda total, amount: total + amount, line_item_cents)
