"""Local diff/commit review pack (AOP-022, M4).

Public surface:

* :func:`hop.evaluation.packs.review.runner.run_case` / :func:`run_all` --
  read-only scoring of a reviewer's findings against labeled frozen snapshots;
* :func:`hop.evaluation.packs.review.matcher.score_review` -- binding,
  deduplication, stale-line detection and precision/recall;
* fixtures under ``benchmarks/review/``.

Reviewed revisions are content-addressed (``tree_digest``); the reviewed tree is
compared before and after scoring and a modification is a failure. Findings are
never counted as quality: comment volume is not scored.
"""

from .matcher import bind_findings, score_review, snapshot_lines, tree_digest
from .runner import (
    CaseOutcome,
    ReviewCase,
    ReviewCaseError,
    aggregate,
    default_cases_root,
    load_case,
    run_all,
    run_case,
)
from .specs import (
    REVIEW_PACK_VERSION,
    BoundFinding,
    FindingStatus,
    LabeledIssue,
    Outcome,
    ReviewFinding,
    ReviewScore,
    Severity,
)

__all__ = [
    "REVIEW_PACK_VERSION",
    "BoundFinding",
    "CaseOutcome",
    "FindingStatus",
    "LabeledIssue",
    "Outcome",
    "ReviewCase",
    "ReviewCaseError",
    "ReviewFinding",
    "ReviewScore",
    "Severity",
    "aggregate",
    "bind_findings",
    "default_cases_root",
    "load_case",
    "run_all",
    "run_case",
    "score_review",
    "snapshot_lines",
    "tree_digest",
]
