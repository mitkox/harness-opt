"""Typed specs for the test/dependency maintenance pack (AOP-025).

Two case families, both measured rather than asserted:

* **fault detection** -- a case repo carries a small library and a set of
  behavioral *faults* (unified diffs). The candidate contributes tests; the pack
  applies each fault to a frozen snapshot and runs the candidate's tests. A fault
  is *killed* when the tests fail on it. The verdict is the measured mutation
  score, so a test that only executes code (high line coverage, no assertion)
  kills nothing and cannot pass.
* **dependency upgrade** -- a case repo pins a local package; each declared
  upgrade names the target version and the local source tree the artifact is
  built from. Artifacts are resolved only from the local case directory (no
  index, no network), their sha256 is recorded, and trusted compatibility tests
  decide whether the intended compatibility is preserved. A declared target with
  no local artifact is reported as ``blocked_missing_artifact`` -- never as a
  pass.

``hidden_ref`` from :mod:`hop.contracts.evaluation` is opaque to this pack: it
never resolves sealed paths or opens a sealed store.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum

DEPS_PACK_VERSION = "deps-pack-v1"


class MaintenanceKind(str, Enum):
    FAULT_DETECTION = "fault-detection"
    DEPENDENCY_UPGRADE = "dependency-upgrade"


class ExampleClass(str, Enum):
    PASSING = "passing"
    FAILING = "failing"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True)
class FaultSpec:
    """One injected behavioral fault, expressed as a patch over the case repo."""

    fault_id: str
    patch_file: str
    description: str = ""

    def manifest(self) -> dict:
        return {
            "fault_id": self.fault_id,
            "patch_file": self.patch_file,
            "description": self.description,
        }


@dataclass(frozen=True)
class FaultExample:
    """One packaged example for a fault case (passing/failing/inconclusive)."""

    label: str
    example_class: ExampleClass
    candidate_patch_file: str = ""
    expected_verdict: str = ""
    expected_error_class: str = ""
    notes: str = ""

    def manifest(self) -> dict:
        return {
            "label": self.label,
            "example_class": self.example_class.value,
            "candidate_patch_file": self.candidate_patch_file,
            "expected_verdict": self.expected_verdict,
            "expected_error_class": self.expected_error_class,
        }


@dataclass(frozen=True)
class FaultCaseSpec:
    """A fault-detection case."""

    case_id: str
    repo_subdir: str
    target_module: str
    test_files: tuple[str, ...]
    faults: tuple[FaultSpec, ...]
    mandatory_faults: tuple[str, ...] = ()
    minimum_kills: int = 0
    title: str = ""
    notes: str = ""
    examples: tuple[FaultExample, ...] = ()

    def manifest(self) -> dict:
        return {
            "case_id": self.case_id,
            "kind": MaintenanceKind.FAULT_DETECTION.value,
            "repo_subdir": self.repo_subdir,
            "target_module": self.target_module,
            "test_files": list(self.test_files),
            "faults": [fault.manifest() for fault in self.faults],
            "mandatory_faults": list(self.mandatory_faults),
            "minimum_kills": int(self.minimum_kills),
        }

    def digest(self) -> str:
        payload = json.dumps(self.manifest(), sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()

    def mandatory(self) -> tuple[str, ...]:
        return self.mandatory_faults or tuple(fault.fault_id for fault in self.faults)


@dataclass(frozen=True)
class UpgradeSpec:
    """One declared dependency upgrade target."""

    package: str
    from_version: str
    to_version: str
    source_dir: str
    expectation: str = "pass"
    notes: str = ""

    def manifest(self) -> dict:
        return {
            "package": self.package,
            "from_version": self.from_version,
            "to_version": self.to_version,
            "source_dir": self.source_dir,
            "expectation": self.expectation,
        }


@dataclass(frozen=True)
class UpgradeCaseSpec:
    """A dependency-upgrade case."""

    case_id: str
    repo_subdir: str
    compatibility_tests: tuple[str, ...]
    upgrades: tuple[UpgradeSpec, ...]
    title: str = ""
    notes: str = ""

    def manifest(self) -> dict:
        return {
            "case_id": self.case_id,
            "kind": MaintenanceKind.DEPENDENCY_UPGRADE.value,
            "repo_subdir": self.repo_subdir,
            "compatibility_tests": list(self.compatibility_tests),
            "upgrades": [upgrade.manifest() for upgrade in self.upgrades],
        }

    def digest(self) -> str:
        payload = json.dumps(self.manifest(), sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()


@dataclass
class FaultCaseResult:
    """Measured outcome of one candidate against one fault case."""

    case_id: str
    candidate: str
    verdict: str
    outcome: str
    error_class: str
    mutation_score: float
    killed: list[str] = field(default_factory=list)
    survived: list[str] = field(default_factory=list)
    mandatory_survived: list[str] = field(default_factory=list)
    lines_executed: int = 0
    lines_total: int = 0
    baseline_exit: int | None = None
    case_digest: str = ""
    steps: list[dict] = field(default_factory=list)
    evidence: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "candidate": self.candidate,
            "verdict": self.verdict,
            "outcome": self.outcome,
            "error_class": self.error_class,
            "mutation_score": self.mutation_score,
            "killed": list(self.killed),
            "survived": list(self.survived),
            "mandatory_survived": list(self.mandatory_survived),
            "lines_executed": self.lines_executed,
            "lines_total": self.lines_total,
            "baseline_exit": self.baseline_exit,
            "case_digest": self.case_digest,
            "steps": self.steps,
            "evidence": self.evidence,
        }


@dataclass
class UpgradeResult:
    """Measured outcome of one declared dependency upgrade."""

    case_id: str
    package: str
    to_version: str
    expectation: str
    verdict: str
    outcome: str
    error_class: str
    artifact_path: str = ""
    artifact_sha256: str = ""
    source_digest: str = ""
    resolution: str = ""
    compatibility_exit: int | None = None
    steps: list[dict] = field(default_factory=list)
    evidence: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "package": self.package,
            "to_version": self.to_version,
            "expectation": self.expectation,
            "verdict": self.verdict,
            "outcome": self.outcome,
            "error_class": self.error_class,
            "artifact_path": self.artifact_path,
            "artifact_sha256": self.artifact_sha256,
            "source_digest": self.source_digest,
            "resolution": self.resolution,
            "compatibility_exit": self.compatibility_exit,
            "steps": self.steps,
            "evidence": self.evidence,
        }


@dataclass
class UpgradeCaseResult:
    case_id: str
    case_digest: str
    upgrades: list[UpgradeResult] = field(default_factory=list)
    baseline_exit: int | None = None
    baseline_artifact_sha256: str = ""
    baseline_resolution: str = ""
    baseline_error: str = ""

    def as_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "case_digest": self.case_digest,
            "baseline_exit": self.baseline_exit,
            "baseline_artifact_sha256": self.baseline_artifact_sha256,
            "baseline_resolution": self.baseline_resolution,
            "baseline_error": self.baseline_error,
            "upgrades": [upgrade.as_dict() for upgrade in self.upgrades],
        }
