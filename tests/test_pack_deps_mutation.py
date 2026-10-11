"""Mutation-style fault detection for the deps pack (AOP-025).

The measured quantity is the mutation score: how many packaged behavioral faults
the candidate's tests kill. The examples prove the pack distinguishes real
behavioral tests from tests that merely execute code, and that a fault which
cannot be applied makes the case inconclusive instead of passing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hop.evaluation.packs.deps import runner

CASES = Path(__file__).resolve().parents[1] / "benchmarks" / "deps" / "fault-detection"


@pytest.fixture(scope="module")
def examples(tmp_path_factory):
    work = tmp_path_factory.mktemp("deps-faults")
    collected = {}
    for case_id in ("normalize-faults", "broken-fault"):
        collected[case_id] = {
            example.label: (example, result)
            for example, result in runner.run_fault_examples(CASES / case_id, work_root=work)
        }
    return collected


def _result(examples, case_id, label):
    example, result = examples[case_id][label]
    assert result.verdict == example.expected_verdict, (label, result.as_dict())
    if example.expected_error_class:
        assert result.error_class == example.expected_error_class, (label, result.as_dict())
    return result


def test_every_packaged_example_meets_its_expectation(examples):
    assert set(examples["normalize-faults"]) == {"passing", "failing", "trivial-coverage"}
    assert set(examples["broken-fault"]) == {"inconclusive"}
    for case_id in examples:
        for label in examples[case_id]:
            _result(examples, case_id, label)


def test_behavioral_tests_kill_every_packaged_fault(examples):
    result = _result(examples, "normalize-faults", "passing")
    assert result.mutation_score == 1.0
    assert result.killed == ["f1", "f2", "f3"]
    assert result.survived == []
    assert result.mandatory_survived == []
    assert result.baseline_exit == 0


def test_a_surviving_mandatory_fault_rejects_the_candidate(examples):
    result = _result(examples, "normalize-faults", "failing")
    assert result.error_class == "fault_survived"
    assert result.mandatory_survived == ["f1"]
    assert result.mutation_score < 1.0
    # The surviving fault is the behavioral one, not an environment problem.
    assert result.baseline_exit == 0


def test_trivial_coverage_increase_does_not_count(examples):
    """Executing the target without asserting behavior must not pass.

    The trivial candidate reaches the target module's lines (a coverage-style
    increase) yet kills zero faults; the verdict follows the mutation score, not
    the line count.
    """
    result = _result(examples, "normalize-faults", "trivial-coverage")
    assert result.error_class == "no_fault_detected"
    assert result.killed == []
    assert result.lines_executed > 0
    assert result.lines_total >= result.lines_executed


def test_unapplied_fault_is_inconclusive_not_a_pass(examples):
    result = _result(examples, "broken-fault", "inconclusive")
    assert result.outcome == "inconclusive"
    assert result.error_class == "fault_patch_unapplied"
    assert result.mutation_score == 0.0


def test_candidate_tests_that_fail_on_correct_code_are_rejected(tmp_path):
    patch = (
        "--- a/tests/test_visible.py\n"
        "+++ b/tests/test_visible.py\n"
        "@@ -1,7 +1,7 @@\n"
        '-"""Candidate-visible tests (initially no behavioral coverage)."""\n'
        '+"""Candidate-visible tests with a wrong expectation."""\n'
        " \n"
        " import textnorm\n"
        " \n"
        " \n"
        "-def test_module_imports():\n"
        "-    assert textnorm is not None\n"
        "+def test_wrong():\n"
        '+    assert textnorm.normalize("MiXeD") == "MiXeD"\n'
    )
    result = runner.run_fault_case(
        CASES / "normalize-faults",
        candidate_patch=patch,
        candidate_label="wrong-expectation",
        work_root=tmp_path,
    )
    assert result.verdict == "fail"
    assert result.error_class == "candidate_tests_fail_on_baseline"
    assert result.mutation_score == 0.0
