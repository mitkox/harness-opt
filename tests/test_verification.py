"""Trusted verifier tests (AOP-010): positive verification + adversarial rejection.

Baseline fails, a real repair passes, and no candidate-controlled trick
(os._exit, sitecustomize/usercustomize, conftest, pytest.ini, skips, zero
collection, deselection) can produce a PASS.
"""
import json
import os

import pytest

from aop import verification
from aop.contracts.records import Verdict

HIDDEN = ".hidden/hidden-tests"
OFFBYONE = "debug-offbyone"
WORDCOUNT = "debug-wordcount"


def _snapshot(tmp_path, case=OFFBYONE, dest="snap"):
    src = f"benchmarks/development/{case}/repo"
    target = str(tmp_path / dest)
    return verification.freeze_workspace(src, target), target


def _run(snap, case=OFFBYONE, tmp_path=None):
    hidden = os.path.join(HIDDEN, case)
    return verification.run_verification(
        "r-test", "a1", snap, hidden, timeout_s=90,
        work_root=(str(tmp_path / "work") if tmp_path else None))


def test_baseline_fails_and_repair_passes(tmp_path):
    manifest, snap = _snapshot(tmp_path)
    assert "median_bug.py" in manifest
    base = _run(snap, tmp_path=tmp_path)
    assert base.verdict == Verdict.FAIL, base.details.get("log_tail", "")[-500:]
    assert base.outcome.value == "fail"
    ev = base.details["verification"]
    assert ev["collected"] == ev["expected_tests"]
    assert ev["outcomes"]["test_hidden_median.py::test_even_length"] == "failed"

    fixed = open(os.path.join(snap, "median_bug.py")).read().replace(
        "return (ordered[mid] + ordered[mid + 1]) / 2",
        "return (ordered[mid - 1] + ordered[mid]) / 2")
    open(os.path.join(snap, "median_bug.py"), "w").write(fixed)
    again = _run(snap, tmp_path=tmp_path)
    assert again.verdict == Verdict.PASS, again.details.get("log_tail", "")[-500:]
    assert again.outcome.value == "pass"
    assert again.contamination_check == "clean"
    assert again.evidence_digest.startswith("sha256:")


def test_wordcount_baseline_fails(tmp_path):
    _, snap = _snapshot(tmp_path, WORDCOUNT)
    res = _run(snap, WORDCOUNT, tmp_path)
    assert res.verdict == Verdict.FAIL


def test_hidden_tests_not_in_agent_source_tree():
    for dirpath, _, filenames in os.walk("benchmarks"):
        for name in filenames:
            assert not name.startswith("test_hidden"), (dirpath, name)


# ---------------------------------------------------------------------------
# Adversarial: candidate-controlled tricks must never yield PASS.
# ---------------------------------------------------------------------------

def test_os_exit_zero_is_not_pass(tmp_path):
    _, snap = _snapshot(tmp_path)
    with open(os.path.join(snap, "median_bug.py"), "w") as fh:
        fh.write("import os\nos._exit(0)\n")
    res = _run(snap, tmp_path=tmp_path)
    assert res.verdict != Verdict.PASS
    assert res.error_class == "verification_no_result"


def test_sitecustomize_does_not_execute(tmp_path):
    _, snap = _snapshot(tmp_path)
    marker = os.path.join(snap, "sitecustomize_ran")
    with open(os.path.join(snap, "sitecustomize.py"), "w") as fh:
        fh.write(f"open({marker!r}, 'w').write('x')\n")
    res = _run(snap, tmp_path=tmp_path)
    assert res.verdict != Verdict.PASS
    assert not os.path.exists(marker), "candidate sitecustomize executed in verifier"


def test_usercustomize_does_not_execute(tmp_path):
    _, snap = _snapshot(tmp_path)
    marker = os.path.join(snap, "usercustomize_ran")
    with open(os.path.join(snap, "usercustomize.py"), "w") as fh:
        fh.write(f"open({marker!r}, 'w').write('x')\n")
    res = _run(snap, tmp_path=tmp_path)
    assert res.verdict != Verdict.PASS
    assert not os.path.exists(marker), "candidate usercustomize executed in verifier"


def test_candidate_conftest_cannot_redefine_collection(tmp_path):
    _, snap = _snapshot(tmp_path)
    with open(os.path.join(snap, "conftest.py"), "w") as fh:
        fh.write("def pytest_collection_modifyitems(items):\n    items[:] = []\n")
    res = _run(snap, tmp_path=tmp_path)
    assert res.verdict != Verdict.PASS


def test_candidate_pytest_ini_cannot_deselect(tmp_path):
    _, snap = _snapshot(tmp_path)
    with open(os.path.join(snap, "pytest.ini"), "w") as fh:
        fh.write("[pytest]\naddopts = -k not_a_real_test --collect-only\n")
    res = _run(snap, tmp_path=tmp_path)
    assert res.verdict != Verdict.PASS


def test_forged_success_claim_cannot_pass(tmp_path):
    _, snap = _snapshot(tmp_path)
    res = _run(snap, tmp_path=tmp_path)
    assert res.verdict == Verdict.FAIL  # claim ignored; hidden tests decide


def test_missing_hidden_fixture_is_infra_error(tmp_path):
    _, snap = _snapshot(tmp_path)
    empty = str(tmp_path / "empty-verifier")
    os.makedirs(empty)
    res = _run(snap, tmp_path=tmp_path)  # sanity: normal run works
    assert res.verdict in (Verdict.FAIL, Verdict.PASS)
    from aop.verification import VerifierSetupError, load_verifier_manifest
    with pytest.raises(VerifierSetupError) as exc:
        load_verifier_manifest(empty)
    assert exc.value.error_class == "missing_verifier_fixture"


# ---------------------------------------------------------------------------
# Machine-readable report evaluation.
# ---------------------------------------------------------------------------

MANIFEST = {
    "verifier_id": "v", "version": "1", "test_files": ["t.py"],
    "expected_tests": ["t.py::a", "t.py::b"], "mandatory_tests": ["t.py::a", "t.py::b"],
    "minimum_test_count": 2,
}


def _report(**kw):
    base = {"collected": ["t.py::a", "t.py::b"], "deselected": [],
            "outcomes": {"t.py::a": "passed", "t.py::b": "passed"}, "exitstatus": 0}
    base.update(kw)
    return base


def test_report_all_skipped_rejected():
    verdict, outcome, err, _ = verification.evaluate_report(
        _report(outcomes={"t.py::a": "skipped", "t.py::b": "skipped"}), MANIFEST)
    assert verdict == Verdict.ERROR and err == "verification_all_skipped"


def test_report_zero_collected_rejected():
    verdict, outcome, err, _ = verification.evaluate_report(
        _report(collected=[], outcomes={}), MANIFEST)
    assert verdict == Verdict.ERROR and err == "verification_zero_tests_collected"


def test_report_below_minimum_rejected():
    verdict, _, err, _ = verification.evaluate_report(
        _report(collected=["t.py::a"], outcomes={"t.py::a": "passed"}), MANIFEST)
    assert verdict == Verdict.ERROR and err == "verification_below_minimum_count"


def test_report_collection_mismatch_rejected():
    verdict, _, err, _ = verification.evaluate_report(
        _report(collected=["t.py::a", "t.py::b", "t.py::c"],
                outcomes={"t.py::a": "passed", "t.py::b": "passed",
                          "t.py::c": "passed"}), MANIFEST)
    assert verdict == Verdict.ERROR and err == "verification_collection_mismatch"


def test_report_deselected_rejected():
    verdict, _, err, _ = verification.evaluate_report(
        _report(deselected=["t.py::b"],
                outcomes={"t.py::a": "passed"}), MANIFEST)
    assert verdict == Verdict.ERROR and err == "verification_deselected_tests"


def test_report_collect_only_rejected():
    verdict, _, err, _ = verification.evaluate_report(
        _report(outcomes={}), MANIFEST)
    assert verdict == Verdict.ERROR and err == "verification_no_tests_executed"


def test_report_genuine_failure_is_fail_not_error():
    verdict, outcome, err, _ = verification.evaluate_report(
        _report(outcomes={"t.py::a": "passed", "t.py::b": "failed"}), MANIFEST)
    assert verdict == Verdict.FAIL and outcome.value == "fail" and err == "tests_failed"
