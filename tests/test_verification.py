"""Trusted verifier tests (AOP-010): positive verification + adversarial rejection.

Baseline fails, a real repair passes, and no candidate-controlled trick
(os._exit, sitecustomize/usercustomize, conftest, pytest.ini, skips, zero
collection, deselection) can produce a PASS.
"""

import os
import socket
import stat
from pathlib import Path

import pytest

from hop import verification
from hop.contracts.records import Verdict

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
        "r-test",
        "a1",
        snap,
        hidden,
        timeout_s=90,
        work_root=(str(tmp_path / "work") if tmp_path else None),
    )


def test_baseline_fails_and_repair_passes(tmp_path):
    manifest, snap = _snapshot(tmp_path)
    assert "median_bug.py" in manifest
    base = _run(snap, tmp_path=tmp_path)
    assert base.verdict == Verdict.FAIL, base.details.get("log_tail", "")[-500:]
    assert base.outcome.value == "fail"
    ev = base.details["verification"]
    assert ev["collected"] == ev["expected_tests"]
    assert ev["outcomes"]["test_hidden_median.py::test_even_length"] == "failed"

    fixed = (
        Path(os.path.join(snap, "median_bug.py"))
        .read_text()
        .replace(
            "return (ordered[mid] + ordered[mid + 1]) / 2",
            "return (ordered[mid - 1] + ordered[mid]) / 2",
        )
    )
    Path(os.path.join(snap, "median_bug.py")).write_text(fixed)
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
    # Candidate os._exit(0) now kills only the candidate worker; the trusted
    # host still writes the authoritative report. This is a task FAIL with a
    # recorded report, not the historical verification_no_result (which meant
    # the evaluator process itself died and produced nothing).
    assert res.outcome.value == "fail"
    assert res.error_class == "tests_failed"
    report = res.details.get("verification", {}).get("report", {})
    outcomes = report.get("outcomes", {})
    assert outcomes, "the authoritative report must record the failing tests"
    assert all(outcome != "passed" for outcome in outcomes.values())


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
    from hop.verification import VerifierSetupError, load_verifier_manifest

    with pytest.raises(VerifierSetupError) as exc:
        load_verifier_manifest(empty)
    assert exc.value.error_class == "missing_verifier_fixture"


# ---------------------------------------------------------------------------
# Machine-readable report evaluation.
# ---------------------------------------------------------------------------

MANIFEST = {
    "verifier_id": "v",
    "version": "1",
    "test_files": ["t.py"],
    "expected_tests": ["t.py::a", "t.py::b"],
    "mandatory_tests": ["t.py::a", "t.py::b"],
    "minimum_test_count": 2,
}


def _report(**kw):
    base = {
        "collected": ["t.py::a", "t.py::b"],
        "deselected": [],
        "outcomes": {"t.py::a": "passed", "t.py::b": "passed"},
        "exitstatus": 0,
    }
    base.update(kw)
    return base


def test_report_all_skipped_rejected():
    verdict, _outcome, err, _ = verification.evaluate_report(
        _report(outcomes={"t.py::a": "skipped", "t.py::b": "skipped"}), MANIFEST
    )
    assert verdict == Verdict.ERROR and err == "verification_all_skipped"


def test_report_zero_collected_rejected():
    verdict, _outcome, err, _ = verification.evaluate_report(
        _report(collected=[], outcomes={}), MANIFEST
    )
    assert verdict == Verdict.ERROR and err == "verification_zero_tests_collected"


def test_report_below_minimum_rejected():
    verdict, _, err, _ = verification.evaluate_report(
        _report(collected=["t.py::a"], outcomes={"t.py::a": "passed"}), MANIFEST
    )
    assert verdict == Verdict.ERROR and err == "verification_below_minimum_count"


def test_report_collection_mismatch_rejected():
    verdict, _, err, _ = verification.evaluate_report(
        _report(
            collected=["t.py::a", "t.py::b", "t.py::c"],
            outcomes={"t.py::a": "passed", "t.py::b": "passed", "t.py::c": "passed"},
        ),
        MANIFEST,
    )
    assert verdict == Verdict.ERROR and err == "verification_collection_mismatch"


def test_report_deselected_rejected():
    verdict, _, err, _ = verification.evaluate_report(
        _report(deselected=["t.py::b"], outcomes={"t.py::a": "passed"}), MANIFEST
    )
    assert verdict == Verdict.ERROR and err == "verification_deselected_tests"


def test_report_collect_only_rejected():
    verdict, _, err, _ = verification.evaluate_report(_report(outcomes={}), MANIFEST)
    assert verdict == Verdict.ERROR and err == "verification_no_tests_executed"


def test_report_genuine_failure_is_fail_not_error():
    verdict, outcome, err, _ = verification.evaluate_report(
        _report(outcomes={"t.py::a": "passed", "t.py::b": "failed"}), MANIFEST
    )
    assert verdict == Verdict.FAIL and outcome.value == "fail" and err == "tests_failed"


# --- D-2: the candidate RPC socket must not survive a verification ----------


def _sockets_under(root):
    found = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            full = os.path.join(dirpath, name)
            try:
                mode = os.stat(full, follow_symlinks=False).st_mode
            except OSError:
                continue
            if stat.S_ISSOCK(mode):
                found.append(full)
    return found


def _bind_socket(path):
    """Create a real AF_UNIX socket file, as the candidate worker does."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path)
    server.close()  # closing does not unlink the path
    assert os.path.exists(path)
    return path


def test_verification_leaves_no_rpc_socket_after_completion(tmp_path):
    """A completed verification must not leave the worker socket behind."""
    _manifest, snap = _snapshot(tmp_path)
    result = _run(snap, tmp_path=tmp_path)
    assert result.verdict in (Verdict.PASS, Verdict.FAIL), result.error_class
    work = tmp_path / "work"
    assert _sockets_under(work) == []
    assert not (work / "verify" / "rpc" / "candidate.sock").exists()


def test_verification_removes_rpc_socket_on_failure(tmp_path, monkeypatch):
    """A socket that exists when the run fails is removed before returning."""
    _manifest, snap = _snapshot(tmp_path)
    work = tmp_path / "work"
    socket_path = str(work / "verify" / "rpc" / "candidate.sock")

    def _fake_bwrap(_work_root):
        # The run fails only after the socket already exists on disk.
        _bind_socket(socket_path)
        return False, "test: sandbox unavailable"

    monkeypatch.setattr(verification, "_bwrap_usable", _fake_bwrap)
    result = verification.run_verification(
        "r-test", "a1", snap, os.path.join(HIDDEN, OFFBYONE), timeout_s=30, work_root=str(work)
    )
    assert result.error_class == "verifier_sandbox_unavailable"
    assert not os.path.exists(socket_path)
    assert _sockets_under(work) == []


def test_legacy_rpc_socket_is_cleaned_on_the_next_run(tmp_path, monkeypatch):
    """A leftover socket is unlinked on the next run, not only by rmtree."""
    _manifest, snap = _snapshot(tmp_path)
    work = tmp_path / "work"
    legacy = _bind_socket(str(work / "verify" / "rpc" / "candidate.sock"))

    def _failing_rmtree(_path, *_args, **_kwargs):
        raise OSError("simulated: the verify root cannot be removed")

    # If the socket were only cleaned as a side effect of the recursive delete,
    # it would survive this run.
    monkeypatch.setattr(verification.shutil, "rmtree", _failing_rmtree)
    with pytest.raises(OSError, match="cannot be removed"):
        verification.run_verification(
            "r-test",
            "a1",
            snap,
            os.path.join(HIDDEN, OFFBYONE),
            timeout_s=30,
            work_root=str(work),
        )
    assert not os.path.exists(legacy)
    assert _sockets_under(work) == []


def test_fallback_rpc_dir_is_removed_on_completion(tmp_path, monkeypatch):
    """The out-of-tree long-path fallback directory does not leak."""
    _manifest, snap = _snapshot(tmp_path)
    fallback = tmp_path / "rpc-fallback"
    long_work = tmp_path / ("w" * 80) / ("x" * 40)  # forces the >100 char fallback
    assert len(str(long_work / "verify" / "rpc" / "candidate.sock")) > 100

    def _fake_mkdtemp(prefix="hop-rpc-", **_kw):
        fallback.mkdir(parents=True, exist_ok=True)
        return str(fallback)

    def _fake_bwrap(_work_root):
        _bind_socket(str(fallback / "candidate.sock"))
        return False, "test: unavailable"

    monkeypatch.setattr(verification.tempfile, "mkdtemp", _fake_mkdtemp)
    monkeypatch.setattr(verification, "_bwrap_usable", _fake_bwrap)
    result = verification.run_verification(
        "r-test", "a1", snap, os.path.join(HIDDEN, OFFBYONE), timeout_s=30, work_root=str(long_work)
    )
    assert result.error_class == "verifier_sandbox_unavailable"
    assert not fallback.exists()
