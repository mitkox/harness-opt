"""End-to-end M1 run orchestration (AOP-011): admit -> isolate -> execute -> freeze -> verify.

Every run has durable identity (repo/environment/model/harness/verifier
digests), a terminal status even for infrastructure failures, an idempotent
admission contract, and a verdict that comes only from the trusted verifier.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import platform
import shutil
import sys
import threading
import time
import traceback
import uuid
from functools import lru_cache

import yaml

from .bundle import compile_bundle, harness_build_digest, model_deployment_digest
from .contracts.base import new_id, utcnow
from .contracts.harness import HarnessBuild, HarnessName, HarnessStatus
from .contracts.model import ModelDeployment
from .contracts.records import (
    AttemptRecord,
    EvaluationResult,
    EventAuthority,
    EventSource,
    RunOutcome,
    RunRecord,
    RunStatus,
    TaskSpec,
    TrajectoryEvent,
    Verdict,
)
from .harnesses.pi_adapter import PiJsonAdapter, harness_executable_digest
from .harnesses.scripted import ScriptedHarness
from .inference import verify_served_model
from .runstore import RunStore
from .sandbox import SandboxSpec, assert_no_hidden_material, prepare_layout
from .storage import ArtifactStore
from .trajectories import REQUIRED_PREFIX_EVENTS, TERMINAL_EVENTS, EventLedger
from .verification import (
    VerifierSetupError,
    freeze_workspace,
    hash_hidden_dir,
    hash_tree,
    load_verifier_manifest,
    run_verification,
)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RUNS_DIR = os.path.join(ROOT, "runs")
VERIFIER_ROOT = os.environ.get("AOP_VERIFIER_ROOT", os.path.join(ROOT, ".hidden"))
HIDDEN_ROOT = os.path.join(VERIFIER_ROOT, "hidden-tests")
VERIFIER_VERSION = "debug-verifier-v2"
ADAPTER_REVISION = "pi-json-adapter-v1"


class InfraError(RuntimeError):
    def __init__(self, error_class: str, message: str):
        super().__init__(message)
        self.error_class = error_class


def load_case(case_id: str) -> tuple[dict, str, str, str]:
    case_dir = os.path.join(ROOT, "benchmarks", "development", case_id)
    with open(os.path.join(case_dir, "task.yaml")) as fh:
        task = yaml.safe_load(fh)
    with open(os.path.join(case_dir, "prompt.md")) as fh:
        prompt = fh.read()
    repo_src = os.path.join(case_dir, task.get("repo_subdir", "repo"))
    return task, prompt, repo_src, case_dir


def load_deployment(alias: str) -> ModelDeployment:
    with open(os.path.join(ROOT, "profiles", "models", "local-inventory.json")) as fh:
        inventory = json.load(fh)
    for record in inventory["deployments"]:
        if (record["deployment_id"].startswith(alias)
                or record.get("endpoint", {}).get("alias") == alias):
            return ModelDeployment.model_validate(record)
    raise KeyError(f"no local deployment for {alias}")


def environment_digest(case_dir: str) -> str:
    h = hashlib.sha256()
    for path in (os.path.join(case_dir, "environment.lock.json"),
                 os.path.join(ROOT, "toolchains.lock.json")):
        if os.path.exists(path):
            with open(path, "rb") as fh:
                h.update(fh.read())
            h.update(b"\0")
    h.update(f"python={platform.python_version()}|{sys.version}|{platform.platform()}".encode())
    return "sha256:" + h.hexdigest()


def qualify_pi_for_m1() -> HarnessBuild:
    """Probe the real pi binary; qualify only on positive probe evidence."""
    return _qualify_pi_for_m1()


@lru_cache(maxsize=1)
def _qualify_pi_for_m1() -> HarnessBuild:
    adapter = PiJsonAdapter()
    caps = adapter.probe()
    term = adapter.probe_termination()
    caps.supports_cancellation = bool(term.get("ok"))
    caps.probe_evidence["termination"] = term
    ok = (caps.supports_headless and caps.supports_streaming_events
          and caps.supports_model_selection and caps.supports_cancellation)
    executable = caps.executable_path or "pi"
    build = HarnessBuild(
        harness=HarnessName.PI, executable=executable, version=caps.build_version,
        adapter_revision=ADAPTER_REVISION, protocol="pi-jsonl-v3",
        status=HarnessStatus.QUALIFIED if ok else HarnessStatus.BLOCKED,
        blocked_reason="" if ok else "headless/stream/model-select/cancel probe failed",
        capabilities={"headless": caps.supports_headless,
                      "streaming": caps.supports_streaming_events,
                      "cancellation": caps.supports_cancellation,
                      "model_selection": caps.supports_model_selection,
                      "session_export": caps.supports_session_export},
        executable_sha256=caps.executable_sha256, probe_digest=caps.probe_digest,
        probe_evidence=caps.probe_evidence)
    build.harness_digest = harness_build_digest(build)
    return build


def check_scope(baseline: dict[str, str], snapshot: dict[str, str],
                allowed_paths: list[str], protected_paths: list[str]) -> dict:
    """Detect out-of-scope modifications/removals and protected-file edits.

    Additions are permitted (scratch files) unless they touch a protected path.
    ``allowed_paths`` and ``protected_paths`` are exact paths or glob patterns.
    """

    def matches(path: str, patterns: list[str]) -> bool:
        return any(fnmatch.fnmatch(path, p) or path == p for p in patterns)

    changed: dict[str, str] = {}
    for rel, digest in snapshot.items():
        if baseline.get(rel) != digest:
            changed[rel] = "modified" if rel in baseline else "added"
    for rel in baseline:
        if rel not in snapshot:
            changed[rel] = "removed"
    violations = []
    for rel, kind in changed.items():
        if matches(rel, protected_paths):
            violations.append({"path": rel, "kind": kind, "reason": "protected"})
        elif kind in ("modified", "removed") and not matches(rel, allowed_paths):
            violations.append({"path": rel, "kind": kind, "reason": "out_of_scope"})
    return {"changed": changed, "violations": sorted(violations, key=lambda v: v["path"]),
            "enforced": bool(allowed_paths or protected_paths)}


class Runner:
    def __init__(self, runs_dir: str = RUNS_DIR, store: RunStore | None = None,
                 artifacts: ArtifactStore | None = None):
        self.runs_dir = runs_dir
        os.makedirs(runs_dir, exist_ok=True)
        self.store = store or RunStore(os.path.join(runs_dir, "ledger.db"))
        self.artifacts = artifacts or ArtifactStore(os.path.join(runs_dir, "artifacts"))

    # -- event emission ----------------------------------------------------
    def _emit(self, ledger: EventLedger, seq: dict[str, int], run_id: str,
              attempt_id: str, bundle_digest: str, source: str,
              authority: EventAuthority, etype: str, attrs: dict,
              agent_id: str = "") -> None:
        seq[source] = seq.get(source, -1) + 1
        ledger.append(TrajectoryEvent(
            event_id=str(uuid.uuid4()), event_type=etype, run_id=run_id,
            attempt_id=attempt_id, agent_id=agent_id,
            source=EventSource(id=source, authority=authority),
            source_sequence=seq[source], observed_at=utcnow().isoformat(),
            bundle_digest=bundle_digest, attributes=attrs))

    # -- admission ---------------------------------------------------------
    def _replay(self, record: RunRecord) -> dict:
        run_dir = os.path.join(self.runs_dir, record.run_id)
        report_path = os.path.join(run_dir, "report.json")
        if os.path.exists(report_path):
            with open(report_path) as fh:
                report = json.load(fh)
        else:
            report = {"run_id": record.run_id, "case_id": record.case_id,
                      "bundle_digest": record.bundle_digest,
                      "outcome": (record.attempts[-1].outcome.value
                                  if record.attempts and record.attempts[-1].outcome
                                  else record.status.value),
                      "verdict": "inconclusive", "run_dir": run_dir}
        report["idempotent_replay"] = True
        report["run_status"] = record.status.value
        return report

    # -- public API --------------------------------------------------------
    def execute(self, case_id: str, model_alias: str = "qwen-flash-next",
                harness: str = "pi", timeout_s: float | None = None,
                cancel: threading.Event | None = None,
                idempotency_key: str = "",
                cancel_file: str | None = None) -> dict:
        cancel = cancel or threading.Event()
        task, prompt, repo_src, case_dir = load_case(case_id)
        budget = float(task.get("time_budget_s", 600))
        timeout_s = budget if timeout_s is None else min(timeout_s, budget)
        t0 = time.monotonic()

        deployment = load_deployment(model_alias)
        harness_build = qualify_pi_for_m1()
        bundle, _ = compile_bundle(deployment, harness_build, prompt)
        hidden_case_dir = os.path.join(HIDDEN_ROOT, case_id)
        verifier_error: VerifierSetupError | None = None
        try:
            verifier_manifest = load_verifier_manifest(hidden_case_dir)
            verifier_id = verifier_manifest["verifier_id"]
            verifier_version = verifier_manifest["version"]
        except VerifierSetupError as exc:
            verifier_manifest = None
            verifier_error = exc
            verifier_id, verifier_version = "", ""

        repo_digest = "sha256:" + hash_tree(repo_src)
        env_digest = environment_digest(case_dir)
        model_digest = model_deployment_digest(deployment)
        run_id = new_id("run")
        record, created = self.store.admit(RunRecord(
            run_id=run_id, task_id=task["task_id"], case_id=case_id,
            bundle_digest=bundle.digest, idempotency_key=idempotency_key,
            repo_snapshot_digest=repo_digest, environment_digest=env_digest,
            verifier_id=verifier_id, verifier_version=verifier_version,
            model_deployment_digest=model_digest,
            harness_digest=harness_build.harness_digest))
        if not created:
            return self._replay(record)
        run_id = record.run_id
        run_dir = os.path.join(self.runs_dir, run_id)
        os.makedirs(run_dir, exist_ok=True)
        ledger = EventLedger(os.path.join(run_dir, "events.jsonl"))
        seq: dict[str, int] = {}
        attempt = self.store.new_attempt(
            run_id, AttemptRecord(attempt_id=new_id("att"), worker_id=new_id("worker")))
        self._emit(ledger, seq, run_id, attempt.attempt_id, bundle.digest,
                   "aop-runner", EventAuthority.PLATFORM_OBSERVATION,
                   "run.admitted", {"task_id": task["task_id"], "bundle": bundle.digest})

        ctx = {"task": task, "prompt": prompt, "repo_src": repo_src, "case_dir": case_dir,
               "budget": budget, "timeout_s": timeout_s, "t0": t0,
               "deployment": deployment, "harness_build": harness_build, "bundle": bundle,
               "run_id": run_id, "run_dir": run_dir, "ledger": ledger, "seq": seq,
               "attempt": attempt, "harness": harness, "cancel": cancel,
               "cancel_file": cancel_file, "verifier_manifest": verifier_manifest,
               "verifier_error": verifier_error,
               "hidden_case_dir": hidden_case_dir, "repo_digest": repo_digest,
               "env_digest": env_digest, "model_digest": model_digest}
        try:
            return self._run_inner(ctx)
        except VerifierSetupError as exc:
            return self._infra_error(ctx, exc.error_class, exc)
        except InfraError as exc:
            return self._infra_error(ctx, exc.error_class, exc)
        except BaseException as exc:  # noqa: BLE001 - every failure is terminal & recorded
            return self._infra_error(ctx, "unclassified_infra_error", exc)

    # -- main lifecycle ----------------------------------------------------
    def _run_inner(self, ctx: dict) -> dict:
        (task, prompt, repo_src, budget, timeout_s, t0, deployment, harness_build,
         bundle, run_id, run_dir, ledger, seq, attempt, harness, cancel,
         cancel_file, verifier_manifest, verifier_error, hidden_case_dir, repo_digest,
         env_digest, model_digest) = tuple(ctx[k] for k in (
             "task", "prompt", "repo_src", "budget", "timeout_s", "t0", "deployment",
             "harness_build", "bundle", "run_id", "run_dir", "ledger", "seq", "attempt",
             "harness", "cancel", "cancel_file", "verifier_manifest", "verifier_error",
             "hidden_case_dir", "repo_digest", "env_digest", "model_digest"))
        client = None

        # 1. Attest that the registered local endpoint serves this deployment.
        if harness == "pi":
            try:
                model_evidence = verify_served_model(deployment)
            except Exception as exc:  # noqa: BLE001
                raise InfraError("model_identity_mismatch", str(exc)) from exc
            self._emit(ledger, seq, run_id, attempt.attempt_id, bundle.digest,
                       "aop-runner", EventAuthority.PLATFORM_OBSERVATION,
                       "model.attested", model_evidence)

        self.store.set_status(run_id, RunStatus.PREPARING)
        layout = prepare_layout(os.path.join(run_dir, "worker"), snapshot_src=repo_src)
        hidden_markers = (os.listdir(hidden_case_dir)
                          if os.path.isdir(hidden_case_dir) else [])
        assert_no_hidden_material(layout.workspace, hidden_markers)
        baseline_manifest = {rel: digest for rel, digest in _manifest_of(repo_src).items()}
        task_spec = TaskSpec(
            task_id=task["task_id"], case_id=task["case_id"], prompt=prompt,
            repo_snapshot_digest=repo_digest, environment_digest=env_digest,
            time_budget_s=budget, verifier_ref=task["verifier_ref"],
            verifier_id=(verifier_manifest or {}).get("verifier_id", ""),
            verifier_version=(verifier_manifest or {}).get("version", ""),
            split=task.get("split", "development"),
            allowed_paths=task.get("allowed_paths", []),
            protected_paths=task.get("protected_paths", []))
        manifest = {
            "run_id": run_id, "attempt_id": attempt.attempt_id,
            "bundle": bundle.model_dump(mode="json"),
            "task": task_spec.model_dump(mode="json"),
            "model": deployment.model_dump(mode="json"),
            "model_deployment_digest": model_digest,
            "harness": harness_build.model_dump(mode="json"),
            "harness_digest": harness_build.harness_digest,
            "verifier": {"id": task_spec.verifier_id, "version": task_spec.verifier_version,
                         "hidden_test_hash": hash_hidden_dir(hidden_case_dir)
                         if os.path.isdir(hidden_case_dir) else ""},
        }
        _write_json(os.path.join(run_dir, "manifest.json"), manifest)
        self._emit(ledger, seq, run_id, attempt.attempt_id, bundle.digest,
                   "aop-runner", EventAuthority.PLATFORM_OBSERVATION,
                   "workspace.prepared", {"workspace": layout.workspace})
        self.store.set_status(run_id, RunStatus.RUNNING)

        # 2. Adapter preparation.
        adapter: object
        if harness == "pi":
            spec = SandboxSpec(share_network=True,
                               ro_binds=_harness_ro_binds(harness_build.executable))
            adapter = PiJsonAdapter(deployment=deployment, spec=spec)
        elif harness.startswith("scripted:"):
            adapter = ScriptedHarness(harness.split(":", 1)[1])
        else:
            raise InfraError("unknown_harness", f"unknown harness {harness}")
        try:
            session = adapter.prepare(bundle.digest, os.path.join(run_dir, "worker"), "w1")
        except Exception as exc:  # noqa: BLE001
            raise InfraError("adapter_prepare_failure", str(exc)) from exc
        self._emit(ledger, seq, run_id, attempt.attempt_id, bundle.digest,
                   "aop-runner", EventAuthority.PLATFORM_OBSERVATION,
                   "harness.accepted", {"harness": harness})

        # 3. Harness execution.
        cancel_seen = _start_cancel_watch(cancel, cancel_file)
        cancel_event_emitted = False
        if cancel_seen:
            self._emit(ledger, seq, run_id, attempt.attempt_id, bundle.digest,
                       "aop-runner", EventAuthority.PLATFORM_OBSERVATION,
                       "cancel.requested", {"source": "pre-run"})
            cancel_event_emitted = True
        agent_t0 = time.monotonic()

        def on_native(raw: dict) -> None:
            if harness == "pi":
                from .harnesses.pi_adapter import native_to_trajectory_kind
                etype, auth = native_to_trajectory_kind(raw)
            else:
                etype, auth = "harness.scripted.event", "harness_observation"
            self._emit(ledger, seq, run_id, attempt.attempt_id, bundle.digest,
                       "pi-native" if harness == "pi" else "scripted",
                       EventAuthority(auth), etype, {"native_type": raw.get("type", "")})

        output = adapter.start(prompt, session, layout.workspace,
                               os.path.join(run_dir, "harness-stdout.log"),
                               os.path.join(run_dir, "harness-stderr.log"),
                               timeout_s, cancel, on_native_event=on_native)
        agent_dt = time.monotonic() - agent_t0
        self._emit(ledger, seq, run_id, attempt.attempt_id, bundle.digest,
                   "aop-runner", EventAuthority.PLATFORM_OBSERVATION,
                   f"harness.{output.terminal_status}", {"exit_code": output.exit_code})
        self._emit(ledger, seq, run_id, attempt.attempt_id, bundle.digest,
                   "harness-report" if harness == "pi" else "scripted",
                   EventAuthority.AGENT_CLAIM, "agent.claim",
                   {"text": output.agent_claim})

        eval_result: EvaluationResult | None = None
        scope: dict | None = None
        verifier_ran = False
        if output.terminal_status in ("timeout", "cancelled"):
            outcome = (RunOutcome.TIMEOUT if output.terminal_status == "timeout"
                       else RunOutcome.CANCELLED)
            verdict = Verdict.INCONCLUSIVE
            if output.terminal_status == "cancelled":
                if not cancel_event_emitted:
                    self._emit(ledger, seq, run_id, attempt.attempt_id, bundle.digest,
                               "aop-runner", EventAuthority.PLATFORM_OBSERVATION,
                               "cancel.requested", {"source": "during-run"})
                    cancel_event_emitted = True
                _write_json(os.path.join(run_dir, "cancellation.json"),
                            {"requested": True, "terminal_status": "cancelled",
                             "exit_code": output.exit_code, "harness": harness})
        else:
            self.store.set_status(run_id, RunStatus.VERIFYING)
            snapshot_dir = os.path.join(run_dir, "snapshot")
            snapshot_manifest = freeze_workspace(layout.workspace, snapshot_dir)
            self._safe_artifact(json.dumps(snapshot_manifest, sort_keys=True).encode(), run_id)
            self._emit(ledger, seq, run_id, attempt.attempt_id, bundle.digest,
                       "aop-runner", EventAuthority.PLATFORM_OBSERVATION,
                       "output.frozen", {"files": len(snapshot_manifest)})

            scope = check_scope(baseline_manifest, snapshot_manifest,
                                task.get("allowed_paths", []),
                                task.get("protected_paths", []))
            _write_json(os.path.join(run_dir, "scope.json"), scope)
            if scope.get("violations"):
                self._emit(ledger, seq, run_id, attempt.attempt_id, bundle.digest,
                           "aop-runner", EventAuthority.PLATFORM_OBSERVATION,
                           "scope.violation", {"violations": scope["violations"]})
                verdict, outcome = Verdict.FAIL, RunOutcome.FAIL
                eval_result = EvaluationResult(
                    run_id=run_id, attempt_id=attempt.attempt_id,
                    verifier_version=VERIFIER_VERSION or "scope-gate",
                    verdict=verdict, outcome=outcome, agent_claim=output.agent_claim,
                    details={"scope": scope}, error_class="scope_violation")
                _write_json(os.path.join(run_dir, "evaluation.json"),
                            eval_result.model_dump(mode="json"))
            else:
                if verifier_manifest is None:
                    raise verifier_error or VerifierSetupError(
                        "missing_verifier_fixture", f"no verifier manifest for {hidden_case_dir}")
                verifier_t0 = time.monotonic()
                eval_result = run_verification(
                    run_id, attempt.attempt_id, snapshot_dir, hidden_case_dir,
                    verifier_version=verifier_manifest["version"], timeout_s=120.0,
                    work_root=os.path.join(run_dir, "verify"), manifest=verifier_manifest)
                verifier_ran = True
                verifier_dt = time.monotonic() - verifier_t0
                eval_result.agent_claim = output.agent_claim
                _write_json(os.path.join(run_dir, "evaluation.json"),
                            eval_result.model_dump(mode="json"))
                self._emit(ledger, seq, run_id, attempt.attempt_id, bundle.digest,
                           "verifier", EventAuthority.VERIFIER_FACT,
                           "evaluation.recorded",
                           {"verdict": eval_result.verdict.value,
                            "error_class": eval_result.error_class,
                            "evidence_digest": eval_result.evidence_digest,
                            "verifier_seconds": round(verifier_dt, 2)})
                verdict, outcome = eval_result.verdict, eval_result.outcome

        required = REQUIRED_PREFIX_EVENTS
        if verifier_ran:
            required = required + ("evaluation.recorded",)
        completeness = ledger.completeness(required_events=required,
                                           terminal_events=TERMINAL_EVENTS)
        if not completeness["complete"] and outcome in (RunOutcome.PASS, RunOutcome.FAIL):
            outcome = RunOutcome.TELEMETRY_INCOMPLETE
        self.store.finalize_attempt(run_id, attempt.attempt_id,
                                    attempt.fencing_token, outcome, output.agent_claim,
                                    error_class=(eval_result.error_class if eval_result else ""))
        total_dt = time.monotonic() - t0
        report = {
            "run_id": run_id, "attempt_id": attempt.attempt_id, "case_id": task["case_id"],
            "bundle_digest": bundle.digest,
            "model_deployment_id": deployment.deployment_id,
            "model_deployment_digest": model_digest,
            "harness_digest": harness_build.harness_digest,
            "harness": harness_build.model_dump(mode="json"),
            "verifier": {"id": task_spec.verifier_id, "version": task_spec.verifier_version},
            "inputs": {"repo_snapshot_digest": repo_digest,
                       "environment_digest": env_digest},
            "outcome": outcome.value, "verdict": verdict.value,
            "error_class": (eval_result.error_class if eval_result else ""),
            "agent_claim": output.agent_claim,
            "agent_seconds": round(agent_dt, 2), "total_seconds": round(total_dt, 2),
            "completeness": completeness,
            "run_dir": run_dir,
            "note": "verdict is verifier-observed; agent_claim is narration only",
        }
        _write_json(os.path.join(run_dir, "report.json"), report)
        _write_json(os.path.join(run_dir, "trace.json"), {
            "spans": "client-side projection only; server prefill/decode timing "
                     "unavailable in M1",
            "events": len(ledger.read_all())})
        return report

    # -- failure handling --------------------------------------------------
    def _safe_artifact(self, data: bytes, run_id: str) -> None:
        try:
            self.artifacts.put(data, run_id)
        except Exception as exc:  # noqa: BLE001
            raise InfraError("storage_failure", str(exc)) from exc

    def _infra_error(self, ctx: dict, error_class: str, exc: BaseException) -> dict:
        run_id = ctx.get("run_id", "")
        run_dir = ctx.get("run_dir", "")
        ledger = ctx.get("ledger")
        seq = ctx.get("seq")
        attempt = ctx.get("attempt")
        bundle = ctx.get("bundle")
        os.makedirs(run_dir, exist_ok=True) if run_dir else None
        detail = {"error_class": error_class, "error_type": type(exc).__name__,
                  "message": str(exc),
                  "traceback_tail": traceback.format_exc()[-2000:]}
        if run_dir:
            try:
                _write_json(os.path.join(run_dir, "error.json"), detail)
            except OSError:
                pass
        if ledger is not None and seq is not None and attempt is not None and bundle is not None:
            try:
                self._emit(ledger, seq, run_id, attempt.attempt_id, bundle.digest,
                           "aop-runner", EventAuthority.PLATFORM_OBSERVATION,
                           "run.infra_error", {"error_class": error_class})
            except Exception:  # noqa: BLE001
                pass
        try:
            self.store.mark_infra_error(run_id, attempt.attempt_id, error_class)
        except Exception:  # noqa: BLE001
            pass
        report = {"run_id": run_id, "case_id": ctx.get("task", {}).get("case_id", ""),
                  "bundle_digest": bundle.digest if bundle else "",
                  "outcome": RunOutcome.INFRA_ERROR.value, "verdict": Verdict.ERROR.value,
                  "error_class": error_class, "run_dir": run_dir,
                  "error": detail}
        if run_dir:
            try:
                _write_json(os.path.join(run_dir, "report.json"), report)
            except OSError:
                pass
        return report


def _manifest_of(root: str) -> dict[str, str]:
    manifest = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            if os.path.islink(full) or "__pycache__" in full:
                continue
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            with open(full, "rb") as fh:
                manifest[rel] = hashlib.sha256(fh.read()).hexdigest()
    return manifest


def _harness_ro_binds(executable: str) -> list[tuple[str, str]]:
    """Read-only host paths the pinned harness needs (itself plus its Node runtime)."""
    binds: list[tuple[str, str]] = []
    real = os.path.realpath(executable)
    for root in (os.path.expanduser("~/.npm-global"),):
        if real.startswith(root) and os.path.isdir(root):
            binds.append((root, root))
    node = shutil.which("node")
    if node:
        # ``pi`` uses ``#!/usr/bin/env node``; bind the resolved runtime so the
        # sandbox does not fall back to an incompatible system node.
        binds.append((os.path.dirname(node), os.path.dirname(node)))
        node_real = os.path.realpath(node)
        node_prefix = os.path.dirname(os.path.dirname(node_real))
        if os.path.isdir(node_prefix):
            binds.append((node_prefix, node_prefix))
    if not binds and os.path.exists(real):
        binds.append((os.path.dirname(real), os.path.dirname(real)))
    seen: set[str] = set()
    unique: list[tuple[str, str]] = []
    for src, dst in binds:
        if src not in seen:
            seen.add(src)
            unique.append((src, dst))
    return unique


def _start_cancel_watch(cancel: threading.Event, cancel_file: str | None) -> bool:
    """Turn a durable cancel-file request into the in-process cancellation event."""
    if cancel.is_set():
        return True
    if not cancel_file:
        return False
    if os.path.exists(cancel_file):
        cancel.set()
        return True

    def watch() -> None:
        while not cancel.is_set():
            if os.path.exists(cancel_file):
                cancel.set()
                return
            time.sleep(0.1)

    threading.Thread(target=watch, name="aop-cancel-watch", daemon=True).start()
    return False


def _write_json(path: str, payload) -> None:
    with open(path, "w") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
