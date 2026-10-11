"""Real-namespace boundary capability tests (WP-1.4c).

These run in the ESF Cube sandbox (sandbox marker). They are deliberately not
skipped when a capability is missing: a host without bwrap or delegated cgroups
must produce explicit blocked evidence instead of a green result.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from hop import verification
from hop.contracts.records import RunOutcome, Verdict
from hop.limit_exec import probe_cgroup_v2
from hop.sandbox import bwrap_available

pytestmark = pytest.mark.sandbox


def _repo_root() -> Path:
    """Resolve the delivery checkout robustly.

    The Cube gate runs pytest from the checkout root with an editable install;
    a path derived only from ``__file__`` is not trusted here, so prefer cwd
    when it contains the tracked sentinels and fail loudly otherwise.
    """
    sentinels = (
        "benchmarks/development/debug-offbyone/repo/median_bug.py",
        "src/hop/verification.py",
    )
    candidates = [Path.cwd(), Path(__file__).resolve().parents[2]]
    for candidate in candidates:
        if all((candidate / sentinel).is_file() for sentinel in sentinels):
            return candidate
    raise AssertionError(
        "cannot locate the delivery checkout; tried "
        + ", ".join(str(candidate) for candidate in candidates)
    )


REPO = _repo_root()


def _write_case(root: Path, candidate: str, test_source: str, test_file: str) -> tuple[str, str]:
    snapshot = root / "snap"
    snapshot.mkdir(parents=True)
    (snapshot / "probe.py").write_text(candidate)
    hidden = root / "hidden"
    hidden.mkdir()
    (hidden / test_file).write_text(test_source)
    (hidden / "verifier.json").write_text(
        json.dumps(
            {
                "verifier_id": "verifier:boundary-probe",
                "version": "1",
                "test_files": [test_file],
                "expected_tests": [f"{test_file}::test_boundary"],
                "mandatory_tests": [f"{test_file}::test_boundary"],
                "minimum_test_count": 1,
            }
        )
    )
    return str(snapshot), str(hidden)


def _run(snapshot: str, hidden: str, work: Path, timeout_s: float = 90.0):
    return verification.run_verification(
        "r-boundary",
        "a1",
        snapshot,
        hidden,
        timeout_s=timeout_s,
        work_root=str(work),
    )


def test_candidate_cannot_read_current_case_hidden_tests(tmp_path):
    candidate = (
        "import os\n"
        "import sys\n"
        "NEEDLES = {'test_' + 'probe.py', 'verifier' + '.json', 'report' + '.json',\n"
        "           'aop_verifier' + '_plugin.py', '_aop_' + 'bootstrap.py'}\n"
        "SKIP = {'/usr', '/lib', '/lib64', '/bin', '/sbin', '/proc', '/sys', '/dev', '/etc'}\n"
        "def hidden_hits():\n"
        "    roots = [os.getcwd(), '/tmp']\n"
        "    roots += [p for p in sys.argv if os.path.isdir(p)]\n"
        "    roots += [p for p in os.environ.values() if os.path.isdir(p)]\n"
        "    hits, seen = [], set()\n"
        "    for root in roots:\n"
        "        root = os.path.realpath(root)\n"
        "        if root in seen or root in SKIP or not os.path.isdir(root):\n"
        "            continue\n"
        "        seen.add(root)\n"
        "        for dirpath, dirnames, filenames in os.walk(root):\n"
        "            dirnames[:] = [d for d in dirnames if os.path.join(dirpath, d) not in SKIP]\n"
        "            for name in filenames:\n"
        "                if name in NEEDLES:\n"
        "                    hits.append(os.path.join(dirpath, name))\n"
        "    return sorted(hits)\n"
    )
    test_source = (
        "from probe import hidden_hits\n\n\ndef test_boundary():\n    assert hidden_hits() == []\n"
    )
    snapshot, hidden = _write_case(tmp_path, candidate, test_source, "test_probe.py")
    res = _run(snapshot, hidden, tmp_path / "work")
    if res.error_class == "verifier_sandbox_unavailable":
        # Explicit blocked evidence, never a pass and never a silent skip.
        assert res.outcome == RunOutcome.INFRA_ERROR
        return
    assert res.verdict == Verdict.PASS, res.details


def test_candidate_report_forgery_cannot_change_verdict(tmp_path):
    candidate = (
        "import json\n"
        "import os\n"
        "import sys\n"
        "TEST_ID = 'test_' + 'probe.py::test_boundary'\n"
        "PASS_REPORT = {'schema_version': 1,\n"
        "               'collected': [TEST_ID],\n"
        "               'deselected': [],\n"
        "               'outcomes': {TEST_ID: 'passed'},\n"
        "               'exitstatus': 0}\n"
        "def forge_and_exit():\n"
        "    targets = [p for p in sys.argv if p.endswith('report.json')]\n"
        "    targets += [os.path.join(os.getcwd(), 'report.json'), '/tmp/report.json']\n"
        "    for target in targets:\n"
        "        try:\n"
        "            with open(target, 'w') as fh:\n"
        "                json.dump(PASS_REPORT, fh)\n"
        "        except OSError:\n"
        "            pass\n"
        "    os._exit(0)\n"
    )
    test_source = (
        "from probe import forge_and_exit\n\n\ndef test_boundary():\n    forge_and_exit()\n"
    )
    snapshot, hidden = _write_case(tmp_path, candidate, test_source, "test_probe.py")
    res = _run(snapshot, hidden, tmp_path / "work")
    if res.error_class == "verifier_sandbox_unavailable":
        assert res.outcome == RunOutcome.INFRA_ERROR
        return
    assert res.verdict == Verdict.FAIL, res.details
    assert res.error_class == "tests_failed"
    report = json.loads((tmp_path / "work" / "verify" / "out" / "report.json").read_text())
    assert report["outcomes"]["test_probe.py::test_boundary"] != "passed"


def test_cgroup_enforced_or_explicitly_blocked(tmp_path, monkeypatch):
    probe = probe_cgroup_v2()
    monkeypatch.setenv("HOP_REQUIRE_QUOTAS", "1")
    snapshot = REPO / "benchmarks" / "development" / "debug-offbyone" / "repo"
    hidden = REPO / ".hidden" / "hidden-tests" / "debug-offbyone"
    res = verification.run_verification(
        "r-quota",
        "a1",
        str(snapshot),
        str(hidden),
        timeout_s=90,
        work_root=str(tmp_path / "work"),
    )
    if not probe["available"]:
        assert res.error_class == "aggregate_quota_unavailable"
        assert res.outcome == RunOutcome.INFRA_ERROR
        return
    assert res.error_class != "aggregate_quota_unavailable", res.details
    assert res.details["resources"]["mode"] == "enforced", res.details["resources"]


def test_descendant_is_placed_in_the_run_cgroup(tmp_path):
    probe = probe_cgroup_v2()
    if not probe["available"]:
        assert probe["mode"] in ("unavailable", "partial")
        assert probe["reasons"]
        return
    status = tmp_path / "status.json"
    env = {
        **os.environ,
        "HOP_CGROUP_PARENT": probe["parent"],
        "HOP_CGROUP_NAME": "sandbox-check",
        "HOP_CGROUP_STATUS": str(status),
        "HOP_QUOTA_DIMENSIONS": "memory,pids",
    }
    proc = subprocess.run(
        [
            sys.executable,
            "-I",
            str(REPO / "src" / "hop" / "limit_exec.py"),
            "60",
            "64",
            str(1 << 30),
            "/bin/sh",
            "-c",
            "cat /proc/self/cgroup",
        ],
        env=env,
        capture_output=True,
        check=False,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    report = json.loads(status.read_text())
    assert report["mode"] == "enforced", report
    assert "hopsandbox-check" in report["cgroup"] or "hop-" in report["cgroup"]


def test_missing_bwrap_is_reported_blocked_not_skipped(tmp_path):
    assert isinstance(bwrap_available(), bool)
    if bwrap_available():
        return
    snapshot = REPO / "benchmarks" / "development" / "debug-offbyone" / "repo"
    hidden = REPO / ".hidden" / "hidden-tests" / "debug-offbyone"
    res = verification.run_verification(
        "r-nobwrap", "a1", str(snapshot), str(hidden), timeout_s=30, work_root=str(tmp_path / "w")
    )
    assert res.error_class == "verifier_sandbox_unavailable"
    assert res.outcome == RunOutcome.INFRA_ERROR


def _limit_exec_env(probe: dict, tmp_path: Path, name: str, limits: dict) -> tuple[dict, Path]:
    status = tmp_path / f"{name}-status.json"
    env = {
        **os.environ,
        "HOP_CGROUP_PARENT": probe["parent"],
        "HOP_CGROUP_NAME": name,
        "HOP_CGROUP_STATUS": str(status),
        "HOP_CGROUP_LIMITS": json.dumps(limits),
        "HOP_QUOTA_DIMENSIONS": ",".join(limits.get("dimensions", ["memory", "pids"])),
    }
    return env, status


def _limit_exec_cmd(workload: Path, memory_arg: int = 1 << 30) -> list[str]:
    return [
        sys.executable,
        "-I",
        str(REPO / "src" / "hop" / "limit_exec.py"),
        "300",
        "64",
        str(memory_arg),
        sys.executable,
        "-I",
        str(workload),
    ]


def test_descendant_memory_is_capped_and_sibling_unaffected(tmp_path):
    """Grandchild allocation over memory.max is OOM-killed; a sibling run survives."""
    probe = probe_cgroup_v2()
    if not probe["available"]:
        assert probe["mode"] in ("unavailable", "partial")
        assert probe["reasons"]
        return
    allocate = tmp_path / "allocate.py"
    allocate.write_text(
        "import sys\n"
        "blocks = [bytearray(40 * 1024 * 1024) for _ in range(int(sys.argv[1]))]\n"
        "print('ALLOC_OK', len(blocks))\n"
    )
    capped_env, capped_status = _limit_exec_env(
        probe, tmp_path, "mem-capped", {"memory_bytes": 64 * 1024 * 1024, "dimensions": ["memory"]}
    )
    sibling_env, sibling_status = _limit_exec_env(
        probe,
        tmp_path,
        "mem-sibling",
        {"memory_bytes": 256 * 1024 * 1024, "dimensions": ["memory"]},
    )
    capped = subprocess.Popen(
        _limit_exec_cmd(allocate) + ["6"],
        env=capped_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    sibling = subprocess.run(
        _limit_exec_cmd(allocate) + ["1"],
        env=sibling_env,
        capture_output=True,
        check=False,
        text=True,
        timeout=120,
    )
    out, err = capped.communicate(timeout=120)
    sibling_report = json.loads(sibling_status.read_text())
    assert sibling_report["mode"] == "enforced", sibling_report
    assert sibling.returncode == 0, sibling.stderr
    assert "ALLOC_OK" in sibling.stdout
    capped_report = json.loads(capped_status.read_text())
    assert capped_report["mode"] == "enforced", capped_report
    assert capped.returncode != 0, (out, err)
    events = Path(capped_report["cgroup"], "memory.events")
    if events.exists():
        assert "oom_kill 0" not in events.read_text()
    assert Path(capped_report["cgroup"]).exists()
    for report in (capped_report, sibling_report):
        try:
            os.rmdir(report["cgroup"])
        except OSError:
            pass


def test_descendant_fork_past_pids_max_is_refused(tmp_path):
    """A descendant cannot exceed pids.max; the sibling run is unaffected."""
    probe = probe_cgroup_v2()
    if not probe["available"]:
        assert probe["mode"] in ("unavailable", "partial")
        assert probe["reasons"]
        return
    fork_bomb = tmp_path / "forks.py"
    fork_bomb.write_text(
        "import os\n"
        "count = 0\n"
        "for _ in range(20):\n"
        "    try:\n"
        "        pid = os.fork()\n"
        "    except OSError as exc:\n"
        "        print('REFUSED', type(exc).__name__, count)\n"
        "        break\n"
        "    if pid == 0:\n"
        "        os._exit(0)\n"
        "    count += 1\n"
        "else:\n"
        "    print('FORKS', count)\n"
    )
    capped_env, capped_status = _limit_exec_env(
        probe, tmp_path, "pids-capped", {"max_processes": 8, "dimensions": ["pids"]}
    )
    proc = subprocess.run(
        _limit_exec_cmd(fork_bomb),
        env=capped_env,
        capture_output=True,
        check=False,
        text=True,
        timeout=120,
    )
    report = json.loads(capped_status.read_text())
    assert report["mode"] == "enforced", report
    assert proc.returncode == 0, proc.stderr
    if "FORKS" in proc.stdout:
        assert int(proc.stdout.split("FORKS", 1)[1].strip()) < 20
    else:
        assert "REFUSED" in proc.stdout
    events = Path(report["cgroup"], "pids.events")
    if events.exists():
        assert "max 0" not in events.read_text()
    assert Path(report["cgroup"]).exists()
    try:
        os.rmdir(report["cgroup"])
    except OSError:
        pass
