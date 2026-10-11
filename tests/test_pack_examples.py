"""AOP-021 example matrix: passing, failing, inconclusive, and overfit.

The matrix is computed once per module: every enabled pack is validated on
frozen snapshots with evaluator-owned hidden tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hop.evaluation.packs import PACK_SPECS, run_examples

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def matrix(tmp_path_factory):
    root = tmp_path_factory.mktemp("pack-examples")
    results = run_examples(
        benchmarks_root=REPO / "benchmarks",
        hidden_root=REPO / ".hidden" / "hidden-tests",
        work_root=root / "work",
        out_root=root / "out",
        packs=PACK_SPECS,
    )
    return root, {(result.pack_id, result.label): result for result in results}


def test_every_pack_passing_example_passes(matrix):
    _root, results = matrix
    for pack in PACK_SPECS:
        result = results[(pack.pack_id, "passing")]
        assert result.outcome == "pass" and result.verdict == "pass"
        assert result.error_class == ""
        assert result.completeness["complete"] is True


def test_every_pack_failing_example_fails(matrix):
    _root, results = matrix
    for pack in PACK_SPECS:
        result = results[(pack.pack_id, "failing")]
        assert result.outcome == "fail" and result.verdict == "fail"
        assert result.error_class == "tests_failed"
        assert result.completeness["complete"] is True


def test_every_pack_inconclusive_example_is_inconclusive(matrix):
    _root, results = matrix
    for pack in PACK_SPECS:
        result = results[(pack.pack_id, "inconclusive")]
        assert result.outcome == "inconclusive" and result.verdict == "error"
        assert result.error_class.startswith("baseline_hidden_")
        assert result.completeness["complete"] is True


def test_overfit_fixture_is_rejected(matrix):
    _root, results = matrix
    result = results[("pack:coding-feature", "overfit")]
    assert result.outcome == "fail" and result.verdict == "fail"
    assert result.error_class == "visible_test_overfit"
    step = next(item for item in result.steps if item["step"] == "overfit")
    assert step["visible_exit_code"] == 0
    assert step["only_visible_tests"] is True


def test_trajectories_and_completeness_are_written(matrix):
    root, _results = matrix
    out = root / "out"
    for pack in PACK_SPECS:
        for label in ("passing", "failing", "inconclusive"):
            artifact = out / pack.pack_id / f"{label}.json"
            assert artifact.is_file(), artifact
            text = artifact.read_text()
            assert '"completeness"' in text and '"steps"' in text
    # The overfit example is recorded alongside the failing example.
    assert (out / "pack:coding-feature" / "overfit.json").is_file()
