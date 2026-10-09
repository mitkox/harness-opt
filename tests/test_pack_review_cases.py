"""Labeled-case acceptance for the review pack (AOP-022).

Covers the passing/failing/inconclusive fixture classes, read-only execution,
duplicate/stale penalisation and precision/recall reporting with revisions.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from hop.evaluation.packs.review import runner
from hop.evaluation.packs.review.matcher import tree_digest
from hop.evaluation.packs.review.runner import (
    aggregate,
    default_cases_root,
    load_case,
    run_all,
    run_case,
)

EXPECTED = {
    "clean-refactor": "pass",
    "severity-calibration": "pass",
    "plausible-false-positive": "fail",
    "stale-lines": "fail",
    "duplicates": "fail",
    "unbindable-finding": "inconclusive",
}


def test_every_labeled_case_meets_its_expectation():
    outcomes = run_all()
    assert {outcome.case.case_id for outcome in outcomes} == set(EXPECTED)
    for outcome in outcomes:
        assert outcome.expectation_met, (outcome.case.case_id, outcome.expectation_detail)
        assert outcome.score.outcome == EXPECTED[outcome.case.case_id]


def test_clean_change_abstains_and_findings_are_not_comment_counts():
    outcomes = {outcome.case.case_id: outcome.score for outcome in run_all()}
    clean = outcomes["clean-refactor"]
    assert clean.abstained is True and clean.findings_total == 0
    # A clean change produces zero findings; severity calibration finds the two
    # labeled issues and nothing else, regardless of how terse the comments are.
    calibration = outcomes["severity-calibration"]
    assert calibration.true_positives == 2
    assert calibration.false_positives == 0
    assert calibration.recall == 1.0


def test_duplicates_and_stale_lines_are_visibly_penalised():
    outcomes = {outcome.case.case_id: outcome.score for outcome in run_all()}
    duplicates = outcomes["duplicates"]
    assert duplicates.true_positives == 1 and duplicates.duplicates == 1
    assert duplicates.outcome == "fail"
    stale = outcomes["stale-lines"]
    assert stale.stale == 1 and stale.false_negatives == 1
    assert stale.outcome == "fail"


def test_precision_recall_and_revisions_are_reported():
    outcomes = run_all()
    summary = aggregate(outcomes)
    assert summary["cases"] == len(EXPECTED)
    assert summary["expectations_met"] == len(EXPECTED)
    # Labeled totals across the fixture set: 4 TP, 1 plausible false positive,
    # 2 missed labels (one stale, one unbindable). Reported, never asserted as 1.
    assert (summary["true_positives"], summary["false_positives"], summary["false_negatives"]) == (
        4,
        1,
        2,
    )
    assert summary["precision"] == 0.8
    assert summary["recall"] == 0.6667
    assert summary["duplicates_penalised"] >= 1
    assert summary["stale_penalised"] >= 1
    assert summary["inconclusive"] == 1
    assert set(summary["revisions"]) == set(EXPECTED)
    for revision in summary["revisions"].values():
        assert revision.startswith("sha256:")


def test_review_execution_is_read_only(tmp_path):
    source = default_cases_root() / "severity-calibration"
    case_dir = tmp_path / "severity-calibration"
    shutil.copytree(source, case_dir)
    before = tree_digest(case_dir / "files")
    contents = {
        path: path.read_bytes()
        for path in sorted((case_dir / "files").rglob("*"))
        if path.is_file()
    }
    score = run_case(case_dir)
    assert score.outcome == "pass"
    assert tree_digest(case_dir / "files") == before
    assert {
        path: path.read_bytes()
        for path in sorted((case_dir / "files").rglob("*"))
        if path.is_file()
    } == contents


def test_a_modified_snapshot_is_reported_not_ignored(tmp_path, monkeypatch):
    source = default_cases_root() / "clean-refactor"
    case_dir = tmp_path / "clean-refactor"
    shutil.copytree(source, case_dir)
    revisions = iter(["sha256:before", "sha256:after"])
    monkeypatch.setattr(runner, "tree_digest", lambda root: next(revisions))
    score = run_case(case_dir)
    assert score.outcome == "fail"
    assert score.error_class == "reviewed_tree_modified"


def test_malformed_case_is_refused(tmp_path):
    directory = tmp_path / "broken"
    (directory / "files").mkdir(parents=True)
    (directory / "case.json").write_text("{not json\n")
    with pytest.raises(runner.ReviewCaseError):
        load_case(directory)


def test_default_cases_root_is_the_benchmark_directory():
    root = default_cases_root()
    assert root.name == "review"
    assert (root / "clean-refactor" / "case.json").is_file()
    assert isinstance(Path(root), Path)
