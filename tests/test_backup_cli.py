"""`hop backup` CLI wiring (WP-3.7, AOP-051).

Reachability, nested help from any cwd, JSON when redirected, and exit codes
0/2/3. The module entry point `python -m hop.backup` stays supported and is not
exercised here.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from hop.cli import main

ROOT = Path(__file__).resolve().parents[1]


def _run(args: list[str], base: Path, *, cwd: Path | None = None) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "src"),
        "HOP_HOME": str(base / "home"),
        "HOP_RUNS_DIR": str(base / "runs"),
    }
    return subprocess.run(
        [sys.executable, "-m", "hop.cli", *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        cwd=str(cwd or ROOT),
        timeout=120,
    )


def test_backup_group_help_lists_verbs(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["backup", "--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "hop backup" in out
    for verb in ("create", "verify", "restore", "chain"):
        assert verb in out


@pytest.mark.parametrize(
    "argv",
    [
        ["backup", "--help"],
        ["backup", "create", "--help"],
        ["backup", "verify", "--help"],
        ["backup", "restore", "--help"],
        ["backup", "chain", "--help"],
    ],
)
def test_backup_nested_help_from_another_cwd(tmp_path, argv):
    proc = _run(argv, tmp_path, cwd=tmp_path)
    assert proc.returncode == 0
    assert "usage: hop backup" in proc.stdout


def test_backup_verbs_round_trip_with_json_when_redirected(tmp_path):
    created = _run(["backup", "create", "--destination", str(tmp_path / "backup")], tmp_path)
    assert created.returncode == 0, created.stderr
    payload = json.loads(created.stdout)  # redirected output is JSON
    assert payload["destination"] == str(tmp_path / "backup")

    verified = _run(["backup", "verify", "--backup", str(tmp_path / "backup")], tmp_path)
    assert verified.returncode == 0
    assert json.loads(verified.stdout)["ok"] is True

    chained = _run(["backup", "chain"], tmp_path)
    assert chained.returncode == 0
    assert json.loads(chained.stdout)["runs"] == 0

    restored = _run(
        ["backup", "restore", "--backup", str(tmp_path / "backup")],
        tmp_path / "restored",
    )
    assert restored.returncode == 0, restored.stderr
    assert json.loads(restored.stdout)["restored"] == payload["files"]


def test_backup_verify_digest_mismatch_exits_3(tmp_path):
    _run(["backup", "create", "--destination", str(tmp_path / "backup")], tmp_path)
    target = next((tmp_path / "backup").rglob("local-inventory.json"))
    target.write_text(target.read_text() + "\n# tampered\n")
    proc = _run(["backup", "verify", "--backup", str(tmp_path / "backup")], tmp_path)
    assert proc.returncode == 3
    assert json.loads(proc.stdout)["ok"] is False


def test_backup_missing_backup_is_invalid_input_exit_2(tmp_path):
    proc = _run(["backup", "verify", "--backup", str(tmp_path / "absent")], tmp_path)
    assert proc.returncode == 2
    assert json.loads(proc.stderr)["code"] == "backup_truncated"


def test_backup_missing_required_argument_exits_2(tmp_path):
    proc = _run(["backup", "create"], tmp_path)
    assert proc.returncode == 2
