"""Read-only review-pack execution over frozen snapshots (AOP-022).

A case is a directory with ``case.json`` (labels + raw reviewer findings +
expectation) and a ``files/`` snapshot. Running a case never writes to the
snapshot; the tree digest is compared before and after and any change is
reported as a failure rather than ignored.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .matcher import score_review, tree_digest
from .specs import LabeledIssue, Outcome, ReviewFinding, ReviewScore


class ReviewCaseError(ValueError):
    """A review case is missing, malformed, or its snapshot is unusable."""


@dataclass(frozen=True)
class ReviewCase:
    case_id: str
    directory: Path
    files_root: Path
    labels: tuple[LabeledIssue, ...]
    findings: tuple[ReviewFinding, ...]
    expectation: dict


def default_cases_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "benchmarks" / "review"
        if candidate.is_dir():
            return candidate
    raise ReviewCaseError("benchmarks/review is not present")


def _as_issues(raw: list[dict]) -> tuple[LabeledIssue, ...]:
    return tuple(
        LabeledIssue(
            path=str(item["path"]),
            line=int(item["line"]),
            rule=str(item.get("rule", "")),
            severity=str(item.get("severity", "medium")),
        )
        for item in raw
    )


def _as_findings(raw: list[dict]) -> tuple[ReviewFinding, ...]:
    return tuple(
        ReviewFinding(
            path=str(item["path"]),
            line=int(item["line"]),
            rule=str(item.get("rule", "")),
            severity=str(item.get("severity", "medium")),
            message=str(item.get("message", "")),
            line_text=str(item.get("line_text", "")),
        )
        for item in raw
    )


def load_case(directory: str | Path) -> ReviewCase:
    directory = Path(directory)
    manifest_path = directory / "case.json"
    if not manifest_path.is_file():
        raise ReviewCaseError(f"missing case.json in {directory}")
    try:
        payload = json.loads(manifest_path.read_text())
    except json.JSONDecodeError as exc:
        raise ReviewCaseError(f"{manifest_path} is not valid JSON: {exc}") from exc
    files_root = directory / "files"
    if not files_root.is_dir():
        raise ReviewCaseError(f"missing files/ snapshot in {directory}")
    case_id = str(payload.get("case_id", directory.name))
    if case_id != directory.name:
        raise ReviewCaseError(f"case_id {case_id!r} does not match directory {directory.name!r}")
    return ReviewCase(
        case_id=case_id,
        directory=directory,
        files_root=files_root,
        labels=_as_issues(payload.get("labels", [])),
        findings=_as_findings(payload.get("findings", [])),
        expectation=dict(payload.get("expects", {})),
    )


def run_case(directory: str | Path) -> ReviewScore:
    """Score one case, proving the reviewed snapshot was not modified."""
    case = load_case(directory)
    before = tree_digest(case.files_root)
    score = score_review(
        case.case_id,
        case.files_root,
        before,
        list(case.findings),
        list(case.labels),
    )
    after = tree_digest(case.files_root)
    if after != before:
        score.outcome = Outcome.FAIL.value
        score.error_class = "reviewed_tree_modified"
    return score


@dataclass(frozen=True)
class CaseOutcome:
    case: ReviewCase
    score: ReviewScore
    expected_outcome: str = ""
    expectation_met: bool = True
    expectation_detail: str = ""


def run_all(cases_root: str | Path | None = None) -> list[CaseOutcome]:
    root = Path(cases_root) if cases_root is not None else default_cases_root()
    outcomes: list[CaseOutcome] = []
    for directory in sorted(p for p in root.iterdir() if p.is_dir()):
        case = load_case(directory)
        score = run_case(directory)
        expected = str(case.expectation.get("outcome", ""))
        detail = ""
        met = True
        if expected:
            met = score.outcome == expected
            if not met:
                detail = f"expected outcome {expected}, got {score.outcome}"
        if met and "abstained" in case.expectation:
            want = bool(case.expectation["abstained"])
            met = score.abstained is want
            if not met:
                detail = f"expected abstained={want}, got {score.abstained}"
        outcomes.append(
            CaseOutcome(
                case=case,
                score=score,
                expected_outcome=expected,
                expectation_met=met,
                expectation_detail=detail,
            )
        )
    return outcomes


def aggregate(outcomes: list[CaseOutcome]) -> dict:
    tp = sum(o.score.true_positives for o in outcomes)
    fp = sum(o.score.false_positives for o in outcomes)
    fn = sum(o.score.false_negatives for o in outcomes)
    precision = 1.0 if tp + fp == 0 else tp / (tp + fp)
    recall = 1.0 if tp + fn == 0 else tp / (tp + fn)
    return {
        "cases": len(outcomes),
        "expectations_met": sum(1 for o in outcomes if o.expectation_met),
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "duplicates_penalised": sum(o.score.duplicates for o in outcomes),
        "stale_penalised": sum(o.score.stale for o in outcomes),
        "inconclusive": sum(1 for o in outcomes if o.score.outcome == Outcome.INCONCLUSIVE.value),
        "revisions": {o.case.case_id: o.score.revision for o in outcomes},
    }
