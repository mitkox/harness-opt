"""Finding matcher: bind to the reviewed revision, deduplicate, and score.

Read-only by construction: nothing here opens a snapshot for writing.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from .specs import (
    BoundFinding,
    FindingStatus,
    LabeledIssue,
    Outcome,
    ReviewFinding,
    ReviewScore,
)


def tree_digest(root: str | Path) -> str:
    """Content-addressed revision identity of a frozen snapshot (read-only)."""
    base = Path(root)
    digest = hashlib.sha256()
    for path in sorted(p for p in base.rglob("*") if p.is_file()):
        digest.update(str(path.relative_to(base)).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def snapshot_lines(root: str | Path, path: str) -> list[str] | None:
    base = Path(root).resolve()
    target = (base / path).resolve()
    if not target.is_relative_to(base) or not target.is_file():
        return None
    return target.read_text(encoding="utf-8", errors="replace").splitlines()


def bind_findings(findings: list[ReviewFinding], root: str | Path) -> list[BoundFinding]:
    """Bind every finding to the snapshot and mark duplicates.

    A finding is stale when its line is outside the file or when its own
    ``line_text`` anchor no longer matches the snapshot line. Duplicates are
    keyed on (path, line, rule, severity); only the first occurrence is scored.
    """
    bound: list[BoundFinding] = []
    seen: dict[tuple[str, int, str, str], int] = {}
    for finding in findings:
        lines = snapshot_lines(root, finding.path)
        if lines is None:
            bound.append(BoundFinding(finding=finding, status=FindingStatus.MISSING_FILE.value))
            continue
        if finding.line < 1 or finding.line > len(lines):
            bound.append(BoundFinding(finding=finding, status=FindingStatus.STALE.value))
            continue
        observed = lines[finding.line - 1]
        anchor = finding.line_text.strip()
        if anchor and anchor != observed.strip():
            bound.append(
                BoundFinding(
                    finding=finding,
                    status=FindingStatus.STALE.value,
                    observed_line_text=observed,
                )
            )
            continue
        key = (finding.path, finding.line, finding.rule, finding.severity)
        duplicate_of = seen.get(key, -1)
        if duplicate_of < 0:
            seen[key] = len(bound)
        bound.append(
            BoundFinding(
                finding=finding,
                status=FindingStatus.FRESH.value,
                observed_line_text=observed,
                duplicate_of=duplicate_of,
            )
        )
    return bound


def score_review(
    case_id: str,
    root: str | Path,
    revision: str,
    findings: list[ReviewFinding],
    labels: list[LabeledIssue],
) -> ReviewScore:
    """Score raw findings against labeled issues for one frozen revision."""
    score = ReviewScore(case_id=case_id, revision=revision, labels=list(labels))
    bound = bind_findings(findings, root)
    score.bound = bound
    score.findings_total = len(bound)
    matched_labels: set[int] = set()
    for item in bound:
        if item.status == FindingStatus.MISSING_FILE.value:
            score.missing_files += 1
            continue
        if item.status == FindingStatus.STALE.value:
            score.stale += 1
            continue
        if item.duplicate_of >= 0:
            score.duplicates += 1
            continue
        score.findings_unique += 1
        match = -1
        for index, label in enumerate(labels):
            if index in matched_labels:
                continue
            if (label.path, label.line, label.rule) == (
                item.finding.path,
                item.finding.line,
                item.finding.rule,
            ):
                match = index
                break
        if match < 0:
            score.false_positives += 1
            continue
        matched_labels.add(match)
        item.matched_label = match
        score.true_positives += 1
        if labels[match].severity != item.finding.severity:
            score.severity_mismatches += 1
    score.false_negatives = len(labels) - len(matched_labels)

    clean_case = not labels
    score.abstained = clean_case and score.findings_unique == 0
    if score.missing_files:
        score.outcome = Outcome.INCONCLUSIVE.value
        score.error_class = "unbindable_finding"
    elif (
        score.false_positives == 0
        and score.false_negatives == 0
        and score.duplicates == 0
        and score.stale == 0
        and score.severity_mismatches == 0
    ):
        score.outcome = Outcome.PASS.value
    else:
        score.outcome = Outcome.FAIL.value
    return score
