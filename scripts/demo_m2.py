#!/usr/bin/env python3
"""M2 end-to-end demonstration: success, failure, timeout, cancellation,
collector outage, skill run, plus one real local-model Pi run.

Writes docs/evidence/m2/demo-report.json. All inference is local; the only
model calls go to the registered loopback deployments.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from aop.runner import Runner  # noqa: E402


def _summarize(rep: dict) -> dict:
    run_dir = rep["run_dir"]
    types: dict[str, int] = {}
    authorities: dict[str, int] = {}
    trace_ids = set()
    if os.path.exists(os.path.join(run_dir, "events.jsonl")):
        for line in open(os.path.join(run_dir, "events.jsonl")):
            if not line.strip():
                continue
            evt = json.loads(line)
            types[evt["event_type"]] = types.get(evt["event_type"], 0) + 1
            auth = evt["source"]["authority"]
            authorities[auth] = authorities.get(auth, 0) + 1
            trace_ids.add(evt["trace_id"])
    artifacts = []
    for root, _, files in os.walk(os.path.join(run_dir)):
        for name in files:
            if name.endswith((".json", ".log")):
                artifacts.append(os.path.relpath(os.path.join(root, name), run_dir))
    trace = {}
    for name in ("trace-otel.json", "trace.json"):
        path = os.path.join(run_dir, name)
        if os.path.exists(path):
            with open(path) as fh:
                trace = json.load(fh)
            trace["_file"] = name
            break
    return {
        "run_id": rep.get("run_id"), "outcome": rep.get("outcome"),
        "verdict": rep.get("verdict"), "trace_id": rep.get("trace_id", ""),
        "event_trace_ids": sorted(trace_ids),
        "bundle_digest": rep.get("bundle_digest"),
        "model_deployment_id": rep.get("model_deployment_id"),
        "event_types": types, "authorities": authorities,
        "completeness_policy": rep.get("completeness_policy", {}),
        "telemetry_incomplete": rep.get("telemetry_incomplete"),
        "resources": rep.get("resources", {}),
        "spool": rep.get("spool", {}),
        "replay": rep.get("replay", {}),
        "trace_spans": ([s["name"] for s in trace.get("spans", [])]
                        if isinstance(trace.get("spans"), list) else []),
        "artifacts": sorted(artifacts)[:40],
        "run_dir": run_dir,
    }


def main() -> int:
    runner = Runner()
    report: dict = {"arms": {}, "notes": []}

    arms = [
        ("A_success", {"case": "debug-offbyone", "harness": "scripted:repair",
                       "key": "m2-demo-A"}),
        ("B_verifier_failure", {"case": "debug-offbyone", "harness": "scripted:succeed",
                                "key": "m2-demo-B"}),
        ("C_timeout", {"case": "debug-offbyone", "harness": "scripted:hang",
                       "key": "m2-demo-C", "timeout": 2.0}),
        ("F_skill", {"case": "debug-offbyone", "harness": "scripted:skill_repair",
                     "key": "m2-demo-F", "skills": ["debug-helper@v3"]}),
    ]
    for name, spec in arms:
        rep = runner.execute(spec["case"], harness=spec["harness"],
                             timeout_s=spec.get("timeout"),
                             idempotency_key=spec["key"],
                             skill_ids=spec.get("skills"))
        report["arms"][name] = _summarize(rep)
        print(f"{name}: {rep['outcome']}/{rep['verdict']} {rep['run_id']}",
              flush=True)

    # D: cancellation via durable cancel file.
    cancel_file = os.path.join(runner.runs_dir, "m2-demo-cancel.flag")
    if os.path.exists(cancel_file):
        os.remove(cancel_file)
    box: dict = {}

    def run_cancel():
        box["rep"] = runner.execute(
            "debug-offbyone", harness="scripted:hang", timeout_s=60,
            cancel_file=cancel_file, idempotency_key="m2-demo-D")

    thread = threading.Thread(target=run_cancel)
    thread.start()
    time.sleep(0.8)
    with open(cancel_file, "w") as fh:
        fh.write("cancel\n")
    thread.join(60)
    report["arms"]["D_cancelled"] = _summarize(box["rep"])
    print(f"D_cancelled: {box['rep']['outcome']} {box['rep']['run_id']}", flush=True)

    # E: collector unavailable during execution; ledger stays authoritative.
    os.environ["AOP_COLLECTOR_DOWN"] = "1"
    try:
        rep_e = runner.execute("debug-offbyone", harness="scripted:repair",
                               idempotency_key="m2-demo-E")
    finally:
        del os.environ["AOP_COLLECTOR_DOWN"]
    report["arms"]["E_collector_outage"] = _summarize(rep_e)
    print(f"E_collector_outage: {rep_e['outcome']} spool={rep_e['spool']}",
          flush=True)
    from aop.telemetry.spool import SpoolQueue
    spool = SpoolQueue(os.path.join(runner.runs_dir, "_spool"),
                       os.path.join(runner.runs_dir, "_collector"))
    report["arms"]["E_recovery_flush"] = spool.flush(rep_e["run_id"])
    print(f"E_recovery: {report['arms']['E_recovery_flush']}", flush=True)

    # G: real local-model Pi run (traced admission -> tools -> verifier).
    try:
        rep_g = runner.execute("debug-offbyone", harness="pi",
                               idempotency_key="m2-demo-G-real-pi")
        report["arms"]["G_real_pi"] = _summarize(rep_g)
        print(f"G_real_pi: {rep_g['outcome']}/{rep_g['verdict']} "
              f"{rep_g['run_id']}", flush=True)
    except Exception as exc:  # noqa: BLE001 - record, do not fail the demo
        report["arms"]["G_real_pi"] = {"error": str(exc)[:500]}
        print(f"G_real_pi FAILED: {exc}", flush=True)

    os.makedirs("docs/evidence/m2", exist_ok=True)
    with open("docs/evidence/m2/demo-report.json", "w") as fh:
        json.dump(report, fh, indent=2, sort_keys=True)
    print("wrote docs/evidence/m2/demo-report.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
