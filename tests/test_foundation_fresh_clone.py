"""Fresh-clone hygiene for the M1 -> M2 migration surface.

``runs/`` is gitignored, so a fresh clone (and every ESF Cube sandbox) has no
``runs/`` directory. Absence is expected: the migration tool must report an
empty set and exit zero, and the tests that need historical M1 evidence must
skip when that evidence is unavailable -- while a present-but-corrupt run still
errors loudly. These tests exercise the real subprocess entry point used by
``tests/test_m2_cli.py`` and ``tests/test_rename_compat.py``.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATE = REPO_ROOT / "scripts/migrate_m1_to_m2.py"


def _migrate(runs_dir):
    return subprocess.run(
        [sys.executable, str(MIGRATE), "--runs-dir", str(runs_dir)],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(REPO_ROOT),
        env={**os.environ, "PYTHONPATH": "src"},
    )


def test_absent_runs_dir_is_empty_success(tmp_path):
    """A fresh clone has no gitignored runs/: empty set, exit 0, no side effects."""
    runs = tmp_path / "runs"
    proc = _migrate(runs)
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert json.loads(proc.stdout) == []
    assert not runs.exists(), "inspection must never create the runs directory"


def test_runs_path_that_is_a_file_is_empty_success(tmp_path):
    """A non-directory runs path has no run set to list; report empty, not crash."""
    runs = tmp_path / "runs"
    runs.write_text("not a directory\n")
    proc = _migrate(runs)
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert json.loads(proc.stdout) == []
    assert runs.read_text() == "not a directory\n"


def test_present_but_corrupt_evidence_still_errors(tmp_path):
    """Absence is tolerated; corrupt committed evidence is still a failure."""
    runs = tmp_path / "runs"
    run_dir = runs / "run-broken"
    run_dir.mkdir(parents=True)
    events = run_dir / "events.jsonl"
    events.write_text("{broken\n")

    proc = _migrate(runs)
    assert proc.returncode == 1, proc.stdout[-2000:]
    rows = json.loads(proc.stdout)
    assert [row["run"] for row in rows] == ["run-broken"]
    assert rows[0]["readable"] is False
    assert rows[0]["legacy"] is False  # malformed, not a legacy pre-schema run
    assert "error" in rows[0]
    assert events.read_text() == "{broken\n"  # read-only inspection


def test_affected_tests_skip_when_m1_evidence_unavailable(tmp_path):
    """The two migration tests skip -- not fail -- without tracked M1 evidence.

    Built as a throwaway checkout with the same sources but no docs/evidence,
    then run through the pinned interpreter so the skip is an integration fact,
    not a unit assertion about a helper.
    """
    clone = tmp_path / "clone"
    clone.mkdir()
    for name in ("tests", "scripts", "src", "specs", ".hidden", "pyproject.toml"):
        source = REPO_ROOT / name
        target = clone / name
        if source.is_dir():
            shutil.copytree(
                source,
                target,
                ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"),
            )
        else:
            shutil.copy2(source, target)
    assert not (clone / "runs").exists()
    assert not (clone / "docs/evidence/m1/real-pi-run").exists()

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_m2_cli.py::test_migration_keeps_m1_runs_readable",
            "tests/test_rename_compat.py::test_historical_m1_runs_readable",
            "-o",
            "addopts=",
            "-q",
            "--no-header",
            "-p",
            "no:cacheprovider",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
        cwd=str(clone),
        env={**os.environ, "PYTHONPATH": "src"},
    )
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 0, combined[-3000:]
    assert "2 skipped" in combined, combined[-3000:]
