"""Negative/adversarial tests for verifier candidate isolation (ADR-005 rev.).

Two properties are asserted here without needing namespaces:

* the trusted host **never imports candidate code** — candidate execution moves
  through the RPC boundary into a separate process (proved with process ids);
* the candidate worker's mounts/env never contain the hidden tests, the verifier
  scaffold, or the report path (proved structurally on the spawn arguments),
  which is what closes the current-case read and the report-forgery vector.

Wrong-result classes stay distinct: an unusable sandbox or an unreachable
candidate worker is ``infra_error``; candidate misbehaviour is a test failure.
Real-namespace enforcement is exercised by ``test_boundary_capabilities.py``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from hop import verification
from hop.contracts.records import RunOutcome, Verdict
from hop.sandbox import RunResult

REPO = Path(__file__).resolve().parents[2]
SNAPSHOT_SRC = REPO / "benchmarks" / "development" / "debug-offbyone" / "repo"
HIDDEN_OFFBYONE = REPO / ".hidden" / "hidden-tests" / "debug-offbyone"


def _freeze(tmp_path: Path, name: str = "snap") -> tuple[dict, str]:
    return verification.freeze_workspace(str(SNAPSHOT_SRC), str(tmp_path / name))


def _rpc_harness(
    tmp_path: Path, candidate: str, test_source: str, test_name: str = "test_sample.py"
) -> dict:
    """Run the real bootstrap + real candidate worker without a namespace.

    The namespace is what makes the isolation *enforceable*; the RPC process
    split and the trusted-host-no-import property are already testable here.
    """
    snapshot = tmp_path / "snap"
    snapshot.mkdir()
    (snapshot / "sample.py").write_text(candidate)
    tests = tmp_path / "hidden"
    tests.mkdir()
    (tests / test_name).write_text(test_source)
    vbin = tmp_path / "vbin"
    wbin = tmp_path / "wbin"
    verification._write_verifier_scaffold(str(vbin), str(wbin))
    sock = tmp_path / "candidate.sock"
    marker = tmp_path / "imports.txt"
    env = {**os.environ, "HOP_CANDIDATE_MARKER": str(marker)}
    worker = subprocess.Popen(
        [sys.executable, "-I", str(wbin / "_aop_candidate_worker.py"), str(snapshot), str(sock)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.time() + 20
    while not sock.exists():
        if worker.poll() is not None:
            _out, err = worker.communicate()
            raise AssertionError(f"candidate worker died before binding: {err}")
        if time.time() > deadline:
            raise AssertionError("candidate worker socket did not appear")
        time.sleep(0.02)
    report = tmp_path / "report.json"
    host = subprocess.run(
        [
            sys.executable,
            "-I",
            str(vbin / "_aop_bootstrap.py"),
            str(snapshot),
            str(tests),
            str(report),
            str(vbin),
            str(sock),
            test_name,
        ],
        env=env,
        capture_output=True,
        check=False,
        text=True,
        timeout=120,
    )
    try:
        worker.wait(timeout=10)
    except subprocess.TimeoutExpired:
        worker.kill()
        worker.wait()
    return {
        "host": host,
        "report": json.loads(report.read_text()) if report.exists() else None,
        "marker": marker.read_text() if marker.exists() else "",
        "worker_pid": worker.pid,
    }


def _error_result_for(report: dict, manifest: dict):
    verdict, outcome, error_class, _ = verification.evaluate_report(report, manifest)
    return verdict, outcome, error_class


# ---------------------------------------------------------------------------
# Trusted host never imports candidate code
# ---------------------------------------------------------------------------


def test_candidate_code_runs_only_in_the_worker_process(tmp_path):
    candidate = (
        "import os\n"
        "_MARK = os.environ.get('HOP_CANDIDATE_MARKER')\n"
        "if _MARK:\n"
        "    open(_MARK, 'a').write('import:' + str(os.getpid()) + '\\n')\n"
        "def add(a, b):\n"
        "    if _MARK:\n"
        "        open(_MARK, 'a').write('call:' + str(os.getpid()) + '\\n')\n"
        "    return a + b\n"
    )
    test_source = "from sample import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"
    result = _rpc_harness(tmp_path, candidate, test_source)
    assert result["host"].returncode == 0, result["host"].stderr[-2000:]
    assert result["report"]["outcomes"] == {"test_sample.py::test_add": "passed"}
    recorded = {line.split(":", 1)[1] for line in result["marker"].splitlines() if ":" in line}
    assert recorded == {str(result["worker_pid"])}, (
        "candidate import/call must happen only in the worker process; "
        f"recorded={sorted(recorded)} worker={result['worker_pid']}"
    )


def test_candidate_exception_is_raised_in_the_trusted_host(tmp_path):
    candidate = "def boom():\n    raise ValueError('nope')\n"
    test_source = (
        "import pytest\n"
        "from sample import boom\n\n\n"
        "def test_boom():\n"
        "    with pytest.raises(ValueError):\n"
        "        boom()\n"
    )
    result = _rpc_harness(tmp_path, candidate, test_source)
    assert result["report"]["outcomes"] == {"test_sample.py::test_boom": "passed"}


def test_candidate_os_exit_cannot_suppress_the_report(tmp_path):
    """The candidate kills only its own worker; the trusted host still reports."""
    candidate = "import os\nos._exit(0)\n"
    test_source = "from sample import add\n\n\ndef test_add():\n    assert add(1, 1) == 2\n"
    result = _rpc_harness(tmp_path, candidate, test_source)
    assert result["report"] is not None, "host must still write the authoritative report"
    manifest = {
        "verifier_id": "v",
        "version": "1",
        "test_files": ["test_sample.py"],
        "expected_tests": ["test_sample.py::test_add"],
        "mandatory_tests": ["test_sample.py::test_add"],
        "minimum_test_count": 1,
    }
    verdict, outcome, error_class = _error_result_for(result["report"], manifest)
    assert verdict == Verdict.FAIL
    assert outcome == RunOutcome.FAIL
    assert error_class == "tests_failed"
    assert result["report"]["outcomes"]["test_sample.py::test_add"] != "passed"


# ---------------------------------------------------------------------------
# Candidate worker cannot see hidden tests / scaffold / report path
# ---------------------------------------------------------------------------


def _fake_spawn_factory(calls: list):
    def fake_spawn(
        cmd,
        *,
        cwd,
        env,
        ro_binds,
        rw_binds,
        spec,
        timeout_s=None,
        cancel=None,
        stdout_path="",
        stderr_path="",
    ):
        calls.append(
            {
                "cmd": list(cmd),
                "env": dict(env),
                "ro": list(ro_binds),
                "rw": list(rw_binds),
                "cwd": cwd,
            }
        )
        if len(calls) == 1:
            # candidate worker: [python, -I, worker.py, snapshot, socket]
            Path(cmd[4]).parent.mkdir(parents=True, exist_ok=True)
            Path(cmd[4]).touch()
        else:
            # verifier host: [python, -I, bootstrap, snapshot, tests, report, vbin, socket, ...]
            Path(cmd[5]).write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "collected": ["test_hidden_median.py::test_even_length"],
                        "deselected": [],
                        "outcomes": {"test_hidden_median.py::test_even_length": "passed"},
                        "exitstatus": 0,
                    }
                )
            )
        return RunResult(
            exit_code=0,
            timed_out=False,
            cancelled=False,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
        )

    return fake_spawn


def test_candidate_worker_mounts_exclude_hidden_tests_and_report(tmp_path, monkeypatch):
    _, snap = _freeze(tmp_path)
    work = tmp_path / "work"
    hidden_dir = str(work / "verify" / "hidden")
    out_dir = str(work / "verify" / "out")
    calls: list = []
    monkeypatch.setattr(verification, "_bwrap_usable", lambda root: (True, ""))
    monkeypatch.setattr(verification, "spawn_confined", _fake_spawn_factory(calls))
    manifest = {
        "verifier_id": "v",
        "version": "1",
        "test_files": ["test_hidden_median.py"],
        "expected_tests": ["test_hidden_median.py::test_even_length"],
        "mandatory_tests": ["test_hidden_median.py::test_even_length"],
        "minimum_test_count": 1,
    }
    res = verification.run_verification(
        "r", "a1", snap, str(HIDDEN_OFFBYONE), timeout_s=30, work_root=str(work), manifest=manifest
    )
    assert res.verdict == Verdict.PASS, res.details
    assert len(calls) == 2, "expected a candidate worker spawn and a verifier host spawn"
    worker = calls[0]
    binds = [dst for _src, dst in worker["ro"] + worker["rw"]]
    assert all(hidden_dir not in bind for bind in binds), binds
    assert all(out_dir not in bind for bind in binds), binds
    joined = " ".join(worker["cmd"]) + " " + json.dumps(worker["env"], sort_keys=True)
    assert hidden_dir not in joined, joined
    assert "report.json" not in joined, joined
    assert all("test_hidden" not in bind for bind in binds), binds


def test_unusable_bwrap_is_infra_error_not_a_verification_failure(tmp_path, monkeypatch):
    _, snap = _freeze(tmp_path)

    def refuse(*args, **kwargs):
        raise verification.SandboxUnavailable("bwrap refused to create a namespace")

    monkeypatch.setattr(verification, "spawn_confined", refuse)
    res = verification.run_verification(
        "r", "a1", snap, str(HIDDEN_OFFBYONE), timeout_s=30, work_root=str(tmp_path / "work")
    )
    assert res.error_class == "verifier_sandbox_unavailable"
    assert res.outcome == RunOutcome.INFRA_ERROR
    assert res.verdict == Verdict.ERROR


def test_real_environment_is_evidenced_or_explicitly_blocked(tmp_path):
    """No silent skip: a green result requires real evidence, else blocked."""
    _, snap = _freeze(tmp_path)
    res = verification.run_verification(
        "r", "a1", snap, str(HIDDEN_OFFBYONE), timeout_s=60, work_root=str(tmp_path / "work")
    )
    if res.error_class == "verifier_sandbox_unavailable":
        assert res.outcome == RunOutcome.INFRA_ERROR
        return
    assert res.verdict in (Verdict.PASS, Verdict.FAIL), {
        "error_class": res.error_class,
        "verdict": res.verdict.value,
        "log_tail": res.details.get("log_tail", "")[-2000:],
        "stderr_tail": res.details.get("stderr_tail", "")[-2000:],
        "candidate_worker": res.details.get("candidate_worker", {}),
        "collection_errors": res.details.get("verification", {})
        .get("report", {})
        .get("collection_errors", []),
        "report_collected": res.details.get("verification", {}).get("report", {}).get("collected"),
    }
    assert res.details["isolation"]["candidate_mounts_hidden_tests"] is False
    assert res.details["isolation"]["trusted_host_imports_candidate"] is False
    assert res.details["isolation"]["mode"] == "separate-candidate-namespace"
