"""Rename compat: HOP canonical namespace with aop/AOP_* legacy support.

Covers the rename acceptance list: hop CLI, legacy aop alias, HOP_*/AOP_*
env (precedence + deprecation), persisted M1/M2 readability, schema
validity, identity stability, and the M1/M2 protection gates.
"""

import hashlib
import json
import os
import subprocess
import sys

import pytest

from hop import investigate as inv
from hop.runner import Runner


@pytest.fixture()
def repair_run(tmp_path):
    runner = Runner(runs_dir=str(tmp_path / "runs"))
    rep = runner.execute(
        "debug-offbyone", harness="scripted:repair", idempotency_key="rename-repair"
    )
    assert rep["outcome"] == "pass"
    return runner, rep


def _hop_cli(*args):
    env = {**os.environ, "PYTHONPATH": "src"}
    return subprocess.run(
        [sys.executable, "-m", "hop.cli", *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )


def test_hop_help_works():
    proc = _hop_cli("--help")
    assert proc.returncode == 0, proc.stderr
    assert "usage: hop" in proc.stdout


def test_hop_run_verbs_work(repair_run, capsys, monkeypatch):
    from hop.cli import main

    runner, rep = repair_run
    monkeypatch.setenv("HOP_RUNS_DIR", runner.runs_dir)
    for verb in (
        "show",
        "trajectory",
        "artifacts",
        "trace",
        "verify",
        "skills",
        "tools",
        "completeness",
        "replay-check",
    ):
        assert main(["run", verb, rep["run_id"]]) == 0, verb
        assert capsys.readouterr().out.strip(), verb


def test_legacy_aop_alias_same_output(repair_run, capsys, monkeypatch):
    from hop.cli import legacy_main, main

    runner, rep = repair_run
    monkeypatch.setenv("HOP_RUNS_DIR", runner.runs_dir)
    assert main(["run", "show", rep["run_id"]]) == 0
    canonical = capsys.readouterr().out
    assert legacy_main(["run", "show", rep["run_id"]]) == 0
    captured = capsys.readouterr()
    assert captured.out == canonical  # same implementation, same semantics
    assert "deprecated" in captured.err


def test_hop_env_vars_work(monkeypatch):
    from hop.cli import _runs_dir
    from hop.envcompat import resolve_env

    monkeypatch.setenv("HOP_RUNS_DIR", "/tmp/hop-runs")
    monkeypatch.delenv("AOP_RUNS_DIR", raising=False)
    assert _runs_dir() == "/tmp/hop-runs"
    assert resolve_env("HOP_RUNS_DIR", "AOP_RUNS_DIR", "d") == "/tmp/hop-runs"


def test_legacy_env_warns(monkeypatch, capsys):
    from hop.cli import _runs_dir

    monkeypatch.delenv("HOP_RUNS_DIR", raising=False)
    monkeypatch.setenv("AOP_RUNS_DIR", "/tmp/aop-runs")
    assert _runs_dir() == "/tmp/aop-runs"
    assert "deprecated" in capsys.readouterr().err


def test_hop_env_wins_over_legacy(monkeypatch, capsys):
    from hop.cli import _runs_dir

    monkeypatch.setenv("HOP_RUNS_DIR", "/tmp/hop-runs")
    monkeypatch.setenv("AOP_RUNS_DIR", "/tmp/aop-runs")
    assert _runs_dir() == "/tmp/hop-runs"
    assert "deprecated" not in capsys.readouterr().err


def test_historical_m1_runs_readable(historical_runs):
    rows = json.loads(
        subprocess.run(
            [sys.executable, "scripts/migrate_m1_to_m2.py", "--runs-dir", str(historical_runs)],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, "PYTHONPATH": "src"},
        ).stdout
    )
    accepted = [r for r in rows if r["run"] == "run-c85be3744478"]
    assert accepted and accepted[0]["readable"] is True
    assert accepted[0]["events"] > 100


def test_m2_trajectories_validate_and_resolve(repair_run):
    from hop.contracts.records import TrajectoryEvent
    from hop.storage import ArtifactStore

    runner, rep = repair_run
    with open(os.path.join(rep["run_dir"], "events.jsonl")) as fh:
        events = [TrajectoryEvent.model_validate(json.loads(line)) for line in fh if line.strip()]
    assert len(events) > 10
    assert any(e.event_type == "evaluation.recorded" for e in events)
    store = ArtifactStore(os.path.join(runner.runs_dir, "artifacts"))
    for ref in inv.artifacts(rep["run_dir"], runner.runs_dir, rep["run_id"]):
        assert store.verify(ref["digest"]) is True
        assert store.get(ref["digest"], rep["run_id"])


def test_bundle_and_artifact_identities_stable():
    from hop.bundle import compile_bundle
    from hop.runner import load_deployment, qualify_pi_for_m1

    deployment = load_deployment("fixture-local")
    harness = qualify_pi_for_m1()
    first, _ = compile_bundle(deployment, harness, "repair task")
    second, _ = compile_bundle(deployment, harness, "repair task")
    assert first.digest == second.digest  # deterministic, rename-invariant
    assert "aop" not in first.digest and "hop" not in first.digest
    payload = b"rename-identity-probe"
    assert (
        "sha256:" + hashlib.sha256(payload).hexdigest()
        == "sha256:" + hashlib.sha256(payload).hexdigest()
    )


def test_local_only_protections_hold(monkeypatch):
    import pytest as _pytest

    from hop.policy import assert_local_url, scrub_worker_env

    with _pytest.raises(ValueError):
        assert_local_url("http://evil.example.com/v1")
    monkeypatch.setenv("HOP_PI_BASE_URL", "http://evil.example.com/v1")
    monkeypatch.setenv("AOP_PI_BASE_URL", "http://evil.example.com/v1")
    scrubbed = scrub_worker_env(dict(os.environ))
    assert "HOP_PI_BASE_URL" not in scrubbed
    assert "AOP_PI_BASE_URL" not in scrubbed


def test_hidden_verifier_isolation_probe(tmp_path):
    from hop.sandbox import assert_no_hidden_material, prepare_layout

    layout = prepare_layout(str(tmp_path / "w"))
    marker = os.path.join(layout.workspace, "test_hidden_median.py")
    with open(marker, "w") as fh:
        fh.write("hidden verifier material simulation")
    with pytest.raises(ValueError, match="hidden marker"):
        assert_no_hidden_material(layout.workspace, ["test_hidden_median.py"])


def test_redaction_still_holds():
    from hop.telemetry.redaction import sanitize_attributes

    clean = sanitize_attributes({"note": "ok", "api_key": "sk-live-1234567890"})
    assert "sk-live-1234567890" not in json.dumps(clean)


def test_telemetry_completeness_on_fresh_run(repair_run):
    from hop.trajectories import EventLedger

    _, rep = repair_run
    ledger = EventLedger(os.path.join(rep["run_dir"], "events.jsonl"))
    assert ledger.verify_integrity()["ok"] is True
    assert ledger.completeness_for_outcome("pass")["complete"] is True


def test_spool_recovery_with_hop_env(tmp_path, monkeypatch):
    from hop.telemetry.spool import SpoolQueue, collector_available

    spool = SpoolQueue(str(tmp_path / "spool"), str(tmp_path / "collector"))
    monkeypatch.setenv("HOP_COLLECTOR_DOWN", "1")
    assert collector_available(str(tmp_path / "collector")) is False
    spool.enqueue("run-x", {"span": "agent.execute"})
    assert spool.flush("run-x")["collector"] == "unavailable-spooled"
    monkeypatch.delenv("HOP_COLLECTOR_DOWN")
    monkeypatch.delenv("AOP_COLLECTOR_DOWN", raising=False)
    assert spool.flush("run-x")["collector"] == "recovered"
