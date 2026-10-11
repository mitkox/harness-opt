"""Local depdemo fixture package."""

__version__ = "1.1.0"


def format_value(value: str) -> str:
    """Trim and upper-case a value."""
    return str(value).strip().upper()


def normalize(value: str) -> str:
    """New additive helper: trim without changing case."""
    return str(value).strip()
