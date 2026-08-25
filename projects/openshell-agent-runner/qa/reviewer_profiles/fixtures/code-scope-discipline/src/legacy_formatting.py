"""Intentionally plain formatting outside the requested review scope."""


def format_total(cents: int) -> str:
    return "$%.2f" % (cents / 100)
