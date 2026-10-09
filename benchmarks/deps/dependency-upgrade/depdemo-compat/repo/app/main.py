"""App that consumes the local depdemo package."""

import depdemo


def render(value: str) -> str:
    """Render a value through the dependency's documented contract."""
    return depdemo.format_value(value)
