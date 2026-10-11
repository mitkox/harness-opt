"""Protected split assignment and sealed-store location policy (AOP-020 half B).

Two responsibilities:

* **Split assignment** — development / validation / sealed-evaluator is a pure,
  deterministic function of ``(seed, group_id)`` so it is stable across runs and
  independent of case order. Membership is per :class:`CaseGroup`; every
  :class:`CasePackage` of a group inherits that group's split, and a group that
  would straddle two splits is a hard error (never silently reassigned).
* **Sealed-store location** — the sealed evaluator store must live outside the
  repository and outside every path an agent or optimizer worker can reach. The
  validator rejects a root inside this checkout or inside any path mounted
  read-only into a worker (``sandbox.SYSTEM_RO_BINDS``).

The modules here interoperate with ``hop.contracts.evaluation`` (owned by
WP-2.1a) by field access, so this half is not blocked while that contract lands.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterable, Mapping
from enum import Enum

from ..files import atomic_json
from ..sandbox import SYSTEM_RO_BINDS

SPLIT_SCHEMA_VERSION = 1
DEFAULT_SEED = "hop-splits-v1"
# Stable iteration/assignment order; do not reorder (it would re-bucket groups).
SPLIT_NAMES = ("development", "validation", "sealed-evaluator")
DEFAULT_RATIOS = {"development": 0.6, "validation": 0.2, "sealed-evaluator": 0.2}
DEFAULT_SEALED_DIRNAME = "sealed"
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class Split(str, Enum):
    DEVELOPMENT = "development"
    VALIDATION = "validation"
    SEALED = "sealed-evaluator"


class SplitStraddleError(ValueError):
    """A CaseGroup would be assigned to more than one split."""


class SplitStoreMisconfigured(ValueError):
    """The store location violates the sealed-path policy."""


def group_id_of(group: object) -> str:
    """Read ``group_id`` from a contract object or an equivalent mapping."""
    value = (
        group.get("group_id") if isinstance(group, Mapping) else getattr(group, "group_id", None)
    )
    if not value or not isinstance(value, str):
        raise SplitStoreMisconfigured(f"cannot read group_id from {group!r}")
    return value


def case_id_of(package: object) -> str:
    value = (
        package.get("case_id")
        if isinstance(package, Mapping)
        else getattr(package, "case_id", None)
    )
    if not value or not isinstance(value, str):
        raise SplitStoreMisconfigured(f"cannot read case_id from {package!r}")
    return value


def assign_split(
    group_id: str,
    *,
    seed: str = DEFAULT_SEED,
    ratios: Mapping[str, float] | None = None,
) -> Split:
    """Deterministic split for a group; order-independent and reproducible."""
    if not group_id:
        raise SplitStoreMisconfigured("group_id must be non-empty")
    weights = dict(ratios or DEFAULT_RATIOS)
    unknown = set(weights) - set(SPLIT_NAMES)
    if unknown:
        raise SplitStoreMisconfigured(f"unknown split names: {sorted(unknown)}")
    total = sum(weights.get(name, 0.0) for name in SPLIT_NAMES)
    if total <= 0:
        raise SplitStoreMisconfigured("split ratios must sum to a positive value")
    digest = hashlib.sha256(f"{seed}\x00{group_id}".encode()).hexdigest()
    point = int(digest[:16], 16) / float(1 << 64)
    cumulative = 0.0
    for name in SPLIT_NAMES:
        cumulative += weights.get(name, 0.0) / total
        if point < cumulative:
            return Split(name)
    return Split(SPLIT_NAMES[-1])


def assert_no_straddle(
    assignments: Mapping[str, str],
    packages: Iterable[object],
) -> dict[str, str]:
    """Verify every package inherits its group's single split.

    ``assignments`` maps ``group_id -> split``; ``packages`` are contract
    ``CasePackage`` objects (or mappings with ``case_id``/``group_id``).
    """
    case_splits: dict[str, str] = {}
    for package in packages:
        group_id = group_id_of(package)
        case_id = case_id_of(package)
        if group_id not in assignments:
            raise SplitStraddleError(f"no split assigned for group {group_id}")
        split = assignments[group_id]
        previous = case_splits.get(case_id)
        if previous is not None and previous != split:
            raise SplitStraddleError(f"case {case_id} straddles {previous} and {split}")
        case_splits[case_id] = split
    by_group: dict[str, set[str]] = {}
    for package in packages:
        by_group.setdefault(group_id_of(package), set()).add(assignments[group_id_of(package)])
    straddling = sorted(group for group, splits in by_group.items() if len(splits) > 1)
    if straddling:
        raise SplitStraddleError(f"groups straddle splits: {straddling}")
    return case_splits


def default_sealed_root() -> str:
    override = os.environ.get("HOP_SEALED_ROOT", "").strip()
    if override:
        return os.path.abspath(override)
    return os.path.join(os.path.expanduser("~"), ".local", "share", "hop", DEFAULT_SEALED_DIRNAME)


def validate_sealed_root(
    path: str,
    *,
    repository_root: str | None = None,
    worker_root: str | None = None,
) -> str:
    """Reject sealed roots that a worker could reach (repo, ro binds, run root)."""
    if not path or not os.path.isabs(path):
        raise SplitStoreMisconfigured(f"sealed root must be absolute: {path!r}")
    resolved = os.path.realpath(path)
    if resolved in ("/", os.path.expanduser("~")):
        raise SplitStoreMisconfigured(f"refusing an over-broad sealed root: {resolved}")
    repo = os.path.realpath(repository_root or _REPO_ROOT)
    forbidden = [repo]
    for bind in SYSTEM_RO_BINDS:
        if os.path.isdir(bind):
            forbidden.append(os.path.realpath(bind))
    if worker_root:
        forbidden.append(os.path.realpath(worker_root))
    for base in forbidden:
        if resolved == base or resolved.startswith(base.rstrip("/") + os.sep):
            raise SplitStoreMisconfigured(
                f"sealed root {resolved} is inside a worker-reachable path {base}"
            )
    return resolved


class SplitStore:
    """Deterministic split assignments persisted outside the repository."""

    def __init__(
        self,
        root: str,
        *,
        seed: str = DEFAULT_SEED,
        ratios: Mapping[str, float] | None = None,
        repository_root: str | None = None,
        worker_root: str | None = None,
    ):
        self.root = validate_sealed_root(
            root, repository_root=repository_root, worker_root=worker_root
        )
        self.seed = seed
        self.ratios = dict(ratios or DEFAULT_RATIOS)
        os.makedirs(self.root, mode=0o700, exist_ok=True)
        self.path = os.path.join(self.root, "assignments.json")

    # -- assignment --------------------------------------------------------
    def assign(self, groups: Iterable[object]) -> dict[str, str]:
        assignments = {
            group_id_of(group): assign_split(
                group_id_of(group), seed=self.seed, ratios=self.ratios
            ).value
            for group in groups
        }
        return dict(sorted(assignments.items()))

    def assign_packages(self, packages: Iterable[object]) -> dict[str, str]:
        """Per-case split, with the no-straddle invariant enforced."""
        packages = list(packages)
        assignments = self.assign(packages)
        return assert_no_straddle(assignments, packages)

    def save(self, assignments: Mapping[str, str]) -> str:
        payload = {
            "schema_version": SPLIT_SCHEMA_VERSION,
            "seed": self.seed,
            "ratios": dict(sorted(self.ratios.items())),
            "split": "assignments",
            "groups": dict(sorted(assignments.items())),
        }
        atomic_json(self.path, payload)
        return self.path

    def load(self) -> dict:
        import json

        if not os.path.exists(self.path):
            return {"schema_version": SPLIT_SCHEMA_VERSION, "groups": {}}
        with open(self.path, encoding="utf-8") as fh:
            payload = json.load(fh)
        if payload.get("schema_version") != SPLIT_SCHEMA_VERSION:
            raise SplitStoreMisconfigured("unsupported assignments schema")
        return payload

    def verify(self) -> dict:
        """Recompute every stored assignment; a mismatch is a hard error."""
        payload = self.load()
        stored = dict(payload.get("groups", {}))
        recomputed = {
            group_id: assign_split(group_id, seed=self.seed, ratios=self.ratios).value
            for group_id in stored
        }
        drifted = sorted(
            group_id for group_id, split in stored.items() if recomputed[group_id] != split
        )
        return {
            "ok": not drifted,
            "groups": len(stored),
            "drifted": drifted,
            "seed": self.seed,
        }

    def show(self, packages: Iterable[object] | None = None) -> dict:
        payload = self.load()
        result = {
            "root": self.root,
            "seed": self.seed,
            "ratios": dict(sorted(self.ratios.items())),
            "groups": dict(payload.get("groups", {})),
        }
        if packages is not None:
            result["cases"] = self.assign_packages(packages)
        return result

    def validate(self, packages: Iterable[object]) -> dict:
        """Full check: stored/recomputed agreement plus the no-straddle rule."""
        payload = self.load()
        assignments = dict(payload.get("groups", {}))
        return {
            "verify": self.verify(),
            "cases": assert_no_straddle(assignments, packages),
        }
