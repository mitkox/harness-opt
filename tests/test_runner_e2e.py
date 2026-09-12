"""End-to-end runner tests (AOP-011): hermetic scripted arms plus real verification.

The scripted harness makes pass/fail/timeout/cancel/scope and infra-error
paths deterministic without a GPU. Real verifier isolation still runs.
"""
import json
import os
import threading
import time

import jsonschema
import pytest

from hop.runner import Runner
from hop.runstore import RunStore


def _runner(tmp_path, artifacts=None):
    return Runner(runs_dir=str(tmp_path / "runs"), artifacts=artifacts)


def test_fail_arm_baseline_unfixed(tmp_path):
    rep = _runner(tmp_path).execute("debug-offbyone", harness="scripted:succeed",
                                    idempotency_key="e2e-fail")
    assert rep["outcome"] == "fail" and rep["verdict"] == "fail"
    assert rep["model_deployment_id"].startswith("qwen-flash-next")
    types = [json.loads(line)["event_type"]
             for line in open(f"{rep['run_dir']}/events.jsonl") if line.strip()]
    assert "run.admitted" in types and "harness.accepted" in types
    assert "evaluation.recorded" in types and "output.frozen" in types
    assert rep["completeness"]["complete"] is True


def test_repair_arm_passes(tmp_path):
    rep = _runner(tmp_path).execute("debug-offbyone", harness="scripted:repair",
                                    idempotency_key="e2e-repair")
    assert rep["outcome"] == "pass" and rep["verdict"] == "pass"
    assert rep["error_class"] == ""


def test_scope_violation_is_rejected(tmp_path):
    rep = _runner(tmp_path).execute("debug-offbyone", harness="scripted:scope_violation",
                                    idempotency_key="e2e-scope")
    assert rep["outcome"] == "fail" and rep["error_class"] == "scope_violation"
    scope = json.load(open(f"{rep['run_dir']}/scope.json"))
    assert any(v["path"] == "reproducer_visible.py" for v in scope["violations"])


def test_forged_success_claim_still_fails(tmp_path):
    rep = _runner(tmp_path).execute("debug-offbyone", harness="scripted:claim_success",
                                    idempotency_key="e2e-forge")
    assert rep["outcome"] == "fail"
    assert "trust me" in rep["agent_claim"]


def test_timeout_arm(tmp_path):
    rep = _runner(tmp_path).execute("debug-offbyone", harness="scripted:hang",
                                    timeout_s=1.0, idempotency_key="e2e-timeout")
    assert rep["outcome"] == "timeout" and rep["verdict"] == "inconclusive"


def test_cancellation_has_durable_artifact(tmp_path):
    rd = tmp_path / "runs"
    runner = Runner(runs_dir=str(rd))
    cancel_file = str(rd / "cancel.flag")
    box = {}

    def run():
        box["rep"] = runner.execute(
            "debug-offbyone", harness="scripted:hang", timeout_s=60,
            cancel_file=cancel_file, idempotency_key="e2e-cancel")

    thread = threading.Thread(target=run)
    thread.start()
    time.sleep(0.6)
    with open(cancel_file, "w") as fh:
        fh.write("cancel\n")
    thread.join(40)
    rep = box["rep"]
    assert rep["outcome"] == "cancelled" and rep["verdict"] == "inconclusive"
    run_dir = rep["run_dir"]
    cancel = json.load(open(f"{run_dir}/cancellation.json"))
    assert cancel["requested"] is True and cancel["terminal_status"] == "cancelled"
    from hop.trajectories import EventLedger
    ledger = EventLedger(f"{run_dir}/events.jsonl")
    events = ledger.read_all()
    assert any(e.event_type == "cancel.requested" for e in events)
    assert any(e.event_type == "harness.cancelled" for e in events)
    assert ledger.completeness()["complete"] is True
    # workspace and durable run artifacts preserved after cancellation
    assert os.path.exists(os.path.join(run_dir, "worker", "workspace", "reproducer_visible.py"))
    assert os.path.exists(os.path.join(run_dir, "report.json"))
    assert os.path.exists(os.path.join(run_dir, "manifest.json"))


def test_duplicate_admission_is_idempotent(tmp_path):
    runner = _runner(tmp_path)
    first = runner.execute("debug-offbyone", harness="scripted:repair",
                           idempotency_key="dup-key")
    second = runner.execute("debug-offbyone", harness="scripted:succeed",
                            idempotency_key="dup-key")
    assert second.get("idempotent_replay") is True
    assert second["run_id"] == first["run_id"]
    assert second["outcome"] == first["outcome"]


def test_every_run_pins_input_identity(tmp_path):
    rep = _runner(tmp_path).execute("debug-wordcount", harness="scripted:repair",
                                    idempotency_key="e2e-pin")
    manifest = json.load(open(f"{rep['run_dir']}/manifest.json"))
    assert manifest["bundle"]["digest"].startswith("sha256:")
    assert manifest["model"]["deployment_id"]
    assert manifest["model"]["weight_shards"], "weight-shard manifest must be recorded"
    assert manifest["harness"]["version"] == "0.85.1"
    assert manifest["harness_digest"].startswith("sha256:")
    assert manifest["verifier"]["id"] == "verifier:debug-wordcount"
    assert manifest["task"]["repo_snapshot_digest"].startswith("sha256:")
    assert manifest["task"]["environment_digest"].startswith("sha256:")
    assert rep["inputs"]["repo_snapshot_digest"].startswith("sha256:")
    record = RunStore(str(tmp_path / "runs" / "ledger.db")).get(rep["run_id"])
    assert record.repo_snapshot_digest and record.environment_digest
    assert record.model_deployment_digest and record.harness_digest
    assert record.verifier_id and record.verifier_version


def test_emitted_events_validate_against_published_schema(tmp_path):
    rep = _runner(tmp_path).execute("debug-offbyone", harness="scripted:repair",
                                    idempotency_key="e2e-schema")
    schema = json.load(open("specs/trajectory-event.schema.json"))
    checker = jsonschema.FormatChecker()
    count = 0
    for line in open(f"{rep['run_dir']}/events.jsonl"):
        if not line.strip():
            continue
        jsonschema.validate(json.loads(line), schema, format_checker=checker)
        count += 1
    assert count > 5


def test_missing_verifier_fixture_is_infra_error(tmp_path, monkeypatch):
    empty_root = tmp_path / "verifier-root"
    os.makedirs(empty_root / "hidden-tests")
    monkeypatch.setattr("hop.runner.HIDDEN_ROOT", str(empty_root / "hidden-tests"))
    rep = _runner(tmp_path).execute("debug-offbyone", harness="scripted:succeed",
                                    idempotency_key="e2e-nofix")
    assert rep["outcome"] == "infra_error" and rep["verdict"] == "error"
    assert rep["error_class"] == "missing_verifier_fixture"
    assert os.path.exists(f"{rep['run_dir']}/report.json")
    assert os.path.exists(f"{rep['run_dir']}/error.json")


def test_invalid_verifier_fixture_is_infra_error(tmp_path, monkeypatch):
    root = tmp_path / "verifier-root" / "hidden-tests" / "debug-offbyone"
    os.makedirs(root)
    (root / "verifier.json").write_text('{"verifier_id": "x"}')
    monkeypatch.setattr("hop.runner.HIDDEN_ROOT", str(tmp_path / "verifier-root" / "hidden-tests"))
    rep = _runner(tmp_path).execute("debug-offbyone", harness="scripted:succeed",
                                    idempotency_key="e2e-badfix")
    assert rep["outcome"] == "infra_error"
    assert rep["error_class"] == "invalid_verifier_fixture"


class _BrokenArtifacts:
    def put(self, data, run_id):
        raise OSError("disk full")

    def get(self, *a, **k):
        raise OSError("disk full")


def test_storage_failure_is_durable_infra_error(tmp_path):
    rep = _runner(tmp_path, artifacts=_BrokenArtifacts()).execute(
        "debug-offbyone", harness="scripted:succeed", idempotency_key="e2e-storage")
    assert rep["outcome"] == "infra_error" and rep["error_class"] == "storage_failure"
    assert os.path.exists(f"{rep['run_dir']}/report.json")


def test_adapter_prepare_failure_is_infra_error(tmp_path, monkeypatch):
    def boom(self, *a, **k):
        raise RuntimeError("prepare failed")

    monkeypatch.setattr("hop.harnesses.scripted.ScriptedHarness.prepare", boom)
    rep = _runner(tmp_path).execute("debug-offbyone", harness="scripted:succeed",
                                    idempotency_key="e2e-prepare")
    assert rep["outcome"] == "infra_error"
    assert rep["error_class"] == "adapter_prepare_failure"
