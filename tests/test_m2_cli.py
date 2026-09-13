"""M2 commit 6: investigation CLI, replay-check, migration, storage negatives."""
import json
import os

import pytest

from hop import investigate as inv
from hop.runner import Runner


@pytest.fixture()
def repair_run(tmp_path):
    runner = Runner(runs_dir=str(tmp_path / "runs"))
    rep = runner.execute("debug-offbyone", harness="scripted:repair",
                         idempotency_key="cli-repair")
    assert rep["outcome"] == "pass"
    return runner, rep


def test_cli_show_trajectory_completeness(repair_run, capsys):
    from hop.cli import main
    import os as _os
    runner, rep = repair_run
    _os.environ["HOP_RUNS_DIR"] = runner.runs_dir
    assert main(["run", "show", rep["run_id"]]) == 0
    out = capsys.readouterr().out
    assert rep["bundle_digest"] in out
    assert main(["run", "completeness", rep["run_id"]]) == 0
    comp = json.loads(capsys.readouterr().out)
    assert comp["complete"] is True and comp["integrity"]["ok"] is True


def test_cli_skills_tools_trace_verify_artifacts_replay(repair_run, capsys):
    from hop.cli import main
    import os as _os
    runner, rep = repair_run
    _os.environ["HOP_RUNS_DIR"] = runner.runs_dir
    for verb in ("skills", "tools", "trace", "verify", "artifacts",
                 "replay-check"):
        assert main(["run", verb, rep["run_id"]]) == 0, verb
        payload = capsys.readouterr().out
        assert payload.strip(), verb
    assert main(["run", "trajectory", rep["run_id"],
                 "--event-type", "run.admitted"]) == 0
    events = json.loads(capsys.readouterr().out)
    assert len(events) == 1 and events[0]["event_type"] == "run.admitted"


def test_fresh_process_reopens_trajectory(repair_run):
    from hop.trajectories import EventLedger
    _, rep = repair_run
    ledger = EventLedger(os.path.join(rep["run_dir"], "events.jsonl"))
    assert len(ledger.read_all()) > 10
    assert ledger.verify_integrity()["ok"] is True
    assert ledger.completeness_for_outcome("pass")["complete"] is True


def test_corrupted_artifact_and_dangling_ref_visible(tmp_path):
    from hop.storage import ArtifactStore
    store = ArtifactStore(str(tmp_path / "artifacts"))
    ref = store.put(b"secret-free-payload", "run1")
    blob = os.path.join(str(tmp_path / "artifacts", ), "blobs",
                        ref.digest.split(":")[1][:2], ref.digest.split(":")[1])
    with open(blob, "r+b") as fh:
        fh.seek(0)
        fh.write(b"X")
    assert store.verify(ref.digest) is False
    with pytest.raises(ValueError, match="tampered"):
        store.get(ref.digest, "run1")
    with pytest.raises(PermissionError):
        store.get("sha256:" + "ff" * 32, "run1")  # dangling reference


def test_migration_keeps_m1_runs_readable():
    if not os.path.isdir("runs/run-c85be3744478"):
        pytest.skip("needs author's local M1 acceptance run (runs/ is machine-local, not in repo)")
    import subprocess
    proc = subprocess.run(
        ["python3", "scripts/migrate_m1_to_m2.py", "--runs-dir", "runs"],
        capture_output=True, text=True, timeout=60,
        env={**os.environ, "PYTHONPATH": "src"})
    assert proc.returncode == 0, proc.stderr[-2000:]
    rows = json.loads(proc.stdout)
    assert rows, "expected existing runs under runs/"
    # The M1 acceptance run (committed evidence) must remain readable.
    accepted = [r for r in rows if r["run"] == "run-c85be3744478"]
    assert accepted and accepted[0]["readable"] is True
    assert accepted[0]["events"] > 100
    # Pre-UUID M1 dev runs predate the finalized contract: flagged legacy,
    # never silently rewritten or claimed readable.
    for row in rows:
        if not row["readable"]:
            assert row["legacy"] is True and "error" in row
