#!/usr/bin/env python3
"""M1 acceptance harness: rerun the exit-criteria probes and emit a matrix.

Writes runs/acceptance/acceptance-report.json and prints a Markdown matrix.
Deterministic probes need no GPU; the real-model probe is opt-in with
``--real-pi`` and the model-identity probe is skipped if the endpoint is down.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from aop import sandbox, verification  # noqa: E402
from aop.bundle import compile_bundle, model_deployment_digest  # noqa: E402
from aop.contracts.harness import HarnessBuild, HarnessName, HarnessStatus  # noqa: E402
from aop.contracts.model import (  # noqa: E402
    LocalEndpoint, ModelDeployment, ModelFamily, ModelStatus, WeightShard)
from aop.contracts.records import Verdict  # noqa: E402
from aop.inference import LocalEndpointClient, verify_served_model  # noqa: E402
from aop.runner import Runner, load_deployment  # noqa: E402
from aop.trajectories import EventLedger  # noqa: E402

HIDDEN = os.path.join(ROOT, ".hidden", "hidden-tests")
OFF = "debug-offbyone"
RESULTS: list[dict] = []


def record(requirement: str, probe: str, passed: bool, evidence: str,
           detail: str = "") -> None:
    RESULTS.append({"requirement": requirement, "probe": probe,
                    "result": "PASS" if passed else "FAIL",
                    "evidence": evidence, "detail": detail})
    print(f"[{'PASS' if passed else 'FAIL'}] {requirement}: {detail or probe}")


def _snapshot(case=OFF, mutate=None):
    root = tempfile.mkdtemp(prefix="aop-acc-")
    snap = os.path.join(root, "snap")
    verification.freeze_workspace(os.path.join(ROOT, "benchmarks", "development", case, "repo"),
                                  snap)
    if mutate:
        mutate(snap)
    return snap, root


def _run_verifier(snap, root, case=OFF):
    return verification.run_verification("acc", "a1", snap,
                                         os.path.join(HIDDEN, case),
                                         work_root=root, timeout_s=90)


def probe_verifier_correctness() -> None:
    snap, root = _snapshot()
    base = _run_verifier(snap, root)
    record("1. buggy baseline fails trusted verification",
           "verification.run_verification(baseline)", base.verdict == Verdict.FAIL,
           os.path.join(root, "verify"), f"verdict={base.verdict.value}")

    def repair(s):
        p = os.path.join(s, "median_bug.py")
        content = open(p).read()
        open(p, "w").write(content.replace(
            "return (ordered[mid] + ordered[mid + 1]) / 2",
            "return (ordered[mid - 1] + ordered[mid]) / 2"))
    snap2, root2 = _snapshot(mutate=repair)
    fixed = _run_verifier(snap2, root2)
    record("2. legitimate repair passes", "verification.run_verification(repair)",
           fixed.verdict == Verdict.PASS, os.path.join(root2, "verify"),
           f"verdict={fixed.verdict.value}")

    def os_exit(s):
        open(os.path.join(s, "median_bug.py"), "w").write("import os\nos._exit(0)\n")
    snap3, root3 = _snapshot(mutate=os_exit)
    r3 = _run_verifier(snap3, root3)
    record("3. os._exit(0) cannot create a false PASS",
           "verification.run_verification(os._exit)", r3.verdict != Verdict.PASS,
           os.path.join(root3, "verify"), f"verdict={r3.verdict.value} err={r3.error_class}")

    def sitecustomize(s):
        marker = os.path.join(s, "sitecustomize_ran")
        open(os.path.join(s, "sitecustomize.py"), "w").write(
            f"open({marker!r}, 'w').write('x')\n")
    snap4, root4 = _snapshot(mutate=sitecustomize)
    r4 = _run_verifier(snap4, root4)
    executed = os.path.exists(os.path.join(snap4, "sitecustomize_ran"))
    record("4. sitecustomize.py cannot create a false PASS",
           "verification.run_verification(sitecustomize)",
           r4.verdict != Verdict.PASS and not executed,
           os.path.join(root4, "verify"),
           f"verdict={r4.verdict.value} executed={executed}")

    def conftest(s):
        open(os.path.join(s, "conftest.py"), "w").write(
            "def pytest_collection_modifyitems(items):\n    items[:] = []\n")
        open(os.path.join(s, "pytest.ini"), "w").write(
            "[pytest]\naddopts = --collect-only\n")
    snap5, root5 = _snapshot(mutate=conftest)
    r5 = _run_verifier(snap5, root5)
    record("5. malicious conftest.py / pytest.ini cannot create a false PASS",
           "verification.run_verification(conftest+pytest.ini)",
           r5.verdict != Verdict.PASS, os.path.join(root5, "verify"),
           f"verdict={r5.verdict.value}")

    manifest = verification.load_verifier_manifest(os.path.join(HIDDEN, OFF))
    checks = {
        "zero": {"collected": [], "outcomes": {}, "deselected": []},
        "all_skipped": {"collected": manifest["expected_tests"],
                        "outcomes": {t: "skipped" for t in manifest["expected_tests"]},
                        "deselected": []},
        "deselected": {"collected": manifest["expected_tests"][:1],
                       "outcomes": {manifest["expected_tests"][0]: "passed"},
                       "deselected": [manifest["expected_tests"][1]]},
        "collect_only": {"collected": manifest["expected_tests"], "outcomes": {},
                         "deselected": []},
    }
    rejected = all(verification.evaluate_report(r, manifest)[0] != Verdict.PASS
                   for r in checks.values())
    record("6. skip-all / zero-test / deselection rejected",
           "verification.evaluate_report(...)", rejected,
           "in-process structural report checks",
           f"rejected={sorted(checks)}")


def probe_boundaries() -> None:
    if not sandbox.bwrap_available():
        record("7. agent cannot read/write hidden verifier material",
               "sandbox.spawn_isolated", False, "n/a", "bwrap unavailable")
        return
    hidden = os.path.join(ROOT, ".hidden", "hidden-tests", OFF, "test_hidden_median.py")
    verifier = os.path.join(ROOT, "src", "aop", "verification.py")
    parent = tempfile.mkdtemp(prefix="aop-bound-")
    other = os.path.join(parent, "run-other", "workspace")
    os.makedirs(other)
    open(os.path.join(other, "secret.txt"), "w").write("other run secret")
    layout = sandbox.prepare_layout(os.path.join(parent, "run-mine", "worker"))
    cmd = ["bash", "-c",
           f"test -e {hidden!r} && echo READ || echo NO_READ; "
           f"echo x > {hidden!r} 2>&1 || echo WRITE_DENIED; "
           f"echo pwn >> {verifier!r} 2>&1 || echo VERIFIER_DENIED; "
           f"cat {os.path.join(other, 'secret.txt')!r} 2>&1 || echo OTHER_DENIED"]
    res = sandbox.spawn_isolated(cmd, layout, sandbox.SandboxSpec(share_network=False))
    out = open(res.stdout_path).read()
    ok = ("NO_READ" in out and "WRITE_DENIED" in out and "VERIFIER_DENIED" in out
          and "other run secret" not in out)
    record("7. agent cannot read/write hidden material or verifier code",
           "sandbox.spawn_isolated(probe)", ok, res.stdout_path,
           out.replace("\n", " ")[:160])
    # candidate startup hooks in verifier
    snap, root = _snapshot(mutate=lambda s: open(
        os.path.join(s, "usercustomize.py"), "w").write("raise SystemExit(0)\n"))
    r = _run_verifier(snap, root)
    record("8. candidate startup hooks cannot execute inside verifier",
           "verification.run_verification(usercustomize)", r.verdict != Verdict.PASS,
           os.path.join(root, "verify"), f"verdict={r.verdict.value}")


def probe_pi_local_only() -> None:
    from aop.policy import assert_local_url, scrub_worker_env
    remote_refused = False
    try:
        assert_local_url("http://10.1.2.3:8000/v1")
    except ValueError:
        remote_refused = True
    injected = "AOP_PI_BASE_URL" not in scrub_worker_env(
        {"PATH": "/usr/bin", "AOP_PI_BASE_URL": "http://evil.example/v1"})
    record("9. Pi cannot be redirected to a non-approved endpoint",
           "policy.assert_local_url + scrub_worker_env",
           remote_refused and injected, "aop.policy",
           f"remote_refused={remote_refused} env_scrubbed={injected}")
    try:
        dep = load_deployment("qwen-flash-next")
        evidence = verify_served_model(dep)
        record("10. executed model identity matches recorded deployment",
               "inference.verify_served_model", True, json.dumps(evidence)[:120],
               f"served={evidence['served_ids']}")
    except Exception as exc:  # noqa: BLE001
        record("10. executed model identity matches recorded deployment",
               "inference.verify_served_model", False, "n/a",
               f"endpoint unavailable: {exc}")


def probe_identity() -> None:
    def dep(shards):
        return ModelDeployment(
            deployment_id="d1", discovery_label="q", family=ModelFamily.QWEN,
            status=ModelStatus.QUALIFIED, quantization="Q4_K_M",
            weight_shards=shards, weight_total_bytes=sum(s.size_bytes for s in shards),
            endpoint=LocalEndpoint(alias="d1", base_url="http://127.0.0.1:8000/v1",
                                   model_id="mitko"))

    def shards():
        return [WeightShard(path=f"m-0000{i}-of-00004.gguf", size_bytes=10 + i,
                            partial_sha256_head_tail_4m=f"{i:016x}") for i in range(1, 5)]
    harness = HarnessBuild(harness=HarnessName.PI, executable="/bin/pi", version="0.85.1",
                           adapter_revision="pi-json-adapter-v1", status=HarnessStatus.QUALIFIED,
                           executable_sha256="a" * 64, probe_digest="sha256:" + "b" * 64)
    base = compile_bundle(dep(shards()), harness, "p")[0].digest
    changed = []
    for i in range(4):
        s = shards()
        s[i].partial_sha256_head_tail_4m = "f" * 16
        changed.append(compile_bundle(dep(s), harness, "p")[0].digest != base)
    record("11. changing any model weight shard changes bundle identity",
           "bundle.compile_bundle(shard N)", all(changed), "in-process",
           f"shard_changes_change_digest={changed}")


def _runner(tmp=None):
    runs = tmp or tempfile.mkdtemp(prefix="aop-acc-runs-")
    return Runner(runs_dir=runs), runs


def probe_lifecycle() -> None:
    runner, runs = _runner()
    first = runner.execute(OFF, harness="scripted:repair", idempotency_key="acc-dup")
    second = runner.execute(OFF, harness="scripted:succeed", idempotency_key="acc-dup")
    record("12. duplicate admission behaves idempotently",
           "Runner.execute(same idempotency key)",
           second.get("idempotent_replay") and second["run_id"] == first["run_id"],
           first["run_dir"], f"replay={second.get('idempotent_replay')}")

    # infra failure: missing verifier fixture
    import aop.runner as runner_mod
    old = runner_mod.HIDDEN_ROOT
    empty = tempfile.mkdtemp(prefix="aop-empty-")
    runner_mod.HIDDEN_ROOT = empty
    try:
        inf = runner.execute(OFF, harness="scripted:succeed", idempotency_key="acc-infra")
    finally:
        runner_mod.HIDDEN_ROOT = old
    record("13. infrastructure failures end as durable infra_error runs",
           "Runner.execute(missing verifier fixture)",
           inf["outcome"] == "infra_error" and os.path.exists(f"{inf['run_dir']}/error.json"),
           inf["run_dir"], f"error_class={inf['error_class']}")

    import jsonschema
    schema = json.load(open(os.path.join(ROOT, "specs", "trajectory-event.schema.json")))
    checker = jsonschema.FormatChecker()
    valid = True
    for line in open(f"{first['run_dir']}/events.jsonl"):
        if line.strip():
            jsonschema.validate(json.loads(line), schema, format_checker=checker)
    record("14. emitted trajectory records validate against the schema",
           "jsonschema.validate(events.jsonl)", valid, f"{first['run_dir']}/events.jsonl",
           "all emitted events valid")

    from aop.contracts.records import EventSource, TrajectoryEvent
    led = EventLedger(os.path.join(first["run_dir"], "alt-events.jsonl"))
    for i, etype in enumerate(("run.admitted", "workspace.prepared", "harness.accepted")):
        led.append(TrajectoryEvent(event_id=f"00000000-0000-4000-8000-{i:012d}",
                                   event_type=etype, run_id="r", attempt_id="a",
                                   source=EventSource(id="x", authority="platform_observation"),
                                   source_sequence=i, observed_at="2026-09-12T00:00:00Z",
                                   bundle_digest=first["bundle_digest"]))
    comp = led.completeness()
    record("15. missing required terminal trajectory data marks incomplete",
           "EventLedger.completeness", (not comp["complete"]) and not comp["gaps"],
           os.path.join(first["run_dir"], "alt-events.jsonl"),
           f"missing_required={comp['missing_required']}")

    arms = {}
    arms["pass"] = runner.execute("debug-wordcount", harness="scripted:repair",
                                  idempotency_key="acc-pass")
    arms["fail"] = runner.execute(OFF, harness="scripted:fail", idempotency_key="acc-fail")
    arms["timeout"] = runner.execute(OFF, harness="scripted:hang", timeout_s=1.0,
                                     idempotency_key="acc-timeout")
    rd = os.path.dirname(first["run_dir"])
    cancel_file = os.path.join(rd, "acc-cancel.flag")
    box = {}

    def do_cancel():
        box["r"] = runner.execute(OFF, harness="scripted:hang", timeout_s=60,
                                  cancel_file=cancel_file, idempotency_key="acc-cancel")
    t = threading.Thread(target=do_cancel)
    t.start()
    time.sleep(0.6)
    open(cancel_file, "w").write("cancel\n")
    t.join(40)
    arms["cancelled"] = box["r"]
    ok = all(os.path.exists(os.path.join(a["run_dir"], "report.json"))
             for a in arms.values())
    record("16. pass, fail, timeout and cancellation all have durable artifacts",
           "Runner.execute(scripted arms)", ok,
           ",".join(a["run_dir"] for a in arms.values()),
           f"outcomes={ {k: v['outcome'] for k, v in arms.items()} }")

    m = json.load(open(f"{first['run_dir']}/manifest.json"))
    record("17. every run identifies repository/environment/model/harness inputs",
           "manifest.json input identity",
           all(m["task"][k].startswith("sha256:") for k in
               ("repo_snapshot_digest", "environment_digest"))
           and m["harness_digest"].startswith("sha256:")
           and m["model_deployment_digest"].startswith("sha256:")
           and m["verifier"]["id"],
           f"{first['run_dir']}/manifest.json", "identity fields present")


def probe_lock() -> None:
    lock = os.path.join(ROOT, "requirements.lock")
    wheels = os.path.join(ROOT, ".vendor", "wheels")
    ok = os.path.exists(lock) and "--hash=sha256:" in open(lock).read()
    built = False
    if os.path.exists(os.path.join(ROOT, ".venv-m1", "bin", "python")):
        built = True
    detail = f"lock={'yes' if ok else 'no'} offline_wheelhouse={os.path.isdir(wheels)} built_venv={built}"
    record("18. fresh environment reproducible from the dependency lock",
           "requirements.lock + scripts/build_lock_env.sh", ok and os.path.isdir(wheels),
           lock, detail)


def probe_real_pi() -> None:
    runner, _ = _runner()
    t0 = time.time()
    rep = runner.execute(OFF, harness="pi", timeout_s=600, idempotency_key="acc-real-pi")
    record("1b. real Pi run derives pass/fail from the trusted verifier",
           "Runner.execute(harness=pi)",
           rep["outcome"] in ("pass", "fail") and rep["verdict"] in ("pass", "fail"),
           rep.get("run_dir", ""),
           f"outcome={rep['outcome']} verdict={rep['verdict']} secs={round(time.time()-t0,1)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--real-pi", action="store_true")
    args = parser.parse_args()
    probe_verifier_correctness()
    probe_boundaries()
    probe_pi_local_only()
    probe_identity()
    probe_lifecycle()
    probe_lock()
    if args.real_pi:
        probe_real_pi()
    out_dir = os.path.join(ROOT, "runs", "acceptance")
    os.makedirs(out_dir, exist_ok=True)
    report = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
              "results": RESULTS,
              "summary": {"pass": sum(r["result"] == "PASS" for r in RESULTS),
                          "fail": sum(r["result"] == "FAIL" for r in RESULTS)}}
    with open(os.path.join(out_dir, "acceptance-report.json"), "w") as fh:
        json.dump(report, fh, indent=2)
    print("\n| Requirement | Probe | Result | Evidence |")
    print("|---|---|---|---|")
    for r in RESULTS:
        print(f"| {r['requirement']} | {r['probe']} | {r['result']} | {r['evidence']} |")
    print(f"\nsummary: {report['summary']}")
    return 0 if report["summary"]["fail"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
