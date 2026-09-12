"""End-to-end M1 run orchestration (AOP-011): admit -> isolate -> execute -> freeze -> verify."""
from __future__ import annotations

import glob
import json
import os
import threading
import time

import yaml

from .bundle import compile_bundle
from .contracts.base import new_id, utcnow
from .contracts.harness import HarnessBuild, HarnessName, HarnessStatus
from .contracts.model import ModelDeployment
from .contracts.records import (
    AttemptRecord,
    EventAuthority,
    RunOutcome,
    RunRecord,
    RunStatus,
    TaskSpec,
    TrajectoryEvent,
    Verdict,
)
from .harnesses.pi_adapter import PiJsonAdapter, native_to_trajectory_kind
from .harnesses.scripted import ScriptedHarness
from .runstore import RunStore
from .sandbox import SandboxSpec, assert_no_hidden_material, prepare_layout
from .storage import ArtifactStore
from .trajectories import EventLedger
from .verification import freeze_workspace, hash_hidden_dir, run_hidden_tests

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RUNS_DIR = os.path.join(ROOT, "runs")
HIDDEN_ROOT = os.path.join(ROOT, ".hidden", "hidden-tests")
VERIFIER_VERSION = "debug-verifier-v1"
ADAPTER_REVISION = "pi-json-adapter-v1"


def load_case(case_id: str) -> tuple[dict, str, str]:
    case_dir = os.path.join(ROOT, "benchmarks", "development", case_id)
    with open(os.path.join(case_dir, "task.yaml")) as fh:
        task = yaml.safe_load(fh)
    with open(os.path.join(case_dir, "prompt.md")) as fh:
        prompt = fh.read()
    return task, prompt, os.path.join(case_dir, task.get("repo_subdir", "repo"))


def load_deployment(alias: str) -> ModelDeployment:
    with open(os.path.join(ROOT, "profiles", "models", "local-inventory.json")) as fh:
        inventory = json.load(fh)
    for record in inventory["deployments"]:
        record = dict(record)
        record.pop("weight_shards", None)
        record.pop("weight_total_bytes", None)
        if record["deployment_id"].startswith(alias) or record["endpoint"]["alias"] == alias:
            return ModelDeployment.model_validate(record)
    raise KeyError(f"no local deployment for {alias}")


def qualify_pi_for_m1() -> HarnessBuild:
    """Probe the real pi binary; qualify only on positive headless evidence."""
    adapter = PiJsonAdapter()
    caps = adapter.probe()
    if not (caps.supports_headless and caps.supports_streaming_events
            and caps.supports_cancellation):
        return HarnessBuild(harness=HarnessName.PI, executable="pi",
                            version=caps.build_version, adapter_revision=ADAPTER_REVISION,
                            protocol="pi-jsonl-v3", status=HarnessStatus.BLOCKED,
                            blocked_reason="headless/cancel/stream probe failed")
    import shutil
    return HarnessBuild(harness=HarnessName.PI, executable=shutil.which("pi") or "pi",
                        version=caps.build_version, adapter_revision=ADAPTER_REVISION,
                        protocol="pi-jsonl-v3", status=HarnessStatus.QUALIFIED,
                        capabilities={"headless": True, "streaming": True,
                                      "cancellation": True, "session_export": True})


class Runner:
    def __init__(self, runs_dir: str = RUNS_DIR):
        self.runs_dir = runs_dir
        os.makedirs(runs_dir, exist_ok=True)
        self.store = RunStore(os.path.join(runs_dir, "ledger.db"))
        self.artifacts = ArtifactStore(os.path.join(runs_dir, "artifacts"))

    def _emit(self, ledger: EventLedger, seq: dict[str, int], run_id: str,
              attempt_id: str, bundle_digest: str, source: str,
              authority: EventAuthority, etype: str, attrs: dict) -> None:
        seq[source] = seq.get(source, -1) + 1
        ledger.append(TrajectoryEvent(
            event_id=new_id("evt"), event_type=etype, run_id=run_id,
            attempt_id=attempt_id, source_id=source, authority=authority,
            source_sequence=seq[source], observed_at=utcnow().isoformat(),
            bundle_digest=bundle_digest, trace_id="0" * 32, span_id="0" * 16,
            attributes=attrs))

    def execute(self, case_id: str, model_alias: str = "qwen-flash-next",
                harness: str = "pi", timeout_s: float | None = None,
                cancel: threading.Event | None = None,
                idempotency_key: str = "") -> dict:
        cancel = cancel or threading.Event()
        task, prompt, repo_src = load_case(case_id)
        budget = float(task.get("time_budget_s", 600))
        timeout_s = budget if timeout_s is None else min(timeout_s, budget)
        t0 = time.monotonic()

        deployment = load_deployment(model_alias)
        harness_build = qualify_pi_for_m1()
        bundle, _ = compile_bundle(deployment, harness_build, prompt)
        run_id = new_id("run")
        record, _ = self.store.admit(RunRecord(
            run_id=run_id, task_id=task["task_id"], case_id=case_id,
            bundle_digest=bundle.digest, idempotency_key=idempotency_key))
        attempt = self.store.new_attempt(
            run_id, AttemptRecord(attempt_id=new_id("att"), worker_id=new_id("worker")))
        run_dir = os.path.join(self.runs_dir, run_id)
        os.makedirs(run_dir)
        ledger = EventLedger(os.path.join(run_dir, "events.jsonl"))
        seq: dict[str, int] = {}
        self._emit(ledger, seq, run_id, attempt.attempt_id, bundle.digest,
                   "aop-runner", EventAuthority.TRUSTED_OBSERVER,
                   "run.admitted", {"task_id": task["task_id"], "bundle": bundle.digest})
        self.store.set_status(run_id, RunStatus.PREPARING)

        # Isolated workspace from the case repo snapshot only (never .hidden).
        layout = prepare_layout(os.path.join(run_dir, "worker"), snapshot_src=repo_src)
        hidden_dir = os.path.join(HIDDEN_ROOT, case_id)
        hidden_markers = os.listdir(hidden_dir) if os.path.isdir(hidden_dir) else []
        assert_no_hidden_material(layout.workspace, hidden_markers)
        task_spec = TaskSpec(task_id=task["task_id"], case_id=case_id, prompt=prompt,
                             time_budget_s=budget, verifier_ref=task["verifier_ref"])
        with open(os.path.join(run_dir, "manifest.json"), "w") as fh:
            json.dump({"run_id": run_id, "attempt_id": attempt.attempt_id,
                       "bundle": bundle.model_dump(mode="json"),
                       "task": task_spec.model_dump(mode="json"),
                       "model": deployment.model_dump(mode="json"),
                       "harness": harness_build.model_dump(mode="json")}, fh, indent=2)
        self._emit(ledger, seq, run_id, attempt.attempt_id, bundle.digest,
                   "aop-runner", EventAuthority.TRUSTED_OBSERVER,
                   "workspace.prepared", {"workspace": layout.workspace})
        self.store.set_status(run_id, RunStatus.RUNNING)

        # Harness execution. Acceptance is recorded; it is not completion.
        if harness == "pi":
            adapter: object = PiJsonAdapter()
        elif harness.startswith("scripted:"):
            _, behavior = harness.split(":", 1)
            adapter = ScriptedHarness(behavior)
        else:
            raise ValueError(f"unknown harness {harness}")
        self._emit(ledger, seq, run_id, attempt.attempt_id, bundle.digest,
                   "aop-runner", EventAuthority.TRUSTED_OBSERVER,
                   "harness.accepted", {"harness": harness})
        session = adapter.prepare(bundle.digest, os.path.join(run_dir, "worker"), "w1")
        agent_t0 = time.monotonic()

        def on_native(raw: dict) -> None:
            etype, auth = (native_to_trajectory_kind(raw) if harness == "pi"
                           else ("harness.scripted.event", "trusted_observer"))
            self._emit(ledger, seq, run_id, attempt.attempt_id, bundle.digest,
                       "pi-native" if harness == "pi" else "scripted",
                       EventAuthority(auth), etype, {"native_type": raw.get("type", "")})

        output = adapter.start(prompt, session, layout.workspace,
                               os.path.join(run_dir, "harness-stdout.log"),
                               os.path.join(run_dir, "harness-stderr.log"),
                               timeout_s, cancel, on_native_event=on_native)
        agent_dt = time.monotonic() - agent_t0
        self._emit(ledger, seq, run_id, attempt.attempt_id, bundle.digest,
                   "aop-runner", EventAuthority.TRUSTED_OBSERVER,
                   f"harness.{output.terminal_status}",
                   {"exit_code": output.exit_code})
        self._emit(ledger, seq, run_id, attempt.attempt_id, bundle.digest,
                   "harness-report" if harness == "pi" else "scripted",
                   EventAuthority.AGENT_REPORT, "agent.claim",
                   {"text": output.agent_claim})

        if output.terminal_status in ("timeout", "cancelled"):
            outcome = (RunOutcome.TIMEOUT if output.terminal_status == "timeout"
                       else RunOutcome.CANCELLED)
            eval_result = None
            verdict = Verdict.INCONCLUSIVE
        else:
            self.store.set_status(run_id, RunStatus.VERIFYING)
            snapshot_dir = os.path.join(run_dir, "snapshot")
            manifest = freeze_workspace(layout.workspace, snapshot_dir)
            self.artifacts.put(json.dumps(manifest, sort_keys=True).encode(), run_id)
            self._emit(ledger, seq, run_id, attempt.attempt_id, bundle.digest,
                       "aop-runner", EventAuthority.TRUSTED_OBSERVER,
                       "output.frozen", {"files": len(manifest)})
            hidden_tests = sorted(glob.glob(os.path.join(hidden_dir, "test_hidden_*.py")))
            if not hidden_tests:
                raise FileNotFoundError(f"no hidden tests for {case_id}")
            verifier_t0 = time.monotonic()
            eval_result = run_hidden_tests(
                run_id, attempt.attempt_id, snapshot_dir, hidden_dir,
                ["python3", "-m", "pytest", *hidden_tests, "-q", "-p", "no:cacheprovider"],
                120.0, hash_hidden_dir(hidden_dir), VERIFIER_VERSION)
            verifier_dt = time.monotonic() - verifier_t0
            eval_result.agent_claim = output.agent_claim
            with open(os.path.join(run_dir, "evaluation.json"), "w") as fh:
                fh.write(eval_result.model_dump_json(indent=2))
            self._emit(ledger, seq, run_id, attempt.attempt_id, bundle.digest,
                       "verifier", EventAuthority.TRUSTED_OBSERVER,
                       "evaluation.recorded",
                       {"verdict": eval_result.verdict.value,
                        "verifier_seconds": round(verifier_dt, 2)})
            verdict = eval_result.verdict
            outcome = eval_result.outcome

        completeness = ledger.completeness()
        if not completeness["complete"] and outcome in (RunOutcome.PASS, RunOutcome.FAIL):
            outcome = RunOutcome.TELEMETRY_INCOMPLETE
        self.store.finalize_attempt(run_id, attempt.attempt_id,
                                    attempt.fencing_token, outcome, output.agent_claim)
        total_dt = time.monotonic() - t0
        report = {
            "run_id": run_id, "attempt_id": attempt.attempt_id, "case_id": case_id,
            "bundle_digest": bundle.digest,
            "model_deployment_id": deployment.deployment_id,
            "harness": harness_build.model_dump(mode="json"),
            "outcome": outcome.value, "verdict": verdict.value,
            "agent_claim": output.agent_claim,
            "agent_seconds": round(agent_dt, 2), "total_seconds": round(total_dt, 2),
            "completeness": completeness,
            "run_dir": run_dir,
            "note": "verdict is verifier-observed; agent_claim is narration only",
        }
        with open(os.path.join(run_dir, "report.json"), "w") as fh:
            json.dump(report, fh, indent=2)
        with open(os.path.join(run_dir, "trace.json"), "w") as fh:
            json.dump({"spans": "client-side projection only; server prefill/decode "
                                "timing unavailable in M1",
                       "events": len(ledger.read_all())}, fh, indent=2)
        return report
