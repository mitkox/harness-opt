"""HOP_* canonical environment (the AOP_* fallback was removed in 1.0).

Canonical prefix is ``HOP_*``. The 0.2 compatibility layer that accepted
``AOP_*`` spellings is gone: a legacy variable that is set now raises
:class:`LegacyEnvRemoved` with a migration pointer instead of silently changing
behaviour (see ``docs/migration-1.0.md``).

Endpoint overrides (``*_PI_BASE_URL``) are never honored by any path: both
spellings stay listed below so they can be scrubbed from worker environments
and ignored by the runner (M1 local-only guarantee; see
``hop.policy.SCRUB_ENV_EXACT``). That scrub is deliberately retained after the
fallback removal.
"""

from __future__ import annotations

import os

LEGACY_PREFIX = "AOP_"
CANONICAL_PREFIX = "HOP_"
MIGRATION_DOC = "docs/migration-1.0.md"


class LegacyEnvRemoved(RuntimeError):
    """A removed ``AOP_*`` environment variable is set."""

    def __init__(self, legacy: str, canonical: str):
        super().__init__(
            f"hop 1.0 removed the legacy environment variable {legacy}; "
            f"use {canonical} instead (see {MIGRATION_DOC})"
        )
        self.legacy = legacy
        self.canonical = canonical


def resolve_env(canonical: str, legacy: str | None = None, default: str = "") -> str:
    """Return the canonical value; a set legacy variable fails closed.

    ``legacy`` is kept in the signature so callers name the removed spelling
    explicitly, but it is never resolved as configuration.
    """
    if canonical in os.environ:
        return os.environ[canonical]
    if legacy and legacy in os.environ:
        raise LegacyEnvRemoved(legacy, canonical)
    return default


def is_flag_set(canonical: str, legacy: str | None = None) -> bool:
    return resolve_env(canonical, legacy, "") == "1"


# Canonical names and their removed legacy equivalents. Endpoint overrides are
# listed here only so both spellings can be scrubbed; they are never resolved as
# configuration (see hop.policy.SCRUB_ENV_EXACT).
RUNS_DIR_VARS = ("HOP_RUNS_DIR", "AOP_RUNS_DIR")
VERIFIER_ROOT_VARS = ("HOP_VERIFIER_ROOT", "AOP_VERIFIER_ROOT")
COLLECTOR_DOWN_VARS = ("HOP_COLLECTOR_DOWN", "AOP_COLLECTOR_DOWN")
PI_BASE_URL_VARS = ("HOP_PI_BASE_URL", "AOP_PI_BASE_URL")
