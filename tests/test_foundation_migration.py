import json
import subprocess
import sys
from pathlib import Path


def migrate(directory):
    script = Path(__file__).resolve().parents[1] / "scripts/migrate_m1_to_m2.py"
    return subprocess.run(
        [sys.executable, str(script), "--runs-dir", str(directory)],
        check=False,
        capture_output=True,
        text=True,
    )


def test_missing_history_is_empty_without_writes(tmp_path):
    directory = tmp_path / "absent"
    result = migrate(directory)
    assert result.returncode == 0 and json.loads(result.stdout) == []
    assert not directory.exists()


def test_corrupt_history_is_not_called_legacy(tmp_path):
    run = tmp_path / "run-broken"
    run.mkdir()
    (run / "events.jsonl").write_text("{broken\n")
    result = migrate(tmp_path)
    assert result.returncode == 1
    assert json.loads(result.stdout)[0]["legacy"] is False
    assert (run / "events.jsonl").read_text() == "{broken\n"


def test_historical_inspection_never_changes_artifacts(historical_runs):
    before = {str(p): p.read_bytes() for p in historical_runs.rglob("*") if p.is_file()}
    result = migrate(historical_runs)
    assert result.returncode == 0
    after = {str(p): p.read_bytes() for p in historical_runs.rglob("*") if p.is_file()}
    assert before == after
