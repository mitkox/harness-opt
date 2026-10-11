"""Typed review-pack specifications (AOP-022).

A review case is a frozen source snapshot plus labeled issues. A reviewer
(a local model or a scripted fixture) produces raw findings; the matcher binds
each finding to the exact snapshot revision, deduplicates it, and scores it
against the labels. Comment counts are never a quality metric here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

REVIEW_PACK_VERSION = "review-pack-v1"


class Severity(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class Outcome(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"


class FindingStatus(str, Enum):
    FRESH = "fresh"
    STALE = "stale"
    MISSING_FILE = "missing_file"


@dataclass(frozen=True)
class ReviewFinding:
    """One raw finding as reported by a reviewer against a revision."""

    path: str
    line: int
    rule: str
    severity: str = Severity.MEDIUM.value
    message: str = ""
    #: Optional anchor: the reviewer's copy of the source line. When present it
    #: must match the snapshot, which catches findings moved to a new revision.
    line_text: str = ""


@dataclass(frozen=True)
class LabeledIssue:
    """A ground-truth issue bound to the same revision."""

    path: str
    line: int
    rule: str
    severity: str = Severity.MEDIUM.value


@dataclass
class BoundFinding:
    finding: ReviewFinding
    status: str
    observed_line_text: str = ""
    duplicate_of: int = -1
    matched_label: int = -1


@dataclass
class ReviewScore:
    case_id: str
    revision: str
    findings_total: int = 0
    findings_unique: int = 0
    duplicates: int = 0
    stale: int = 0
    missing_files: int = 0
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    severity_mismatches: int = 0
    abstained: bool = False
    outcome: str = Outcome.INCONCLUSIVE.value
    error_class: str = ""
    bound: list[BoundFinding] = field(default_factory=list)
    labels: list[LabeledIssue] = field(default_factory=list)

    @property
    def precision(self) -> float:
        denominator = self.true_positives + self.false_positives
        return 1.0 if denominator == 0 else self.true_positives / denominator

    @property
    def recall(self) -> float:
        denominator = self.true_positives + self.false_negatives
        return 1.0 if denominator == 0 else self.true_positives / denominator

    def as_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "revision": self.revision,
            "findings_total": self.findings_total,
            "findings_unique": self.findings_unique,
            "duplicates": self.duplicates,
            "stale": self.stale,
            "missing_files": self.missing_files,
            "true_positives": self.true_positives,
            "false_positives": self.false_positives,
            "false_negatives": self.false_negatives,
            "severity_mismatches": self.severity_mismatches,
            "abstained": self.abstained,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "outcome": self.outcome,
            "error_class": self.error_class,
        }
