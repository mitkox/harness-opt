"""Typed pack specifications for the coding/debugging workflow packs (AOP-021).

A pack binds one benchmark case to the independent success evidence that must
hold for it: hidden behavioral tests, regression tests, and API/scope review.
Specs are declarative and content-addressed; the runner decides nothing that is
not written here.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum

PACK_VERIFIER_VERSION = "pack-verifier-v1"


class PackKind(str, Enum):
    FEATURE = "feature"
    REFACTOR = "refactor"
    REPRODUCER = "reproducer"


class VerifierKind(str, Enum):
    BEHAVIORAL = "behavioral"
    REGRESSION = "regression"
    SCOPE = "scope"


class ExampleClass(str, Enum):
    PASSING = "passing"
    FAILING = "failing"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True)
class VerifierSpec:
    """One trusted verifier: hidden files, exact expected tests, and its kind."""

    kind: VerifierKind
    test_files: tuple[str, ...]
    expected_tests: tuple[str, ...]
    mandatory_tests: tuple[str, ...] = ()
    minimum_test_count: int = 1

    def manifest(self, verifier_id: str) -> dict:
        mandatory = self.mandatory_tests or self.expected_tests
        if not self.test_files or not self.expected_tests:
            raise ValueError(f"verifier {verifier_id} must declare files and expected tests")
        if not set(mandatory).issubset(set(self.expected_tests)):
            raise ValueError(f"verifier {verifier_id} mandatory tests must be expected tests")
        if self.minimum_test_count > len(self.expected_tests):
            raise ValueError(f"verifier {verifier_id} minimum count exceeds expected tests")
        return {
            "verifier_id": verifier_id,
            "version": PACK_VERIFIER_VERSION,
            "kind": self.kind.value,
            "test_files": list(self.test_files),
            "expected_tests": list(self.expected_tests),
            "mandatory_tests": list(mandatory),
            "minimum_test_count": int(self.minimum_test_count),
        }


@dataclass(frozen=True)
class PackSpec:
    """A workflow pack over one case: evidence, scope, and baseline expectation."""

    pack_id: str
    kind: PackKind
    case_id: str
    title: str
    verifiers: tuple[VerifierSpec, ...]
    allowed_paths: tuple[str, ...]
    protected_paths: tuple[str, ...] = ()
    #: What a correct, unpatched baseline must show (reproducer: hidden tests fail).
    baseline_hidden: str = "failing"
    #: Public command whose pass is the overfitting signal (empty disables it).
    visible_command: tuple[str, ...] = ()
    #: Candidate-visible test paths; a patch touching only these while hidden
    #: evidence fails is visible-test overfitting, not a hidden regression.
    visible_test_paths: tuple[str, ...] = ()
    notes: str = ""

    def manifest(self) -> dict:
        return {
            "pack_id": self.pack_id,
            "kind": self.kind.value,
            "case_id": self.case_id,
            "title": self.title,
            "verifiers": [
                v.manifest(f"verifier:{self.case_id}:{v.kind.value}") for v in self.verifiers
            ],
            "allowed_paths": list(self.allowed_paths),
            "protected_paths": list(self.protected_paths),
            "baseline_hidden": self.baseline_hidden,
            "visible_command": list(self.visible_command),
            "visible_test_paths": list(self.visible_test_paths),
        }

    def digest(self) -> str:
        payload = json.dumps(self.manifest(), sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True)
class PackExample:
    """One example class for a pack.

    ``candidate_patch_file`` is relative to
    ``benchmarks/packs/candidates/<case_id>/``; ``baseline_patch_file`` (when
    set) forces an invalid baseline so the case must end inconclusive.
    """

    pack_id: str
    example_class: ExampleClass
    label: str = ""
    candidate_patch_file: str = ""
    baseline_patch_file: str = ""
    expected_outcome: str = "pass"
    expected_error_class: str = ""
    notes: str = ""

    @property
    def example_label(self) -> str:
        return self.label or self.example_class.value


@dataclass
class PackResult:
    """Outcome of validating one candidate against one pack on a frozen snapshot."""

    pack_id: str
    example_class: str
    candidate_digest: str
    outcome: str
    verdict: str
    error_class: str
    snapshot_digest: str
    label: str = ""
    baseline: dict = field(default_factory=dict)
    steps: list[dict] = field(default_factory=list)
    evidence: dict = field(default_factory=dict)
    completeness: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "pack_id": self.pack_id,
            "example_class": self.example_class,
            "label": self.label,
            "candidate_digest": self.candidate_digest,
            "outcome": self.outcome,
            "verdict": self.verdict,
            "error_class": self.error_class,
            "snapshot_digest": self.snapshot_digest,
            "baseline": self.baseline,
            "steps": self.steps,
            "evidence": self.evidence,
            "completeness": self.completeness,
        }
