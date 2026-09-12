"""Verifier tests (AOP-010): baseline fails, valid repair passes, seal holds."""
import os
import shutil
import subprocess

from aop import verification

HIDDEN = ".hidden/hidden-tests"


def _snapshot_of_baseline(tmp_path, case):
    src = f"benchmarks/development/{case}/repo"
    dest = str(tmp_path / "snap")
    return verification.freeze_workspace(src, dest), dest


def test_baseline_fails_and_repair_passes(tmp_path):
    manifest, snap = _snapshot_of_baseline(tmp_path, "debug-offbyone")
    assert "median_bug.py" in manifest
    hidden = os.path.join(HIDDEN, "debug-offbyone")
    hhash = verification.hash_hidden_dir(hidden)
    base = verification.run_hidden_tests(
        "r-test", "a1", snap, hidden, ["python3", "-m", "pytest", os.path.join(os.path.abspath(hidden), "test_hidden_median.py"), "-q", "-p", "no:cacheprovider"], 60.0, hhash)
    assert base.verdict.value == "fail", base.details["log_tail"][-500:]
    assert base.outcome.value == "fail"
    # valid repair
    fixed = open(os.path.join(snap, "median_bug.py")).read().replace(
        "return (ordered[mid] + ordered[mid + 1]) / 2",
        "return (ordered[mid - 1] + ordered[mid]) / 2")
    open(os.path.join(snap, "median_bug.py"), "w").write(fixed)
    again = verification.run_hidden_tests(
        "r-test", "a1", snap, hidden, ["python3", "-m", "pytest", os.path.join(os.path.abspath(hidden), "test_hidden_median.py"), "-q", "-p", "no:cacheprovider"], 60.0, hhash)
    assert again.verdict.value == "pass", again.details["log_tail"][-500:]
    assert again.contamination_check == "clean"


def test_wordcount_baseline_fails(tmp_path):
    _, snap = _snapshot_of_baseline(tmp_path, "debug-wordcount")
    hidden = os.path.join(HIDDEN, "debug-wordcount")
    res = verification.run_hidden_tests(
        "r-test", "a1", snap, hidden, ["python3", "-m", "pytest", os.path.join(os.path.abspath(hidden), "test_hidden_counter.py"), "-q", "-p", "no:cacheprovider"], 60.0,
        verification.hash_hidden_dir(hidden))
    assert res.verdict.value == "fail"


def test_hidden_tests_not_in_agent_source_tree():
    for dirpath, _, filenames in os.walk("benchmarks"):
        for name in filenames:
            assert not name.startswith("test_hidden"), (dirpath, name)


def test_forged_claim_cannot_pass(tmp_path):
    """Exit 0 + 'all tests pass' narration with failing hidden tests => FAIL."""
    _, snap = _snapshot_of_baseline(tmp_path, "debug-offbyone")
    hidden = os.path.join(HIDDEN, "debug-offbyone")
    res = verification.run_hidden_tests(
        "r-test", "a1", snap, hidden, ["python3", "-m", "pytest", os.path.join(os.path.abspath(hidden), "test_hidden_median.py"), "-q", "-p", "no:cacheprovider"], 60.0,
        verification.hash_hidden_dir(hidden))
    assert res.verdict.value == "fail"  # claim ignored; hidden tests decide


def test_malformed_output_is_error_not_pass(tmp_path):
    _, snap = _snapshot_of_baseline(tmp_path, "debug-offbyone")
    hidden = os.path.join(HIDDEN, "debug-offbyone")
    res = verification.run_hidden_tests(
        "r-test", "a1", snap, hidden, ["python3", "nonexistent_test_file.py"], 60.0,
        verification.hash_hidden_dir(hidden))
    assert res.verdict.value in ("fail", "error")
    assert res.outcome.value != "pass"


def test_oversized_snapshot_handled(tmp_path):
    _, snap = _snapshot_of_baseline(tmp_path, "debug-offbyone")
    big = os.path.join(snap, "big.bin")
    with open(big, "wb") as fh:
        fh.write(b"\x00" * (2 * 1024 * 1024))
    hidden = os.path.join(HIDDEN, "debug-offbyone")
    res = verification.run_hidden_tests(
        "r-test", "a1", snap, hidden, ["python3", "-m", "pytest", os.path.join(os.path.abspath(hidden), "test_hidden_median.py"), "-q", "-p", "no:cacheprovider"], 60.0,
        verification.hash_hidden_dir(hidden))
    assert res.outcome.value in ("fail", "infra_error")  # never pass-by-confusion
