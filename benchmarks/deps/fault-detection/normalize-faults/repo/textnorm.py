"""Tiny text normalization library (deps-pack fixture)."""


def normalize(value: str) -> str:
    """Trim surrounding whitespace and lower-case the text."""
    return value.strip().lower()


def titleize(value: str) -> str:
    """Title-case the trimmed text."""
    return value.strip().title()
