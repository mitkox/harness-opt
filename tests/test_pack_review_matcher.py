"""Unit tests for the review-pack matcher (AOP-022): binding, dedup, scoring."""

from __future__ import annotations

from pathlib import Path

from hop.evaluation.packs.review.matcher import bind_findings, score_review, tree_digest
from hop.evaluation.packs.review.specs import LabeledIssue, Outcome, ReviewFinding


def _snapshot(tmp_path: Path) -> Path:
    root = tmp_path / "files"
    (root / "src").mkdir(parents=True)
    (root / "src/app.py").write_text("alpha\nbeta\ngamma\n")
    return root


def test_tree_digest_is_content_addressed_and_read_only(tmp_path):
    root = _snapshot(tmp_path)
    first = tree_digest(root)
    assert first.startswith("sha256:")
    assert tree_digest(root) == first
    (root / "src/app.py").write_text("alpha\nBETA\ngamma\n")
    assert tree_digest(root) != first


def test_bind_marks_fresh_stale_missing_and_duplicate(tmp_path):
    root = _snapshot(tmp_path)
    findings = [
        ReviewFinding(path="src/app.py", line=2, rule="r", severity="low"),
        ReviewFinding(path="src/app.py", line=99, rule="r", severity="low"),
        ReviewFinding(path="src/app.py", line=1, rule="r", severity="low", line_text="WRONG"),
        ReviewFinding(path="src/nope.py", line=1, rule="r"),
        ReviewFinding(path="src/app.py", line=2, rule="r", severity="low", message="again"),
    ]
    bound = bind_findings(findings, root)
    assert [item.status for item in bound] == ["fresh", "stale", "stale", "missing_file", "fresh"]
    assert bound[4].duplicate_of == 0
    assert bound[0].observed_line_text == "beta"


def test_clean_case_abstains_and_never_scores_comments(tmp_path):
    root = _snapshot(tmp_path)
    score = score_review("clean", root, tree_digest(root), [], [])
    assert score.abstained is True
    assert score.outcome == Outcome.PASS.value
    assert score.precision == 1.0 and score.recall == 1.0


def test_findings_on_a_clean_case_are_false_positives(tmp_path):
    root = _snapshot(tmp_path)
    score = score_review(
        "clean",
        root,
        tree_digest(root),
        [ReviewFinding(path="src/app.py", line=1, rule="style")],
        [],
    )
    assert score.abstained is False
    assert score.false_positives == 1
    assert score.outcome == Outcome.FAIL.value


def test_precision_recall_severity_and_penalties(tmp_path):
    root = _snapshot(tmp_path)
    labels = [
        LabeledIssue(path="src/app.py", line=1, rule="a", severity="high"),
        LabeledIssue(path="src/app.py", line=3, rule="c", severity="low"),
    ]
    findings = [
        ReviewFinding(path="src/app.py", line=1, rule="a", severity="low"),  # TP, wrong severity
        ReviewFinding(path="src/app.py", line=2, rule="b", severity="low"),  # FP
        ReviewFinding(path="src/app.py", line=99, rule="c"),  # stale
        ReviewFinding(path="src/app.py", line=1, rule="a", severity="low", message="dup"),
    ]
    score = score_review("mixed", root, tree_digest(root), findings, labels)
    assert (score.true_positives, score.false_positives, score.false_negatives) == (1, 1, 1)
    assert score.severity_mismatches == 1
    assert score.stale == 1
    assert score.duplicates == 1
    assert score.precision == 0.5 and score.recall == 0.5
    assert score.outcome == Outcome.FAIL.value


def test_unbindable_finding_is_inconclusive_not_a_false_pass(tmp_path):
    root = _snapshot(tmp_path)
    score = score_review(
        "unbindable",
        root,
        tree_digest(root),
        [ReviewFinding(path="src/missing.py", line=1, rule="a")],
        [LabeledIssue(path="src/missing.py", line=1, rule="a")],
    )
    assert score.missing_files == 1
    assert score.outcome == Outcome.INCONCLUSIVE.value
    assert score.error_class == "unbindable_finding"
