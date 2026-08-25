"""Convert display names to stable ASCII slugs."""

import re
import unicodedata


def slugify(value: str) -> str:
    """Return a lowercase ASCII slug or reject a value with no usable text."""
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-")
    if not slug:
        raise ValueError("display name must contain an ASCII letter or digit")
    return slug
