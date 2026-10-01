"""Minimal deterministic semantic-version constraints for HOP components (M3).

HOP resolves component references such as ``debugging@^2.1.0`` against an
immutable local registry. This module implements only the subset needed for
deterministic resolution and deliberately has no network or range-preference
heuristics beyond "highest satisfying version wins".

A version is ``MAJOR.MINOR.PATCH`` with an optional ``-prerelease`` suffix.
An exact reference is a bare version (``1.2.3``). Supported constraint
operators: ``^``, ``~``, ``>=``, ``>``, ``<=``, ``<``, ``=`` and the wildcard
``*``. Constraints may be conjoined with commas or whitespace. A bare
``MAJOR`` or ``MAJOR.MINOR`` is treated as an exact prefix wildcard
(``1`` == ``>=1.0.0,<2.0.0``; ``1.2`` == ``>=1.2.0,<1.3.0``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import total_ordering

_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.\-]+))?$")

_OPERATORS = ("^", "~", ">=", "<=", ">", "<", "=")


class VersionError(ValueError):
    """Raised when a version or constraint cannot be parsed."""


@total_ordering
@dataclass(frozen=True)
class Version:
    major: int
    minor: int
    patch: int
    prerelease: str = ""

    @classmethod
    def parse(cls, text: str) -> Version:
        match = _VERSION_RE.match(text.strip())
        if not match:
            raise VersionError(f"invalid semantic version: {text!r}")
        return cls(
            int(match.group(1)), int(match.group(2)), int(match.group(3)), match.group(4) or ""
        )

    def __str__(self) -> str:
        base = f"{self.major}.{self.minor}.{self.patch}"
        return f"{base}-{self.prerelease}" if self.prerelease else base

    def _cmp_key(self):
        # A release outranks any prerelease of the same numeric version.
        return (self.major, self.minor, self.patch, 0 if self.prerelease else 1, self.prerelease)

    def __lt__(self, other: Version) -> bool:  # type: ignore[override]
        if not isinstance(other, Version):
            return NotImplemented
        return self._cmp_key() < other._cmp_key()

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return self._cmp_key() == other._cmp_key()


@dataclass(frozen=True)
class _Clause:
    op: str
    version: Version

    def matches(self, candidate: Version) -> bool:
        if self.op == "=":
            return candidate == self.version
        if self.op == "^":
            floor = self.version
            if floor.major > 0:
                ceiling = Version(floor.major + 1, 0, 0)
            elif floor.minor > 0:
                ceiling = Version(0, floor.minor + 1, 0)
            else:
                ceiling = Version(0, 0, floor.patch + 1)
            return floor <= candidate < ceiling
        if self.op == "~":
            floor = self.version
            ceiling = Version(floor.major, floor.minor + 1, 0)
            return floor <= candidate < ceiling
        if self.op == ">=":
            return candidate >= self.version
        if self.op == ">":
            return candidate > self.version
        if self.op == "<=":
            return candidate <= self.version
        if self.op == "<":
            return candidate < self.version
        raise VersionError(f"unknown operator {self.op!r}")


class Constraint:
    """A parsed constraint. ``str(Constraint.any())`` is ``*``."""

    def __init__(self, raw: str, clauses: list[_Clause]):
        self.raw = raw
        self.clauses = clauses

    @classmethod
    def any(cls) -> Constraint:
        return cls("*", [])

    @classmethod
    def parse(cls, text: str) -> Constraint:
        raw = (text or "").strip()
        if raw in ("", "*", "latest"):
            return cls.any()
        clauses: list[_Clause] = []
        for token in re.split(r"[,\s]+", raw):
            if not token:
                continue
            clauses.append(cls._parse_token(token))
        if not clauses:
            return cls.any()
        return cls(raw, clauses)

    @staticmethod
    def _parse_token(token: str) -> _Clause:
        op = "="
        body = token
        for candidate in _OPERATORS:
            if token.startswith(candidate):
                op = candidate
                body = token[len(candidate) :]
                break
        if op == "=" and body.count(".") < 2:
            # Prefix wildcard: 1 -> >=1.0.0,<2.0.0 ; 1.2 -> >=1.2.0,<1.3.0
            parts = body.split(".")
            if not all(p.isdigit() for p in parts) or not parts:
                raise VersionError(f"invalid version constraint: {token!r}")
            major = int(parts[0])
            if len(parts) == 1:
                return _Clause("^", Version(major, 0, 0))
            return _Clause("~", Version(major, int(parts[1]), 0))
        try:
            version = Version.parse(body)
        except VersionError as exc:
            raise VersionError(f"invalid version constraint: {token!r}") from exc
        return _Clause(op, version)

    def matches(self, version: Version | str) -> bool:
        candidate = version if isinstance(version, Version) else Version.parse(version)
        return all(clause.matches(candidate) for clause in self.clauses)

    def is_exact(self) -> bool:
        return bool(self.clauses) and all(c.op == "=" for c in self.clauses)

    def exact_version(self) -> Version | None:
        if self.is_exact() and len(self.clauses) == 1:
            return self.clauses[0].version
        return None

    def __str__(self) -> str:
        return self.raw


def parse_version(text: str) -> Version:
    return Version.parse(text)


def satisfies(version: str, constraint: str) -> bool:
    return Constraint.parse(constraint).matches(version)


def highest_satisfying(versions: list[str], constraint: str) -> str | None:
    parsed = Constraint.parse(constraint)
    candidates = []
    for version in versions:
        try:
            candidate = Version.parse(version)
        except VersionError:
            continue
        if parsed.matches(candidate):
            candidates.append(candidate)
    if not candidates:
        return None
    return str(max(candidates))


def is_valid_version(text: str) -> bool:
    try:
        Version.parse(text)
    except VersionError:
        return False
    return True
