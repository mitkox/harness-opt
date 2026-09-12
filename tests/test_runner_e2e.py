"""End-to-end runner tests with scripted harness (AOP-011). Hermetic, no GPU."""
import json
import threading

from aop.runner import Runner


def _runner(tmp_path):
    return Runner(runs_dir=str(tmp_path / "runs"))


def test_fail_arm_baseline_unfixed(tmp_path):
    rep = _runner(tmp_path).execute("debug-offbyone", harness="scripted:succeed",
                                    idempotency_key="e2e-fail")
    assert rep["outcome"] == "fail" and rep["verdict"] == "fail"
    assert rep["model_deployment_id"].startswith("qwen-flash-next")
    events = open(f"{rep['run_dir']}/events.jsonl").read().strip().split("\n")
    types = [json.loads(e)["event_type"] for e in events]
    assert "run.admitted" in types and "harness.accepted" in types
    assert "evaluation.recorded" in types and "output.frozen" in types
    assert rep["completeness"]["complete"] is True
    assert "verifier-observed" in rep["note"]


def test_forged_success_claim_still_fails(tmp_path):
    rep = _runner(tmp_path).execute("debug-offbyone", harness="scripted:claim_success",
                                    idempotency_key="e2e-forge")
    assert rep["outcome"] == "fail"
    assert "trust me" in rep["agent_claim"]


def test_timeout_and_cancel_arms(tmp_path):
    runner = _runner(tmp_path)
    rep = runner.execute("debug-offbyone", harness="scripted:hang", timeout_s=1.0,
                         idempotency_key="e2e-timeout")
    assert rep["outcome"] == "timeout" and rep["verdict"] == "inconclusive"
    cancel = threading.Event()
    box = {}
    thread = threading.Thread(target=lambda: box.update(
        rep=runner.execute("debug-offbyone", harness="scripted:hang", timeout_s=60.0,
                           cancel=cancel, idempotency_key="e2e-cancel")))
    thread.start()
    cancel.set()
    thread.join(30)
    assert box["rep"]["outcome"] == "cancelled"


def test_every_run_pins_identities(tmp_path):
    rep = _runner(tmp_path).execute("debug-wordcount", harness="scripted:succeed",
                                    idempotency_key="e2e-pin")
    manifest = json.load(open(f"{rep['run_dir']}/manifest.json"))
    assert manifest["bundle"]["digest"].startswith("sha256:")
    assert manifest["model"]["deployment_id"]
    assert manifest["harness"]["version"] == "0.85.1"
    assert manifest["task"]["verifier_ref"]
    # hidden content must not be in the agent workspace
    ws_files = json.dumps(manifest)
    assert "test_hidden" not in open(f"{rep['run_dir']}/manifest.json").read()
    import os
    for _, _, files in os.walk(os.path.join(rep["run_dir"], "worker", "workspace")):
        for name in files:
            assert not name.startswith("test_hidden")
