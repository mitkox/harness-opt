"""Local depdemo fixture package."""

__version__ = "2.0.0"


def format_value(value: str) -> str:
    """Trim and lower-case a value (2.0 behavior change)."""
    return str(value).strip().lower()
