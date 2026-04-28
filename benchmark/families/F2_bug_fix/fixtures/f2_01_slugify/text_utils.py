import re


def slugify(value: str) -> str:
    """Return a URL slug for a human-readable title."""
    return value.strip().lower().replace(" ", "-")
