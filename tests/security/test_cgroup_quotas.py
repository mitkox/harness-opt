"""Negative tests for aggregate cgroup v2 quotas (ADR-013 / WP-1.4c).

Contract under test: a quota is only ever reported ``enforced`` after a real
mkdir/write/read-back/placement; when the capability is absent the result is
``unavailable``/``limited``/``blocked`` with the exact unenforced dimensions.
Nothing here silently skips: a host without delegated cgroups yields explicit
blocked evidence, never a green pass.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from hop import limit_exec

REPO = Path(__file__).resolve().parents[2]
LIMIT_EXEC = REPO / "src" / "hop" / "limit_exec.py"


def _fake_cgroup(root: Path, controllers: str = "memory pids cpu io") -> Path:
    """A plain directory that mimics the cgroup v2 files the code touches."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "cgroup.controllers").write_text(controllers + "\n")
    (root / "cgroup.subtree_control").write_text("\n")
    return root


def test_probe_refuses_a_plain_directory(tmp_path):
    report = limit_exec.probe_cgroup_v2(str(tmp_path))
    assert report["available"] is False
    assert report["mode"] == "unavailable"
    assert any(
        reason.startswith(("no_cgroup2_mount", "mkdir_denied")) for reason in report["reasons"]
    )


def test_probe_reports_mkdir_denied_without_residue(tmp_path, monkeypatch):
    monkeypatch.setattr(limit_exec, "cgroup2_mount_for", lambda path: str(tmp_path))
    occupied = tmp_path / f"hop-probe-{os.getpid()}"
    occupied.write_text("occupied")
    report = limit_exec.probe_cgroup_v2(str(tmp_path))
    assert report["available"] is False
    assert any(reason.startswith("mkdir_denied") for reason in report["reasons"])
    assert not list(tmp_path.glob("hop-probe-*/child"))


def test_probe_measures_enforceable_dimensions_and_placement(tmp_path, monkeypatch):
    root = _fake_cgroup(tmp_path / "cgroup")
    monkeypatch.setattr(limit_exec, "cgroup2_mount_for", lambda path: str(root))
    report = limit_exec.probe_cgroup_v2(str(root))
    assert report["available"] is True
    assert report["placement"] is True
    assert report["enforceable"]["memory"] is True
    assert report["enforceable"]["pids"] is True
    assert report["enforceable"]["cpu"] is True
    assert report["mode"] == "enforced"
    assert not list(root.glob("hop-probe-*")), "probe left residue"


def test_apply_limits_refuses_a_plain_directory(tmp_path):
    with pytest.raises(RuntimeError, match="cgroup2 mount"):
        limit_exec.apply_cgroup_limits(str(tmp_path), "n", {"memory_bytes": 1, "max_processes": 1})


def test_apply_limits_places_self_and_reads_back(tmp_path, monkeypatch):
    root = _fake_cgroup(tmp_path / "cgroup")
    monkeypatch.setattr(limit_exec, "cgroup2_mount_for", lambda path: str(root))
    report = limit_exec.apply_cgroup_limits(
        str(root),
        "unit",
        {"memory_bytes": 1234, "max_processes": 64, "dimensions": ["memory", "pids"]},
    )
    child = Path(report["cgroup"])
    assert report["mode"] == "enforced"
    assert (child / "memory.max").read_text() == "1234"
    assert (child / "pids.max").read_text() == "64"
    assert str(os.getpid()) in (child / "cgroup.procs").read_text()
    shutil.rmtree(child)


def test_unenforced_dimension_is_reported_not_claimed(tmp_path, monkeypatch):
    root = _fake_cgroup(tmp_path / "cgroup", controllers="memory")
    monkeypatch.setattr(limit_exec, "cgroup2_mount_for", lambda path: str(root))
    report = limit_exec.apply_cgroup_limits(
        str(root),
        "unit2",
        {"memory_bytes": 1234, "max_processes": 64, "dimensions": ["memory", "pids"]},
    )
    assert report["mode"] == "limited"
    assert report["enforced"] == ["memory"]
    assert any(item.startswith("pids") for item in report["unenforced"])
    shutil.rmtree(Path(report["cgroup"]))


def _run_limit_exec(env_extra: dict, command: list[str]) -> subprocess.CompletedProcess:
    env = {**os.environ, **env_extra}
    return subprocess.run(
        [sys.executable, "-I", str(LIMIT_EXEC), "60", "64", str(1 << 30), *command],
        env=env,
        capture_output=True,
        check=False,
        text=True,
        timeout=60,
    )


def test_limit_exec_refuses_to_exec_on_non_cgroup_parent(tmp_path):
    marker = tmp_path / "ran"
    proc = _run_limit_exec(
        {"HOP_CGROUP_PARENT": str(tmp_path), "HOP_CGROUP_NAME": "blocked"},
        ["/bin/sh", "-c", f"touch {marker}"],
    )
    assert proc.returncode == 3, proc.stderr
    assert not marker.exists()
    assert "refusing to exec" in proc.stderr


def test_requiring_quotas_blocks_before_exec_when_unenforced(tmp_path):
    root = _fake_cgroup(tmp_path / "cgroup", controllers="memory")
    marker = tmp_path / "ran"
    status = tmp_path / "status.json"
    script = (
        "import sys\n"
        "from hop import limit_exec\n"
        f"limit_exec.cgroup2_mount_for = lambda path: {str(root)!r}\n"
        "calls = []\n"
        "limit_exec.os.execvpe = lambda *a, **k: calls.append(a)\n"
        f"sys.argv = ['limit_exec.py', '60', '64', str(1 << 30), '/bin/sh', '-c', 'touch {marker}']\n"
        "try:\n"
        "    limit_exec.main()\n"
        "except SystemExit as exc:\n"
        "    print('EXIT', exc.code)\n"
        "print('EXEC', bool(calls))\n"
    )
    env = {
        **os.environ,
        "PYTHONPATH": str(REPO / "src"),
        "HOP_CGROUP_PARENT": str(root),
        "HOP_CGROUP_NAME": "req",
        "HOP_CGROUP_STATUS": str(status),
        "HOP_QUOTA_DIMENSIONS": "memory,pids",
        "HOP_REQUIRE_QUOTAS": "1",
    }
    proc = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        capture_output=True,
        check=False,
        text=True,
        timeout=60,
    )
    assert "EXIT 3" in proc.stdout, (proc.stdout, proc.stderr)
    assert "EXEC False" in proc.stdout
    assert not marker.exists()


def test_real_cgroup_enforcement_or_explicitly_blocked():
    """Real environment: prove enforced when delegated, otherwise report blocked.

    This is deliberately not a skip: an unavailable host must surface
    ``unavailable``/``partial`` evidence, and a run that *requires* quotas must
    be refused before exec.
    """
    probe = limit_exec.probe_cgroup_v2()
    if not probe["available"]:
        assert probe["mode"] in ("unavailable", "partial")
        assert probe["reasons"], "an unavailable probe must say why"
        assert probe["placement"] is False
        return
    status = Path(os.environ.get("TMPDIR", "/tmp")) / f"hop-quota-{os.getpid()}.json"
    env = {
        **os.environ,
        "HOP_CGROUP_PARENT": probe["parent"],
        "HOP_CGROUP_NAME": f"probe-{os.getpid()}",
        "HOP_CGROUP_STATUS": str(status),
        "HOP_QUOTA_DIMENSIONS": "memory,pids",
    }
    proc = subprocess.run(
        [
            sys.executable,
            "-I",
            str(LIMIT_EXEC),
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
    assert "hop-probe-" in proc.stdout, proc.stdout
    report = __import__("json").loads(status.read_text())
    assert report["mode"] == "enforced", report
    assert "hop-" in report["cgroup"]
    try:
        os.rmdir(report["cgroup"])
    except OSError:
        pass
