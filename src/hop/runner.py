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
from .telemetry.observe import (
    InferenceRecord,
    SkillCatalogEntry,
    normalize_tool_call,
    sample_resources,
    skill_catalog_digest,
)
from .telemetry.redaction import sanitize_attributes
from .telemetry.spool import SpoolQueue
from .telemetry.taxonomy import assert_known_event
from .telemetry.tracing import RunTracer
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
              agent_id: str = "", parent_agent_id: str = "",
              trace_id: str = "", span_id: str = "",
              parent_span_id: str = "", model_digest: str = "",
              harness_digest: str = "", repo_digest: str = "",
              env_digest: str = "", skill_id: str = "",
              skill_version: str = "", skill_digest: str = "",
              tool_id: str = "", tool_version: str = "",
              classification: str = "internal",
              payload_refs: list[str] | None = None,
              agent_claim_text: str = "") -> None:
        """M2 envelope emission. Redacts attributes; never inlines secrets."""
        assert_known_event(etype)
        now = utcnow().isoformat()
        seq[source] = seq.get(source, -1) + 1
        clean = sanitize_attributes(attrs)
        event = TrajectoryEvent(
            event_id=str(uuid.uuid4()), event_type=etype, run_id=run_id,
            attempt_id=attempt_id, agent_id=agent_id,
            parent_agent_id=parent_agent_id,
            source=EventSource(id=source, authority=authority),
            source_sequence=seq[source], event_timestamp=now,
            observed_at=now,
            trace_id=trace_id or "0" * 32, span_id=span_id or "0" * 16,
            parent_span_id=parent_span_id,
            model_deployment_digest=model_digest, harness_digest=harness_digest,
            bundle_digest=bundle_digest, repo_snapshot_digest=repo_digest,
            environment_digest=env_digest,
            skill_id=skill_id, skill_version=skill_version,
            skill_digest=skill_digest, tool_id=tool_id,
            tool_version=tool_version,
            data_classification=classification,
            payload_refs=list(payload_refs or []),
            attributes=clean)
        if agent_claim_text and authority == EventAuthority.AGENT_CLAIM:
            event.attributes["claim_digest"] = (
                "sha256:" + hashlib.sha256(
                    agent_claim_text.encode()).hexdigest())
        ledger.append(event)

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
                cancel_file: str | None = None,
                skill_ids: list[str] | None = None) -> dict:
        cancel = cancel or threading.Event()
        task, prompt, repo_src, case_dir = load_case(case_id)
        budget = float(task.get("time_budget_s", 600))
        timeout_s = budget if timeout_s is None else min(timeout_s, budget)
        t0 = time.monotonic()

        deployment = load_deployment(model_alias)
        harness_build = qualify_pi_for_m1()
        bundle, _ = compile_bundle(deployment, harness_build, prompt,
                                   skill_variant_ids=skill_ids)
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
        # M2: one primary run trace; trajectory events carry its IDs.
        tracer = RunTracer(run_id)
        wf = tracer.start("workflow.run", {"aop.run_id": run_id,
                                           "aop.bundle_digest": bundle.digest})
        adm = tracer.start("admission.resolve", {"aop.task_id": task["task_id"]})
        ident = {"task_id": task["task_id"], "bundle": bundle.digest,
                 "model_deployment_digest": model_digest,
                 "harness_digest": harness_build.harness_digest,
                 "repo_snapshot_digest": repo_digest,
                 "environment_digest": env_digest,
                 "trace_id": tracer.trace_id}
        self._emit(ledger, seq, run_id, attempt.attempt_id, bundle.digest,
                   "aop-runner", EventAuthority.PLATFORM_OBSERVATION,
                   "run.admitted", ident,
                   trace_id=tracer.trace_id, span_id=adm.span_id,
                   parent_span_id=wf.span_id,
                   model_digest=model_digest,
                   harness_digest=harness_build.harness_digest,
                   repo_digest=repo_digest, env_digest=env_digest)
        tracer.finish(adm)
        self._emit(ledger, seq, run_id, attempt.attempt_id, bundle.digest,
                   "aop-runner", EventAuthority.PLATFORM_OBSERVATION,
                   "run.started", {"queue_delay_s": round(time.monotonic() - t0, 3)},
                   trace_id=tracer.trace_id, span_id=wf.span_id,
                   model_digest=model_digest,
                   harness_digest=harness_build.harness_digest,
                   repo_digest=repo_digest, env_digest=env_digest)
        # Prompt/context snapshot as content-addressed artifact (never inline).
        # Best-effort here: the snapshot-stage write later is the strict one
        # (storage failures surface as durable infra_error there).
        prompt_ref = self._artifact_ref_or_none(prompt.encode(), run_id,
                                                "confidential")
        self._emit(ledger, seq, run_id, attempt.attempt_id, bundle.digest,
                   "aop-runner", EventAuthority.PLATFORM_OBSERVATION,
                   "context.prepared",
                   {"prompt_digest": (prompt_ref.digest if prompt_ref else ""),
                    "prompt_bytes": (prompt_ref.size_bytes if prompt_ref else 0)},
                   trace_id=tracer.trace_id, span_id=wf.span_id,
                   model_digest=model_digest,
                   harness_digest=harness_build.harness_digest,
                   repo_digest=repo_digest, env_digest=env_digest,
                   payload_refs=([prompt_ref.digest] if prompt_ref else []))
        # Skill catalog exposure (M2 records exposure separately from use).
        skill_entries = [SkillCatalogEntry(
            s.split("@")[0], s.split("@")[1] if "@" in s else "v1",
            "sha256:" + hashlib.sha256(s.encode()).hexdigest(), "")
            for s in bundle.skill_variant_ids]
        catalog_digest = skill_catalog_digest(skill_entries) if skill_entries else (
            "sha256:" + hashlib.sha256(b"empty-catalog").hexdigest())
        self._emit(ledger, seq, run_id, attempt.attempt_id, bundle.digest,
                   "aop-runner", EventAuthority.PLATFORM_OBSERVATION,
                   "skill.catalog_exposed",
                   {"skills": [e.skill_id for e in skill_entries],
                    "catalog_digest": catalog_digest,
                    "note": "exposure is not selection, loading, or execution"},
                   trace_id=tracer.trace_id, span_id=wf.span_id,
                   model_digest=model_digest,
                   harness_digest=harness_build.harness_digest,
                   payload_refs=([catalog_digest] if skill_entries else []))
        self._emit(ledger, seq, run_id, attempt.attempt_id, bundle.digest,
                   "aop-runner", EventAuthority.PLATFORM_OBSERVATION,
                   "agent.started", {"agent_id": "agent-1", "harness": harness},
                   agent_id="agent-1",
                   trace_id=tracer.trace_id, span_id=wf.span_id,
                   model_digest=model_digest,
                   harness_digest=harness_build.harness_digest)
        spool = SpoolQueue(os.path.join(self.runs_dir, "_spool"),
                           os.path.join(self.runs_dir, "_collector"))

        ctx = {"task": task, "prompt": prompt, "repo_src": repo_src, "case_dir": case_dir,
               "budget": budget, "timeout_s": timeout_s, "t0": t0,
               "deployment": deployment, "harness_build": harness_build, "bundle": bundle,
               "run_id": run_id, "run_dir": run_dir, "ledger": ledger, "seq": seq,
               "attempt": attempt, "harness": harness, "cancel": cancel,
               "cancel_file": cancel_file, "verifier_manifest": verifier_manifest,
               "verifier_error": verifier_error,
               "hidden_case_dir": hidden_case_dir, "repo_digest": repo_digest,
               "env_digest": env_digest, "model_digest": model_digest,
               "tracer": tracer, "wf": wf, "spool": spool,
               "prompt_ref": prompt_ref, "catalog_digest": catalog_digest}
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
         env_digest, model_digest, tracer, wf, spool) = tuple(ctx[k] for k in (
             "task", "prompt", "repo_src", "budget", "timeout_s", "t0", "deployment",
             "harness_build", "bundle", "run_id", "run_dir", "ledger", "seq", "attempt",
             "harness", "cancel", "cancel_file", "verifier_manifest", "verifier_error",
             "hidden_case_dir", "repo_digest", "env_digest", "model_digest",
             "tracer", "wf", "spool"))
        ident_kw = {"trace_id": tracer.trace_id, "span_id": wf.span_id,
                    "model_digest": model_digest,
                    "harness_digest": harness_build.harness_digest,
                    "repo_digest": repo_digest, "env_digest": env_digest}
        client = None

        # 1. Attest that the registered local endpoint serves this deployment.
        if harness == "pi":
            try:
                model_evidence = verify_served_model(deployment)
            except Exception as exc:  # noqa: BLE001
                raise InfraError("model_identity_mismatch", str(exc)) from exc
            self._emit(ledger, seq, run_id, attempt.attempt_id, bundle.digest,
                       "aop-runner", EventAuthority.PLATFORM_OBSERVATION,
                       "model.attested", model_evidence, **ident_kw)

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
        ws_span = tracer.start("workspace.prepare")
        self._emit(ledger, seq, run_id, attempt.attempt_id, bundle.digest,
                   "aop-runner", EventAuthority.PLATFORM_OBSERVATION,
                   "workspace.prepared", {"workspace": layout.workspace},
                   **ident_kw)
        tracer.finish(ws_span)
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
                   "harness.accepted", {"harness": harness}, **ident_kw)

        # 3. Harness execution.
        cancel_seen = _start_cancel_watch(cancel, cancel_file)
        cancel_event_emitted = False
        if cancel_seen:
            self._emit(ledger, seq, run_id, attempt.attempt_id, bundle.digest,
                       "aop-runner", EventAuthority.PLATFORM_OBSERVATION,
                       "cancel.requested", {"source": "pre-run"}, **ident_kw)
            self._emit(ledger, seq, run_id, attempt.attempt_id, bundle.digest,
                       "aop-runner", EventAuthority.PLATFORM_OBSERVATION,
                       "run.cancel_requested", {"source": "pre-run"}, **ident_kw)
            cancel_event_emitted = True
        agent_span = tracer.start("agent.execute", {"aop.harness": harness})
        infer_span = tracer.start("inference.request",
                                  {"aop.model_deployment_id": deployment.deployment_id})
        infer_rec = InferenceRecord(
            deployment_id=deployment.deployment_id,
            deployment_digest=model_digest,
            model_id=(deployment.endpoint.model_id if deployment.endpoint else ""),
            tokenizer_id="unknown-unexposed-by-pi",
            quantization=deployment.quantization or "unknown",
            server_build=deployment.server_build or "unknown",
            context_limit=deployment.context_length_configured or None)
        self._emit(ledger, seq, run_id, attempt.attempt_id, bundle.digest,
                   "aop-runner", EventAuthority.PLATFORM_OBSERVATION,
                   "inference.request", infer_rec.to_attributes(),
                   trace_id=tracer.trace_id, span_id=infer_span.span_id,
                   parent_span_id=agent_span.span_id, **{k: v for k, v in ident_kw.items()
                                                         if k not in ("trace_id", "span_id")})
        tool_spans: dict[str, object] = {}
        tool_starts: dict[str, float] = {}
        first_token_seen = False
        agent_t0 = time.monotonic()

        def on_native(raw: dict) -> None:
            nonlocal first_token_seen
            if harness == "pi":
                from .harnesses.pi_adapter import native_to_trajectory_kind
                etype, auth = native_to_trajectory_kind(raw)
            else:
                etype, auth = "harness.scripted.event", "harness_observation"
            self._emit(ledger, seq, run_id, attempt.attempt_id, bundle.digest,
                       "pi-native" if harness == "pi" else "scripted",
                       EventAuthority(auth), etype, {"native_type": raw.get("type", "")},
                       **ident_kw)
            rtype = raw.get("type", "")
            native_source = "pi-native" if harness == "pi" else "scripted"
            if rtype in ("skill_selected", "skill_loaded", "skill_executed",
                         "skill_failed"):
                skill_id = str(raw.get("skillId", raw.get("skill_id", "")))
                skill_version = str(raw.get("skillVersion",
                                            raw.get("skill_version", "")))
                skill_digest = ("sha256:" + hashlib.sha256(
                    f"{skill_id}@{skill_version}".encode()).hexdigest()
                    if skill_id else "")
                skill_map = {"skill_selected": "skill.selected",
                             "skill_loaded": "skill.loaded",
                             "skill_executed": "skill.executed",
                             "skill_failed": "skill.failed"}
                if rtype == "skill_selected":
                    sspan = tracer.start("skill.select", {"aop.skill": skill_id})
                elif rtype == "skill_loaded":
                    sspan = tracer.start("skill.load", {"aop.skill": skill_id})
                else:
                    sspan = agent_span
                self._emit(ledger, seq, run_id, attempt.attempt_id, bundle.digest,
                           native_source, EventAuthority.HARNESS_OBSERVATION,
                           skill_map[rtype],
                           {"skill_id": skill_id, "skill_version": skill_version,
                            "skill_digest": skill_digest,
                            "resources": raw.get("resources", []),
                            "status": raw.get("status", "")},
                           trace_id=tracer.trace_id, span_id=sspan.span_id,
                           parent_span_id=agent_span.span_id,
                           skill_id=skill_id, skill_version=skill_version,
                           skill_digest=skill_digest,
                           **{k: v for k, v in ident_kw.items()
                               if k not in ("trace_id", "span_id")})
                if rtype in ("skill_selected", "skill_loaded"):
                    tracer.finish(sspan)
            if harness == "pi" and rtype in ("message_update", "message_end") \
                    and not first_token_seen:
                first_token_seen = True
                # TTFT is client-observed first streaming chunk, not server timing.
                self._emit(ledger, seq, run_id, attempt.attempt_id, bundle.digest,
                           "aop-runner", EventAuthority.PLATFORM_OBSERVATION,
                           "inference.first_token",
                           {"client_observed": True,
                            "note": "client-side first chunk; server prefill "
                                    "timing unavailable"},
                           trace_id=tracer.trace_id, span_id=infer_span.span_id,
                           parent_span_id=agent_span.span_id,
                           **{k: v for k, v in ident_kw.items()
                               if k not in ("trace_id", "span_id")})
            if rtype == "tool_execution_start":
                call_id = str(raw.get("toolCallId", raw.get("tool_call_id", "")))
                tool_name = str(raw.get("toolName", raw.get("tool_name", "unknown")))
                args = raw.get("args", {})
                if not isinstance(args, dict):
                    args = {"raw": str(args)}
                tool_starts[call_id] = time.time()
                tsp = tracer.start("tool.execute",
                                   {"aop.tool": tool_name, "aop.invocation": call_id})
                tool_spans[call_id] = tsp
                call = normalize_tool_call(tool_name, native_source, call_id,
                                           "agent-1", args, {},
                                           tool_starts[call_id],
                                           tool_starts[call_id])
                self._emit(ledger, seq, run_id, attempt.attempt_id, bundle.digest,
                           native_source, EventAuthority.HARNESS_OBSERVATION,
                           "tool.request",
                           {"tool_name": call.tool_name,
                            "invocation_id": call.invocation_id,
                            "args_digest": call.args_digest,
                            "args_summary": call.args_summary},
                           trace_id=tracer.trace_id, span_id=tsp.span_id,
                           parent_span_id=agent_span.span_id,
                           tool_id=tool_name, tool_version=native_source,
                           **{k: v for k, v in ident_kw.items()
                               if k not in ("trace_id", "span_id")})
                self._emit(ledger, seq, run_id, attempt.attempt_id, bundle.digest,
                           native_source, EventAuthority.HARNESS_OBSERVATION,
                           "tool.started",
                           {"tool_name": tool_name, "invocation_id": call_id},
                           trace_id=tracer.trace_id, span_id=tsp.span_id,
                           parent_span_id=agent_span.span_id,
                           tool_id=tool_name, tool_version=native_source,
                           **{k: v for k, v in ident_kw.items()
                               if k not in ("trace_id", "span_id")})
            elif rtype in ("tool_execution_end",):
                call_id = str(raw.get("toolCallId", raw.get("tool_call_id", "")))
                tool_name = str(raw.get("toolName", raw.get("tool_name", "unknown")))
                result = raw.get("result", raw.get("output", {}))
                if not isinstance(result, dict):
                    result = {"raw": str(result)[:2000]}
                start = tool_starts.get(call_id, time.time())
                tsp = tool_spans.pop(call_id, None)
                call = normalize_tool_call(tool_name, native_source, call_id,
                                           "agent-1", {}, result, start, time.time(),
                                           exit_status=str(raw.get("status", "ok")))
                payload_refs = []
                try:
                    ref = self.artifacts.put_classified(
                        json.dumps(result, sort_keys=True).encode(),
                        run_id, "internal")
                    payload_refs = [ref.digest]
                    call.artifact_refs = payload_refs
                except Exception:
                    pass
                self._emit(ledger, seq, run_id, attempt.attempt_id, bundle.digest,
                           native_source, EventAuthority.HARNESS_OBSERVATION,
                           "tool.completed",
                           {"tool_name": call.tool_name,
                            "invocation_id": call.invocation_id,
                            "result_digest": call.result_digest,
                            "result_summary": call.result_summary,
                            "exit_status": call.exit_status},
                           trace_id=tracer.trace_id,
                           span_id=(tsp.span_id if tsp else infer_span.span_id),
                           parent_span_id=agent_span.span_id,
                           tool_id=tool_name, tool_version=native_source,
                           payload_refs=payload_refs,
                           **{k: v for k, v in ident_kw.items()
                               if k not in ("trace_id", "span_id")})
                if tsp is not None:
                    tracer.finish(tsp)

        output = adapter.start(prompt, session, layout.workspace,
                               os.path.join(run_dir, "harness-stdout.log"),
                               os.path.join(run_dir, "harness-stderr.log"),
                               timeout_s, cancel, on_native_event=on_native)
        agent_dt = time.monotonic() - agent_t0
        for _tsp in list(tool_spans.values()):
            try:
                tracer.finish(_tsp, status="error")
            except Exception:
                pass
        infer_rec.duration_s = round(agent_dt, 3)
        infer_rec.finish_reason = ("completed" if output.terminal_status == "exited"
                                   else output.terminal_status)
        if output.terminal_status in ("timeout", "cancelled"):
            infer_rec.error_class = output.terminal_status
        self._emit(ledger, seq, run_id, attempt.attempt_id, bundle.digest,
                   "aop-runner", EventAuthority.PLATFORM_OBSERVATION,
                   ("inference.completed" if output.terminal_status == "exited"
                    else "inference.failed"),
                   infer_rec.to_attributes(),
                   trace_id=tracer.trace_id, span_id=infer_span.span_id,
                   parent_span_id=agent_span.span_id,
                   **{k: v for k, v in ident_kw.items()
                       if k not in ("trace_id", "span_id")})
        tracer.finish(infer_span, status="ok" if output.terminal_status == "exited"
                      else "error")
        self._emit(ledger, seq, run_id, attempt.attempt_id, bundle.digest,
                   "aop-runner", EventAuthority.PLATFORM_OBSERVATION,
                   f"harness.{output.terminal_status}", {"exit_code": output.exit_code},
                   **ident_kw)
        # M2 canonical terminal marker alongside the M1 harness.* marker.
        terminal_map = {"exited": None, "crashed": "run.infra_error",
                        "timeout": "run.timeout", "cancelled": "run.cancelled"}
        if terminal_map.get(output.terminal_status):
            self._emit(ledger, seq, run_id, attempt.attempt_id, bundle.digest,
                       "aop-runner", EventAuthority.PLATFORM_OBSERVATION,
                       terminal_map[output.terminal_status],
                       {"exit_code": output.exit_code}, **ident_kw)
        self._emit(ledger, seq, run_id, attempt.attempt_id, bundle.digest,
                   "harness-report" if harness == "pi" else "scripted",
                   EventAuthority.AGENT_CLAIM, "agent.claim",
                   {"text": output.agent_claim},
                   agent_claim_text=output.agent_claim, **ident_kw)
        self._emit(ledger, seq, run_id, attempt.attempt_id, bundle.digest,
                   "aop-runner", EventAuthority.PLATFORM_OBSERVATION,
                   "agent.completed",
                   {"agent_id": "agent-1", "agent_seconds": round(agent_dt, 3)},
                   agent_id="agent-1", **ident_kw)
        tracer.finish(agent_span)

        eval_result: EvaluationResult | None = None
        scope: dict | None = None
        verifier_ran = False
        verifier_dt = 0.0
        if output.terminal_status in ("timeout", "cancelled"):
            outcome = (RunOutcome.TIMEOUT if output.terminal_status == "timeout"
                       else RunOutcome.CANCELLED)
            verdict = Verdict.INCONCLUSIVE
            if output.terminal_status == "cancelled":
                if not cancel_event_emitted:
                    self._emit(ledger, seq, run_id, attempt.attempt_id, bundle.digest,
                               "aop-runner", EventAuthority.PLATFORM_OBSERVATION,
                               "cancel.requested", {"source": "during-run"},
                               **ident_kw)
                    self._emit(ledger, seq, run_id, attempt.attempt_id, bundle.digest,
                               "aop-runner", EventAuthority.PLATFORM_OBSERVATION,
                               "run.cancel_requested", {"source": "during-run"},
                               **ident_kw)
                    cancel_event_emitted = True
                _write_json(os.path.join(run_dir, "cancellation.json"),
                            {"requested": True, "terminal_status": "cancelled",
                             "exit_code": output.exit_code, "harness": harness})
        else:
            self.store.set_status(run_id, RunStatus.VERIFYING)
            snapshot_dir = os.path.join(run_dir, "snapshot")
            snapshot_manifest = freeze_workspace(layout.workspace, snapshot_dir)
            self._safe_artifact(json.dumps(snapshot_manifest, sort_keys=True).encode(),
                                run_id)
            snap_ref = self._artifact_ref_or_none(
                json.dumps(snapshot_manifest, sort_keys=True).encode(), run_id)
            self._emit(ledger, seq, run_id, attempt.attempt_id, bundle.digest,
                       "aop-runner", EventAuthority.PLATFORM_OBSERVATION,
                       "output.frozen", {"files": len(snapshot_manifest)},
                       payload_refs=([snap_ref.digest] if snap_ref else []),
                       **ident_kw)
            self._emit(ledger, seq, run_id, attempt.attempt_id, bundle.digest,
                       "aop-runner", EventAuthority.PLATFORM_OBSERVATION,
                       "workspace.snapshot",
                       {"files": len(snapshot_manifest),
                        "manifest_digest": (snap_ref.digest if snap_ref else "")},
                       payload_refs=([snap_ref.digest] if snap_ref else []),
                       **ident_kw)
            # Patch + frozen diff as content-addressed artifacts.
            patch_text, diff_text = _patch_and_diff(baseline_manifest,
                                                    snapshot_manifest,
                                                    layout.workspace)
            patch_ref = self._safe_artifact_ref(patch_text.encode(), run_id)
            diff_ref = self._safe_artifact_ref(diff_text.encode(), run_id)
            if patch_text.strip():
                self._emit(ledger, seq, run_id, attempt.attempt_id, bundle.digest,
                           "aop-runner", EventAuthority.PLATFORM_OBSERVATION,
                           "patch.generated",
                           {"patch_digest": patch_ref.digest if patch_ref else "",
                            "changed_files": len(snapshot_manifest)},
                           payload_refs=([patch_ref.digest] if patch_ref else []),
                           **ident_kw)
            self._emit(ledger, seq, run_id, attempt.attempt_id, bundle.digest,
                       "aop-runner", EventAuthority.PLATFORM_OBSERVATION,
                       "diff.frozen",
                       {"diff_digest": diff_ref.digest if diff_ref else "",
                        "diff_bytes": len(diff_text.encode())},
                       payload_refs=([diff_ref.digest] if diff_ref else []),
                       **ident_kw)

            scope = check_scope(baseline_manifest, snapshot_manifest,
                                task.get("allowed_paths", []),
                                task.get("protected_paths", []))
            _write_json(os.path.join(run_dir, "scope.json"), scope)
            if scope.get("violations"):
                self._emit(ledger, seq, run_id, attempt.attempt_id, bundle.digest,
                           "aop-runner", EventAuthority.PLATFORM_OBSERVATION,
                           "scope.violation", {"violations": scope["violations"]},
                           **ident_kw)
                verdict, outcome = Verdict.FAIL, RunOutcome.FAIL
                eval_result = EvaluationResult(
                    run_id=run_id, attempt_id=attempt.attempt_id,
                    verifier_version=VERIFIER_VERSION or "scope-gate",
                    verdict=verdict, outcome=outcome, agent_claim=output.agent_claim,
                    details={"scope": scope}, error_class="scope_violation")
                _write_json(os.path.join(run_dir, "evaluation.json"),
                            eval_result.model_dump(mode="json"))
                self._emit(ledger, seq, run_id, attempt.attempt_id, bundle.digest,
                           "aop-runner", EventAuthority.PLATFORM_OBSERVATION,
                           "run.failed", {"reason": "scope_violation"}, **ident_kw)
            else:
                if verifier_manifest is None:
                    raise verifier_error or VerifierSetupError(
                        "missing_verifier_fixture", f"no verifier manifest for {hidden_case_dir}")
                vspan = tracer.start("verifier.execute",
                                     {"aop.verifier": verifier_manifest["version"]})
                self._emit(ledger, seq, run_id, attempt.attempt_id, bundle.digest,
                           "verifier", EventAuthority.VERIFIER_FACT,
                           "verifier.started",
                           {"verifier_id": verifier_manifest.get("verifier_id", ""),
                            "version": verifier_manifest["version"]},
                           trace_id=tracer.trace_id, span_id=vspan.span_id,
                           parent_span_id=wf.span_id,
                           **{k: v for k, v in ident_kw.items()
                               if k not in ("trace_id", "span_id")})
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
                try:
                    ev_ref = self.artifacts.put_classified(
                        json.dumps(eval_result.model_dump(mode="json"),
                                   sort_keys=True).encode(), run_id, "internal")
                    ev_refs = [ev_ref.digest]
                except Exception:
                    ev_refs = []
                verification = (eval_result.details or {}).get("verification", {})
                outcomes = verification.get("outcomes", {}) if verification else {}
                for nodeid, result in sorted(outcomes.items()):
                    self._emit(ledger, seq, run_id, attempt.attempt_id, bundle.digest,
                               "verifier", EventAuthority.VERIFIER_FACT,
                               ("verifier.test_passed" if result == "passed"
                                else "verifier.test_failed"),
                               {"test": nodeid, "outcome": result},
                               trace_id=tracer.trace_id, span_id=vspan.span_id,
                               parent_span_id=wf.span_id,
                               **{k: v for k, v in ident_kw.items()
                                   if k not in ("trace_id", "span_id")})
                if not outcomes:
                    self._emit(ledger, seq, run_id, attempt.attempt_id, bundle.digest,
                               "verifier", EventAuthority.VERIFIER_FACT,
                               "verifier.test_collected",
                               {"collected": verification.get("collected", [])},
                               trace_id=tracer.trace_id, span_id=vspan.span_id,
                               parent_span_id=wf.span_id,
                               **{k: v for k, v in ident_kw.items()
                                   if k not in ("trace_id", "span_id")})
                self._emit(ledger, seq, run_id, attempt.attempt_id, bundle.digest,
                           "verifier", EventAuthority.VERIFIER_FACT,
                           "evaluation.recorded",
                           {"verdict": eval_result.verdict.value,
                            "error_class": eval_result.error_class,
                            "evidence_digest": eval_result.evidence_digest,
                            "verifier_seconds": round(verifier_dt, 2)},
                           trace_id=tracer.trace_id, span_id=vspan.span_id,
                           parent_span_id=wf.span_id,
                           payload_refs=ev_refs,
                           **{k: v for k, v in ident_kw.items()
                               if k not in ("trace_id", "span_id")})
                self._emit(ledger, seq, run_id, attempt.attempt_id, bundle.digest,
                           "verifier", EventAuthority.VERIFIER_FACT,
                           "verifier.completed",
                           {"verdict": eval_result.verdict.value,
                            "outcome": eval_result.outcome.value},
                           trace_id=tracer.trace_id, span_id=vspan.span_id,
                           parent_span_id=wf.span_id,
                           **{k: v for k, v in ident_kw.items()
                               if k not in ("trace_id", "span_id")})
                tracer.finish(vspan)
                verdict, outcome = eval_result.verdict, eval_result.outcome

        required = REQUIRED_PREFIX_EVENTS
        if verifier_ran:
            required = required + ("evaluation.recorded",)
        completeness = ledger.completeness(required_events=required,
                                           terminal_events=TERMINAL_EVENTS)
        outcome_key = outcome.value
        policy_comp = ledger.completeness_for_outcome(outcome_key)
        # M2 required-evidence policy decides promotion eligibility; the legacy
        # M1 completeness stays in the report for compatibility.
        if not policy_comp["complete"] and outcome in (RunOutcome.PASS, RunOutcome.FAIL):
            outcome = RunOutcome.TELEMETRY_INCOMPLETE
            self._emit(ledger, seq, run_id, attempt.attempt_id, bundle.digest,
                       "aop-runner", EventAuthority.PLATFORM_OBSERVATION,
                       "telemetry.incomplete",
                       {"missing_required": policy_comp["missing_required"],
                        "policy": policy_comp["policy"]}, **ident_kw)
        elif policy_comp["complete"] and spool.pending_count(run_id) == 0:
            pass
        gaps = policy_comp.get("gaps", {})
        if gaps:
            self._emit(ledger, seq, run_id, attempt.attempt_id, bundle.digest,
                       "aop-runner", EventAuthority.PLATFORM_OBSERVATION,
                       "telemetry.gap_detected", {"gaps": gaps}, **ident_kw)
        if outcome in (RunOutcome.PASS, RunOutcome.FAIL):
            self._emit(ledger, seq, run_id, attempt.attempt_id, bundle.digest,
                       "aop-runner", EventAuthority.PLATFORM_OBSERVATION,
                       ("run.completed" if outcome == RunOutcome.PASS
                        else "run.failed"),
                       {"outcome": outcome.value}, **ident_kw)
        self.store.finalize_attempt(run_id, attempt.attempt_id,
                                    attempt.fencing_token, outcome, output.agent_claim,
                                    error_class=(eval_result.error_class if eval_result else ""))
        total_dt = time.monotonic() - t0
        res = sample_resources()
        res.wall_s = total_dt
        res.model_s = agent_dt
        res.harness_tool_s = agent_dt
        res.verifier_s = verifier_dt
        resources = res.to_dict()
        _write_json(os.path.join(run_dir, "resources.json"), resources)
        tracer.finish(wf, status="ok" if outcome == RunOutcome.PASS else "error")
        otel_payload = tracer.export(os.path.join(run_dir, "trace-otel.json"))
        for span in otel_payload.get("spans", []):
            spool.enqueue(run_id, {"trace_id": span["trace_id"],
                                   "span": span["name"],
                                   "span_id": span["span_id"]})
        spool_report = spool.flush(run_id)
        if spool_report.get("collector") == "recovered" and \
                spool_report.get("flushed", 0):
            try:
                self._emit(ledger, seq, run_id, attempt.attempt_id, bundle.digest,
                           "aop-runner", EventAuthority.PLATFORM_OBSERVATION,
                           "telemetry.recovered", spool_report, **ident_kw)
            except Exception:
                pass
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
            "completeness_policy": policy_comp,
            "telemetry_incomplete": policy_comp.get("telemetry_incomplete", False),
            "trace_id": tracer.trace_id,
            "resources": resources,
            "spool": spool_report,
            "replay": {
                "repo_snapshot_digest": repo_digest,
                "environment_digest": env_digest,
                "model_deployment_digest": model_digest,
                "harness_digest": harness_build.harness_digest,
                "bundle_digest": bundle.digest,
                "verifier_id": task_spec.verifier_id,
                "verifier_version": task_spec.verifier_version,
                "tool_versions": ["pi-native"],
                "skill_versions": list(bundle.skill_variant_ids),
                "note": "identity metadata for future replay; stochastic model "
                        "generation is not claimed deterministic",
            },
            "run_dir": run_dir,
            "note": "verdict is verifier-observed; agent_claim is narration only",
        }
        _write_json(os.path.join(run_dir, "report.json"), report)
        _write_json(os.path.join(run_dir, "trace.json"), {
            "trace_id": tracer.trace_id,
            "spans": [s["name"] for s in otel_payload.get("spans", [])],
            "note": "client-side projection only; server prefill/decode timing "
                    "unavailable (see trace-otel.json provenance)",
            "events": len(ledger.read_all())})
        return report

    def _artifact_ref_or_none(self, data: bytes, run_id: str,
                              classification: str = "internal"):
        """Best-effort artifact write (tolerates legacy test doubles)."""
        try:
            put = getattr(self.artifacts, "put_classified", None)
            if put is not None:
                return put(data, run_id, classification)
            return self.artifacts.put(data, run_id)
        except Exception:
            return None

    # -- failure handling --------------------------------------------------
    def _safe_artifact(self, data: bytes, run_id: str) -> None:
        try:
            self.artifacts.put(data, run_id)
        except Exception as exc:  # noqa: BLE001
            raise InfraError("storage_failure", str(exc)) from exc

    def _safe_artifact_ref(self, data: bytes, run_id: str):
        try:
            return self.artifacts.put_classified(data, run_id, "internal")
        except Exception:
            return None

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


def _patch_and_diff(baseline: dict[str, str], snapshot: dict[str, str],
                    workspace: str) -> tuple[str, str]:
    """Minimal unified-ish patch + frozen diff over changed files only.

    Large blobs stay in the artifact store; the diff artifact references
    digests for files too large to inline.
    """
    import difflib
    changed = [rel for rel, digest in snapshot.items()
               if baseline.get(rel) != digest]
    patch_lines: list[str] = []
    for rel in sorted(changed):
        patch_lines.append(f"--- a/{rel}\n+++ b/{rel}\n")
        path = os.path.join(workspace, rel)
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except OSError:
            content = ""
        if len(content) > 200_000:
            digest = "sha256:" + hashlib.sha256(content.encode()).hexdigest()
            patch_lines.append(f"[blob too large for inline diff; sha256:{digest[7:15]}]\n")
            continue
        patch_lines.extend(
            difflib.unified_diff([], content.splitlines(keepends=True),
                                 fromfile=f"a/{rel}", tofile=f"b/{rel}"))
    text = "".join(patch_lines)
    return text, text


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
