"""Local depdemo fixture package."""

__version__ = "1.0.0"


def format_value(value: str) -> str:
    """Trim and upper-case a value."""
    return str(value).strip().upper()
