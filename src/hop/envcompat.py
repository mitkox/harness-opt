"""HOP_* canonical environment with AOP_* legacy fallback (rename compat).

Canonical prefix is ``HOP_*``. Every ``AOP_*`` variable keeps working during
the compatibility period, but:

- ``HOP_*`` wins when both are present;
- legacy use prints a concise deprecation warning to stderr;
- endpoint overrides (``*_PI_BASE_URL``) are never honored by any path:
  both variants are scrubbed from worker environments and ignored by the
  runner (M1 local-only guarantee; see hop.policy).

Removal target for the legacy prefix: M4 (see ADR-008).
"""
from __future__ import annotations

import os
import sys

LEGACY_PREFIX = "AOP_"
CANONICAL_PREFIX = "HOP_"
LEGACY_REMOVAL_TARGET = "M4"

_warned: set[str] = set()


def resolve_env(canonical: str, legacy: str | None = None,
                default: str = "") -> str:
    """Return the canonical value, falling back to legacy with a warning."""
    if canonical in os.environ:
        return os.environ[canonical]
    if legacy and legacy in os.environ:
        if legacy not in _warned:
            _warned.add(legacy)
            print(f"hop: deprecated env {legacy}; use {canonical} "
                  f"(legacy support ends {LEGACY_REMOVAL_TARGET})",
                  file=sys.stderr)
        return os.environ[legacy]
    return default


def is_flag_set(canonical: str, legacy: str | None = None) -> bool:
    return resolve_env(canonical, legacy, "") == "1"


# Canonical names and their legacy equivalents. Endpoint overrides are listed
# here only so both spellings can be scrubbed; they are never resolved as
# configuration (see hop.policy.SCRUB_ENV_EXACT).
RUNS_DIR_VARS = ("HOP_RUNS_DIR", "AOP_RUNS_DIR")
VERIFIER_ROOT_VARS = ("HOP_VERIFIER_ROOT", "AOP_VERIFIER_ROOT")
COLLECTOR_DOWN_VARS = ("HOP_COLLECTOR_DOWN", "AOP_COLLECTOR_DOWN")
PI_BASE_URL_VARS = ("HOP_PI_BASE_URL", "AOP_PI_BASE_URL")
