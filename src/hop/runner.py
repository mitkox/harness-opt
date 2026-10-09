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
import sqlite3
import sys
import threading
import time
import traceback
import uuid
from dataclasses import dataclass
from functools import lru_cache, partial

import yaml  # type: ignore[import-untyped]

from .bundle import compile_bundle, harness_build_digest, model_deployment_digest
from .config import Paths, current_paths
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
from .evaluation.sealed import SealedAccessDenied, SealedIntegrityError, SealedStore
from .files import atomic_json, ensure_owner_dir, identifier
from .harnesses.broker import Broker
from .harnesses.pi_adapter import PiJsonAdapter
from .harnesses.scripted import ScriptedHarness
from .inference import verify_served_model
from .lifecycle import (
    CancellationWatch,
    ExecutedRun,
    HarnessLifecycle,
    PreparedRun,
    ProfileIdentityKw,
    RunContext,
    RunRequest,
    RunResult,
    VerifiedRun,
)
from .runstore import RunStore
from .sandbox import (
    SandboxSpec,
    assert_no_hidden_material,
    prepare_layout,
    probe_worker_network_isolation,
)
from .storage import ArtifactStore
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
from .telemetry.tracing import RunTracer, Span
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
from .envcompat import VERIFIER_ROOT_VARS, resolve_env

VERIFIER_ROOT = resolve_env(*VERIFIER_ROOT_VARS, default=os.path.join(ROOT, ".hidden"))
HIDDEN_ROOT = os.path.join(VERIFIER_ROOT, "hidden-tests")
VERIFIER_VERSION = "debug-verifier-v2"
ADAPTER_REVISION = "pi-json-adapter-v1"


def require_worker_network_isolation(harness: str, report: dict) -> dict:
    """Fail closed when the worker network cannot be proven isolated (ADR-012).

    There is deliberately no shared-network fallback for agent harnesses: an
    unenforceable boundary is terminal ``infra_error``, never a run.
    """
    if harness == "pi" and not report.get("enforced"):
        raise InfraError("worker_network_isolation_unavailable", str(report.get("reason", "")))
    return report


class SealedAdoptionError(RuntimeError):
    """A declared sealed holdout could not be resolved at the trusted boundary."""


@dataclass
class HiddenCase:
    """Where the verifier's hidden case came from, for evidence purposes."""

    hidden_dir: str
    source: str  # "sealed" | "legacy-unsealed"
    sealed_case_id: str = ""
    hidden_ref_digest: str = ""
    ledger_path: str = ""
    exposure_count: int = 0
    repeated: bool = False
    promotion_eligible: bool = True


def plan_hidden_case(
    case_id: str,
    *,
    legacy_root: str,
    sealed_root: str = "",
    index_path: str = "",
) -> tuple[str, dict]:
    """Decide the hidden-case source without reading or ledgering anything.

    Returns ``("sealed", entry)`` when a case index declares a ``hidden_ref``
    for this benchmark case, else ``("legacy-unsealed", {})``. Planning is
    side-effect free so a duplicate/idempotent submission cannot record a
    spurious holdout read.
    """
    from .evaluation.splits import default_sealed_root

    index_file = index_path or os.environ.get("HOP_CASE_PACKAGE_INDEX", "")
    if not sealed_root and not index_file:
        # No sealed store is configured: stay on the legacy path and do not
        # touch the default sealed root (which may not exist or be writable).
        return "legacy-unsealed", {}
    root = sealed_root or default_sealed_root()
    if not index_file:
        index_file = os.path.join(root, "case-index.json")
    if not os.path.isfile(index_file):
        return "legacy-unsealed", {}
    try:
        with open(index_file, encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise SealedAdoptionError(f"cannot read case index {index_file}: {exc}") from exc
    cases = payload.get("cases", payload) if isinstance(payload, dict) else {}
    entry = dict(cases.get(case_id, {})) if isinstance(cases, dict) else {}
    if entry.get("hidden_ref"):
        return "sealed", entry
    return "legacy-unsealed", {}


def materialize_sealed_case(
    entry: dict,
    run_dir: str,
    *,
    run_id: str,
    sealed_root: str = "",
) -> HiddenCase:
    """Owner-read the sealed holdout (ledgered) and stage it in the run dir.

    Any failure is a hard ``SealedAdoptionError``: a declared sealed case never
    falls back to the interim ``.hidden/`` tree.
    """
    from .evaluation.splits import default_sealed_root

    root = sealed_root or default_sealed_root()
    hidden_ref = str(entry.get("hidden_ref", ""))
    sealed_case_id = str(entry.get("case_id", ""))
    try:
        store = SealedStore(root)
        material = store.read(
            hidden_ref,
            actor=f"verifier:{run_id}",
            role="verifier",
            purpose="evaluation",
            run_id=run_id,
        )
    except (SealedAccessDenied, SealedIntegrityError, KeyError, ValueError) as exc:
        raise SealedAdoptionError(str(exc)) from exc
    destination = os.path.join(run_dir, "sealed-case")
    if os.path.isdir(destination):
        shutil.rmtree(destination)
    os.makedirs(destination, mode=0o700)
    for rel, data in sorted(material.items()):
        target = os.path.join(destination, rel)
        os.makedirs(os.path.dirname(target) or destination, mode=0o700, exist_ok=True)
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
    report = store.exposure()
    repeated = sealed_case_id in set(store.repeated_exposure())
    return HiddenCase(
        hidden_dir=destination,
        source="sealed",
        sealed_case_id=sealed_case_id,
        hidden_ref_digest="sha256:" + hashlib.sha256(hidden_ref.encode()).hexdigest()[:16],
        ledger_path=store.ledger.path,
        exposure_count=int(report.get("counts", {}).get(sealed_case_id, 0)),
        repeated=repeated,
        # A repeated holdout exposure invalidates promotion claims for the runs
        # that read it, not just for a single run reading it twice.
        promotion_eligible=store.promotion_eligible(run_id) and not repeated,
    )


class InfraError(RuntimeError):
    def __init__(self, error_class: str, message: str):
        super().__init__(message)
        self.error_class = error_class


def load_case(case_id: str, root: str = ROOT) -> tuple[dict, str, str, str]:
    identifier(case_id)
    case_dir = os.path.join(root, "benchmarks", "development", case_id)
    with open(os.path.join(case_dir, "task.yaml")) as fh:
        task = yaml.safe_load(fh)
    with open(os.path.join(case_dir, "prompt.md")) as fh:
        prompt = fh.read()
    repo_src = os.path.join(case_dir, task.get("repo_subdir", "repo"))
    return task, prompt, repo_src, case_dir


def load_deployment(alias: str, inventory_path: str | None = None) -> ModelDeployment:
    with open(inventory_path or current_paths().deployments) as fh:
        inventory = json.load(fh)
    exact = [r for r in inventory["deployments"] if r["deployment_id"] == alias]
    matches = exact or [
        r for r in inventory["deployments"] if (r.get("endpoint") or {}).get("alias") == alias
    ]
    if len(matches) > 1:
        raise ValueError(f"ambiguous deployment alias {alias!r}; use an exact deployment ID")
    if matches:
        return ModelDeployment.model_validate(matches[0])
    raise KeyError(f"no local deployment for {alias}")


def environment_digest(case_dir: str) -> str:
    h = hashlib.sha256()
    for path in (
        os.path.join(case_dir, "environment.lock.json"),
        os.path.join(ROOT, "toolchains.lock.json"),
    ):
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
    ok = (
        caps.supports_headless
        and caps.supports_streaming_events
        and caps.supports_model_selection
        and caps.supports_cancellation
    )
    executable = caps.executable_path or "pi"
    build = HarnessBuild(
        harness=HarnessName.PI,
        executable=executable,
        version=caps.build_version,
        adapter_revision=ADAPTER_REVISION,
        protocol="pi-jsonl-v3",
        status=HarnessStatus.QUALIFIED if ok else HarnessStatus.BLOCKED,
        blocked_reason="" if ok else "headless/stream/model-select/cancel probe failed",
        capabilities={
            "headless": caps.supports_headless,
            "streaming": caps.supports_streaming_events,
            "cancellation": caps.supports_cancellation,
            "model_selection": caps.supports_model_selection,
            "session_export": caps.supports_session_export,
        },
        executable_sha256=caps.executable_sha256,
        probe_digest=caps.probe_digest,
        probe_evidence=caps.probe_evidence,
    )
    build.harness_digest = harness_build_digest(build)
    return build


def check_scope(
    baseline: dict[str, str],
    snapshot: dict[str, str],
    allowed_paths: list[str],
    protected_paths: list[str],
) -> dict:
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
    return {
        "changed": changed,
        "violations": sorted(violations, key=lambda v: v["path"]),
        "enforced": bool(allowed_paths or protected_paths),
    }


class Runner:
    def __init__(
        self,
        runs_dir: str | None = None,
        store: RunStore | None = None,
        artifacts: ArtifactStore | None = None,
        registry=None,
        hop_home: str | None = None,
        paths: Paths | None = None,
    ):
        self.paths = paths or current_paths()
        self._explicit_paths = paths is not None
        runs_dir = runs_dir or str(self.paths.runs)
        self._owns_store = store is None
        self.runs_dir = runs_dir
        ensure_owner_dir(runs_dir)
        self.store = store or RunStore(os.path.join(runs_dir, "ledger.db"))
        self.artifacts = artifacts or ArtifactStore(os.path.join(runs_dir, "artifacts"))
        # M3: optional profile registry. Lazily constructed only when a profile
        # run is requested, so legacy M1/M2 callers need no registry.
        self._registry = registry
        self._hop_home = hop_home

    def close(self) -> None:
        if self._owns_store:
            self.store.close()

    def registry(self):
        if self._registry is None:
            from .profiles import open_registry

            self._registry = open_registry(self._hop_home)
        return self._registry

    def _effective_hop_home(self) -> str:
        if self._hop_home:
            return self._hop_home
        root = getattr(self.registry(), "root", "")
        if root:
            return os.path.dirname(root)
        return ""

    def _prepare_profile(
        self,
        profile_path: str,
        target: str,
        overrides: dict[str, str] | None,
        deployment: ModelDeployment | None = None,
    ) -> dict | None:
        """Resolve -> lock -> compile a source profile entirely in memory.

        Returning compiled bytes (rather than writing first) lets the execution
        bundle digest include the compiled-target digest before admission.
        """
        if not profile_path:
            return None
        from . import profiles as P

        registry = self.registry()
        profile = P.load_profile(profile_path)
        # A profile naming a model deployment is a hard identity claim: the
        # deployment must exist locally and match the run's deployment.
        if profile.model.deployment_ref:
            try:
                declared = load_deployment(profile.model.deployment_ref)
            except KeyError as exc:
                raise InfraError(
                    "unknown_model_deployment",
                    f"profile references undeclared model deployment "
                    f"{profile.model.deployment_ref!r}",
                ) from exc
            if deployment is not None and model_deployment_digest(
                declared
            ) != model_deployment_digest(deployment):
                raise InfraError(
                    "profile_model_mismatch",
                    f"profile deployment {profile.model.deployment_ref!r} does not "
                    f"match run model {deployment.deployment_id!r}",
                )
        profile, resolved, lock = P.lock_profile(
            profile_path,
            registry,
            target=target,
            overrides=overrides,
            home=self._effective_hop_home(),
        )
        artifact, files = P.compile_resolved(profile, resolved, lock, registry, target=target)
        return {
            "profile": profile,
            "resolved": resolved,
            "lock": lock,
            "artifact": artifact,
            "files": files,
            "profile_id": f"{profile.metadata.name}@{profile.metadata.version}",
            "profile_digest": resolved.profile_digest,
            "lock_digest": lock.lock_digest,
            "artifact_digest": artifact.artifact_digest,
            "target": target,
            "registry_root": registry.root,
            "skill_ids": [
                f"{c.name}@{c.version}" for c in resolved.components if c.type.value == "skill"
            ],
        }

    def _profile_refusal_report(
        self, case_id: str, task: dict, profile_path: str, exc: BaseException
    ) -> dict:
        """Durable infra_error for a profile that cannot be admitted at all.

        The profile never reaches the agent worker: a missing component, cycle,
        policy violation, or undeclared model deployment is refused here and
        recorded with a content-addressed, error-derived bundle digest.
        """
        error_class = (
            getattr(exc, "code", "")
            or getattr(exc, "error_class", "")
            or "profile_resolution_error"
        )
        payload = {
            "profile": profile_path,
            "case_id": case_id,
            "error_class": error_class,
            "type": type(exc).__name__,
        }
        digest = (
            "sha256:" + hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        )
        run_id = new_id("run")
        record, _ = self.store.admit(
            RunRecord(
                run_id=run_id,
                task_id=task.get("task_id", case_id),
                case_id=case_id,
                bundle_digest=digest,
            )
        )
        run_id = record.run_id
        run_dir = os.path.join(self.runs_dir, run_id)
        ensure_owner_dir(run_dir)
        attempt = self.store.new_attempt(
            run_id, AttemptRecord(attempt_id=new_id("att"), worker_id=new_id("worker"))
        )
        self.store.mark_infra_error(run_id, attempt.attempt_id, error_class)
        detail = {
            "error_class": error_class,
            "error_type": type(exc).__name__,
            "message": str(exc),
            "detail": getattr(exc, "detail", {}),
        }
        report = {
            "run_id": run_id,
            "case_id": case_id,
            "bundle_digest": digest,
            "outcome": RunOutcome.INFRA_ERROR.value,
            "verdict": Verdict.ERROR.value,
            "error_class": error_class,
            "run_dir": run_dir,
            "profile": {"profile_path": profile_path},
            "error": detail,
            "note": "profile refused before agent execution",
        }
        _write_json(os.path.join(run_dir, "report.json"), report)
        _write_json(os.path.join(run_dir, "error.json"), detail)
        return report

    # -- event emission ----------------------------------------------------
    def _emit(
        self,
        ledger: EventLedger,
        seq: dict[str, int],
        run_id: str,
        attempt_id: str,
        bundle_digest: str,
        source: str,
        authority: EventAuthority,
        etype: str,
        attrs: dict,
        agent_id: str = "",
        parent_agent_id: str = "",
        trace_id: str = "",
        span_id: str = "",
        parent_span_id: str = "",
        model_digest: str = "",
        harness_digest: str = "",
        repo_digest: str = "",
        env_digest: str = "",
        skill_id: str = "",
        skill_version: str = "",
        skill_digest: str = "",
        tool_id: str = "",
        tool_version: str = "",
        profile_id: str = "",
        profile_digest: str = "",
        lock_digest: str = "",
        compiled_target: str = "",
        compiled_target_digest: str = "",
        classification: str = "internal",
        payload_refs: list[str] | None = None,
        agent_claim_text: str = "",
    ) -> None:
        """M2 envelope emission. Redacts attributes; never inlines secrets."""
        assert_known_event(etype)
        now = utcnow().isoformat()
        seq[source] = seq.get(source, -1) + 1
        clean = sanitize_attributes(attrs)
        event = TrajectoryEvent(
            event_id=str(uuid.uuid4()),
            event_type=etype,
            run_id=run_id,
            attempt_id=attempt_id,
            agent_id=agent_id,
            parent_agent_id=parent_agent_id,
            source=EventSource(id=source, authority=authority),
            source_sequence=seq[source],
            event_timestamp=now,
            observed_at=now,
            trace_id=trace_id or "0" * 32,
            span_id=span_id or "0" * 16,
            parent_span_id=parent_span_id,
            model_deployment_digest=model_digest,
            harness_digest=harness_digest,
            bundle_digest=bundle_digest,
            repo_snapshot_digest=repo_digest,
            environment_digest=env_digest,
            profile_id=profile_id,
            profile_digest=profile_digest,
            lock_digest=lock_digest,
            compiled_target=compiled_target,
            compiled_target_digest=compiled_target_digest,
            skill_id=skill_id,
            skill_version=skill_version,
            skill_digest=skill_digest,
            tool_id=tool_id,
            tool_version=tool_version,
            data_classification=classification,
            payload_refs=list(payload_refs or []),
            attributes=clean,
        )
        if agent_claim_text and authority == EventAuthority.AGENT_CLAIM:
            event.attributes["claim_digest"] = (
                "sha256:" + hashlib.sha256(agent_claim_text.encode()).hexdigest()
            )
        ledger.append(event)

    # -- admission ---------------------------------------------------------
    def _replay(self, record: RunRecord) -> dict:
        run_dir = os.path.join(self.runs_dir, record.run_id)
        report_path = os.path.join(run_dir, "report.json")
        if os.path.exists(report_path):
            with open(report_path) as fh:
                report = json.load(fh)
        else:
            report = {
                "run_id": record.run_id,
                "case_id": record.case_id,
                "bundle_digest": record.bundle_digest,
                "outcome": (
                    record.attempts[-1].outcome.value
                    if record.attempts and record.attempts[-1].outcome
                    else record.status.value
                ),
                "verdict": "inconclusive",
                "run_dir": run_dir,
            }
        report["idempotent_replay"] = True
        report["run_status"] = record.status.value
        if record.profile_id and "profile" not in report:
            report["profile"] = {
                "profile_id": record.profile_id,
                "profile_digest": record.profile_digest,
                "lock_digest": record.lock_digest,
                "compiled_target": record.compiled_target,
                "compiled_target_digest": record.compiled_target_digest,
            }
        return report

    # -- public API --------------------------------------------------------
    def execute(
        self,
        case_id: str,
        model_alias: str = "",
        harness: str = "pi",
        timeout_s: float | None = None,
        cancel: threading.Event | None = None,
        idempotency_key: str = "",
        cancel_file: str | None = None,
        skill_ids: list[str] | None = None,
        profile: str = "",
        profile_target: str = "pi",
        variant_overrides: dict[str, str] | None = None,
    ) -> dict:
        cancel = cancel or threading.Event()
        task, prompt, repo_src, case_dir = load_case(
            case_id, str(self.paths.workspace) if self._explicit_paths else ROOT
        )
        budget = float(task.get("time_budget_s", 600))
        timeout_s = budget if timeout_s is None else min(timeout_s, budget)
        t0 = time.monotonic()

        deployment = load_deployment(model_alias, str(self.paths.deployments))
        harness_build = qualify_pi_for_m1()
        try:
            profile_info = self._prepare_profile(
                profile, profile_target, variant_overrides, deployment
            )
        except Exception as exc:  # profile validation/resolution/compile refusal
            if not profile:
                raise
            return self._profile_refusal_report(case_id, task, profile, exc)
        if profile_info is not None:
            skill_ids = profile_info["skill_ids"]
        bundle, _ = compile_bundle(
            deployment,
            harness_build,
            prompt,
            skill_variant_ids=skill_ids,
            profile_id=(profile_info["profile_id"] if profile_info else ""),
            profile_digest=(profile_info["profile_digest"] if profile_info else ""),
            lock_digest=(profile_info["lock_digest"] if profile_info else ""),
            compiled_target=(profile_target if profile_info else ""),
            compiled_target_digest=(profile_info["artifact_digest"] if profile_info else ""),
        )
        # The hidden case is resolved once, here: the sealed store is the only
        # source when a case index declares a hidden_ref (its read is ledgered),
        # otherwise the interim .hidden/ tree is used and reported as legacy.
        run_id = new_id("run")
        run_dir = os.path.join(self.runs_dir, run_id)
        hidden_root = (
            str(self.paths.verifier / "hidden-tests") if self._explicit_paths else HIDDEN_ROOT
        )
        verifier_error: VerifierSetupError | None = None
        hidden_case = HiddenCase(
            hidden_dir=os.path.join(hidden_root, case_id), source="legacy-unsealed"
        )
        try:
            hidden_source, sealed_entry = plan_hidden_case(
                case_id,
                legacy_root=hidden_root,
                sealed_root=os.environ.get("HOP_SEALED_ROOT", ""),
                index_path=os.environ.get("HOP_CASE_PACKAGE_INDEX", ""),
            )
        except SealedAdoptionError as exc:
            hidden_source, sealed_entry = "legacy-unsealed", {}
            verifier_error = VerifierSetupError("sealed_case_unavailable", str(exc))
        hidden_case_dir = os.path.join(hidden_root, case_id)
        if hidden_source == "sealed":
            # The sealed manifest is only available after the owner read, which
            # happens post-admission so duplicate submissions cannot ledger one.
            verifier_manifest = None
            verifier_id, verifier_version = "", ""
        elif verifier_error is None:
            try:
                verifier_manifest = load_verifier_manifest(hidden_case_dir)
                verifier_id = verifier_manifest["verifier_id"]
                verifier_version = verifier_manifest["version"]
            except VerifierSetupError as exc:
                verifier_manifest = None
                verifier_error = exc
                verifier_id, verifier_version = "", ""
        else:
            verifier_manifest = None
            verifier_id, verifier_version = "", ""

        repo_digest = "sha256:" + hash_tree(repo_src)
        env_digest = environment_digest(case_dir)
        model_digest = model_deployment_digest(deployment)
        record, created = self.store.admit(
            RunRecord(
                run_id=run_id,
                task_id=task["task_id"],
                case_id=case_id,
                bundle_digest=bundle.digest,
                idempotency_key=idempotency_key,
                repo_snapshot_digest=repo_digest,
                environment_digest=env_digest,
                verifier_id=verifier_id,
                verifier_version=verifier_version,
                model_deployment_digest=model_digest,
                harness_digest=harness_build.harness_digest,
                profile_id=(profile_info["profile_id"] if profile_info else ""),
                profile_digest=(profile_info["profile_digest"] if profile_info else ""),
                lock_digest=(profile_info["lock_digest"] if profile_info else ""),
                compiled_target=(profile_target if profile_info else ""),
                compiled_target_digest=(profile_info["artifact_digest"] if profile_info else ""),
            )
        )
        if not created:
            return self._replay(record)
        run_id = record.run_id
        run_dir = os.path.join(self.runs_dir, run_id)
        ensure_owner_dir(run_dir)
        hidden_case = HiddenCase(hidden_dir=hidden_case_dir, source=hidden_source)
        if hidden_source == "sealed":
            # Trusted boundary: the only sealed read happens here, once, for the
            # run that was actually created; it is recorded in the holdout ledger.
            try:
                hidden_case = materialize_sealed_case(
                    sealed_entry,
                    run_dir,
                    run_id=run_id,
                    sealed_root=os.environ.get("HOP_SEALED_ROOT", ""),
                )
                hidden_case_dir = hidden_case.hidden_dir
                verifier_manifest = load_verifier_manifest(hidden_case_dir)
                verifier_error = None
            except (SealedAdoptionError, VerifierSetupError) as exc:
                verifier_manifest = None
                verifier_error = VerifierSetupError("sealed_case_unavailable", str(exc))
        ledger = EventLedger(os.path.join(run_dir, "events.jsonl"))
        seq: dict[str, int] = {}
        attempt = self.store.new_attempt(
            run_id, AttemptRecord(attempt_id=new_id("att"), worker_id=new_id("worker"))
        )
        # M2: one primary run trace; trajectory events carry its IDs.
        tracer = RunTracer(run_id)
        wf = tracer.start(
            "workflow.run", {"aop.run_id": run_id, "aop.bundle_digest": bundle.digest}
        )
        # Prompt/context snapshot as content-addressed artifact (never inline).
        # Best-effort here: the snapshot-stage write later is the strict one
        # (storage failures surface as durable infra_error there).
        prompt_ref = self._artifact_ref_or_none(prompt.encode(), run_id, "confidential")
        # Skill catalog exposure (M2 records exposure separately from use).
        skill_entries = [
            SkillCatalogEntry(
                s.split("@")[0],
                s.split("@")[1] if "@" in s else "v1",
                "sha256:" + hashlib.sha256(s.encode()).hexdigest(),
                "",
            )
            for s in bundle.skill_variant_ids
        ]
        catalog_digest = (
            skill_catalog_digest(skill_entries)
            if skill_entries
            else ("sha256:" + hashlib.sha256(b"empty-catalog").hexdigest())
        )
        spool = SpoolQueue(
            os.path.join(self.runs_dir, "_spool"), os.path.join(self.runs_dir, "_collector")
        )

        ctx = RunContext(
            task=task,
            prompt=prompt,
            repo_src=repo_src,
            case_dir=case_dir,
            budget=budget,
            timeout_s=timeout_s,
            t0=t0,
            deployment=deployment,
            harness_build=harness_build,
            bundle=bundle,
            run_id=run_id,
            run_dir=run_dir,
            ledger=ledger,
            seq=seq,
            attempt=attempt,
            harness=harness,
            cancel=cancel,
            cancel_file=cancel_file,
            verifier_manifest=verifier_manifest,
            verifier_error=verifier_error,
            hidden_case_dir=hidden_case_dir,
            repo_digest=repo_digest,
            env_digest=env_digest,
            model_digest=model_digest,
            tracer=tracer,
            wf=wf,
            spool=spool,
            prompt_ref=prompt_ref,
            catalog_digest=catalog_digest,
            profile_info=profile_info,
            profile_target=profile_target,
        )
        # Hidden-case provenance is evidence, not a contract field: keep it on
        # the context so the manifest and evaluation report can record it.
        ctx.hidden_source = hidden_case.source  # type: ignore[attr-defined]
        ctx.sealed_case_id = hidden_case.sealed_case_id  # type: ignore[attr-defined]
        ctx.hidden_ref_digest = hidden_case.hidden_ref_digest  # type: ignore[attr-defined]
        ctx.holdout_ledger = hidden_case.ledger_path  # type: ignore[attr-defined]
        ctx.holdout_exposure_count = hidden_case.exposure_count  # type: ignore[attr-defined]
        ctx.holdout_repeated = hidden_case.repeated  # type: ignore[attr-defined]
        ctx.holdout_promotion_eligible = hidden_case.promotion_eligible  # type: ignore[attr-defined]
        try:
            with CancellationWatch(cancel, cancel_file):
                return self._run_inner(ctx)
        except KeyboardInterrupt as exc:
            cancel.set()
            return self._infra_error(ctx, "cancelled", exc)
        except VerifierSetupError as exc:
            return self._infra_error(ctx, exc.error_class, exc)
        except InfraError as exc:
            return self._infra_error(ctx, exc.error_class, exc)
        except BaseException as exc:  # noqa: BLE001 - every failure is terminal & recorded
            return self._infra_error(ctx, "unclassified_infra_error", exc)

    def execute_request(self, request: RunRequest) -> RunResult:
        """Typed public entry point; legacy command handlers route through it."""
        return RunResult(
            request,
            self.execute(
                request.case_id,
                model_alias=request.model_alias,
                harness=request.harness,
                timeout_s=request.timeout_s,
                idempotency_key=request.idempotency_key,
                profile=request.profile,
                profile_target=request.profile_target,
                variant_overrides=request.variant_overrides,
            ),
        )

    # -- main lifecycle ----------------------------------------------------
    def _run_inner(self, ctx: RunContext) -> dict:
        # Prefix evidence lives inside the terminal-evidence guard: a failure
        # writing the very first events must still produce a durable report
        # instead of escaping execute() without terminal evidence.
        self._emit_prefix_evidence(ctx)
        prepared = self._prepare_run(ctx)
        try:
            execution = self._execute_run(ctx, prepared)
            verification = self._verify_run(ctx, prepared, execution)
            return self._finalize_run(ctx, prepared, execution, verification)
        finally:
            close = getattr(prepared.adapter, "close", None)
            if callable(close):
                close()

    def _emit_prefix_evidence(self, ctx: RunContext) -> None:
        """Emit run.admitted/started, context.prepared, catalog and agent.started."""
        emit = self._events(ctx)
        profile_kw = ProfileIdentityKw(
            profile_id=(ctx.profile_info["profile_id"] if ctx.profile_info else ""),
            profile_digest=(ctx.profile_info["profile_digest"] if ctx.profile_info else ""),
            lock_digest=(ctx.profile_info["lock_digest"] if ctx.profile_info else ""),
            compiled_target=(ctx.profile_target if ctx.profile_info else ""),
            compiled_target_digest=(
                ctx.profile_info["artifact_digest"] if ctx.profile_info else ""
            ),
        )
        ident = {
            "task_id": ctx.task["task_id"],
            "bundle": ctx.bundle.digest,
            "model_deployment_digest": ctx.model_digest,
            "harness_digest": ctx.harness_build.harness_digest,
            "repo_snapshot_digest": ctx.repo_digest,
            "environment_digest": ctx.env_digest,
            "trace_id": ctx.tracer.trace_id,
        }
        adm = ctx.tracer.start("admission.resolve", {"aop.task_id": ctx.task["task_id"]})
        emit(
            "aop-runner",
            EventAuthority.PLATFORM_OBSERVATION,
            "run.admitted",
            ident,
            trace_id=ctx.tracer.trace_id,
            span_id=adm.span_id,
            parent_span_id=ctx.wf.span_id,
            model_digest=ctx.model_digest,
            harness_digest=ctx.harness_build.harness_digest,
            repo_digest=ctx.repo_digest,
            env_digest=ctx.env_digest,
            **profile_kw,
        )
        ctx.tracer.finish(adm)
        emit(
            "aop-runner",
            EventAuthority.PLATFORM_OBSERVATION,
            "run.started",
            {"queue_delay_s": round(time.monotonic() - ctx.t0, 3)},
            trace_id=ctx.tracer.trace_id,
            span_id=ctx.wf.span_id,
            model_digest=ctx.model_digest,
            harness_digest=ctx.harness_build.harness_digest,
            repo_digest=ctx.repo_digest,
            env_digest=ctx.env_digest,
            **profile_kw,
        )
        emit(
            "aop-runner",
            EventAuthority.PLATFORM_OBSERVATION,
            "context.prepared",
            {
                "prompt_digest": (ctx.prompt_ref.digest if ctx.prompt_ref else ""),
                "prompt_bytes": (ctx.prompt_ref.size_bytes if ctx.prompt_ref else 0),
            },
            trace_id=ctx.tracer.trace_id,
            span_id=ctx.wf.span_id,
            model_digest=ctx.model_digest,
            harness_digest=ctx.harness_build.harness_digest,
            repo_digest=ctx.repo_digest,
            env_digest=ctx.env_digest,
            payload_refs=([ctx.prompt_ref.digest] if ctx.prompt_ref else []),
            **profile_kw,
        )
        skill_entries = [s.split("@")[0] for s in ctx.bundle.skill_variant_ids]
        emit(
            "aop-runner",
            EventAuthority.PLATFORM_OBSERVATION,
            "skill.catalog_exposed",
            {
                "skills": skill_entries,
                "catalog_digest": ctx.catalog_digest,
                "note": "exposure is not selection, loading, or execution",
            },
            trace_id=ctx.tracer.trace_id,
            span_id=ctx.wf.span_id,
            model_digest=ctx.model_digest,
            harness_digest=ctx.harness_build.harness_digest,
            payload_refs=([ctx.catalog_digest] if skill_entries else []),
            **profile_kw,
        )
        emit(
            "aop-runner",
            EventAuthority.PLATFORM_OBSERVATION,
            "agent.started",
            {"agent_id": "agent-1", "harness": ctx.harness},
            agent_id="agent-1",
            trace_id=ctx.tracer.trace_id,
            span_id=ctx.wf.span_id,
            model_digest=ctx.model_digest,
            harness_digest=ctx.harness_build.harness_digest,
            **profile_kw,
        )

    def _events(self, ctx: RunContext):
        return partial(
            self._emit, ctx.ledger, ctx.seq, ctx.run_id, ctx.attempt.attempt_id, ctx.bundle.digest
        )

    def _prepare_run(self, ctx: RunContext) -> PreparedRun:
        emit = self._events(ctx)
        ident_kw = {
            "trace_id": ctx.tracer.trace_id,
            "span_id": ctx.wf.span_id,
            "model_digest": ctx.model_digest,
            "harness_digest": ctx.harness_build.harness_digest,
            "repo_digest": ctx.repo_digest,
            "env_digest": ctx.env_digest,
            "profile_id": (ctx.profile_info["profile_id"] if ctx.profile_info else ""),
            "profile_digest": (ctx.profile_info["profile_digest"] if ctx.profile_info else ""),
            "lock_digest": (ctx.profile_info["lock_digest"] if ctx.profile_info else ""),
            "compiled_target": (ctx.profile_target if ctx.profile_info else ""),
            "compiled_target_digest": (
                ctx.profile_info["artifact_digest"] if ctx.profile_info else ""
            ),
        }

        # 1. Attest that the registered local endpoint serves this deployment.
        if ctx.harness == "pi":
            try:
                model_evidence = verify_served_model(ctx.deployment)
            except Exception as exc:
                raise InfraError("model_identity_mismatch", str(exc)) from exc
            emit(
                "aop-runner",
                EventAuthority.PLATFORM_OBSERVATION,
                "model.attested",
                model_evidence,
                **ident_kw,
            )

        self.store.set_status(ctx.run_id, RunStatus.PREPARING)
        layout = prepare_layout(os.path.join(ctx.run_dir, "worker"), snapshot_src=ctx.repo_src)
        compiled_dir = ""
        if ctx.profile_info is not None:
            compiled_dir = os.path.join(ctx.run_dir, "worker", "profile")
            for rel, data in sorted(ctx.profile_info["files"].items()):
                destination = os.path.join(compiled_dir, rel)
                ensure_owner_dir(os.path.dirname(destination) or compiled_dir)
                with open(destination, "wb") as fh:
                    fh.write(data)
        hidden_markers = (
            os.listdir(ctx.hidden_case_dir) if os.path.isdir(ctx.hidden_case_dir) else []
        )
        assert_no_hidden_material(layout.workspace, hidden_markers)
        baseline_manifest = {rel: digest for rel, digest in _manifest_of(ctx.repo_src).items()}
        task_spec = TaskSpec(
            task_id=ctx.task["task_id"],
            case_id=ctx.task["case_id"],
            prompt=ctx.prompt,
            repo_snapshot_digest=ctx.repo_digest,
            environment_digest=ctx.env_digest,
            time_budget_s=ctx.budget,
            verifier_ref=ctx.task["verifier_ref"],
            verifier_id=(ctx.verifier_manifest or {}).get("verifier_id", ""),
            verifier_version=(ctx.verifier_manifest or {}).get("version", ""),
            split=ctx.task.get("split", "development"),
            allowed_paths=ctx.task.get("allowed_paths", []),
            protected_paths=ctx.task.get("protected_paths", []),
        )
        network = (
            probe_worker_network_isolation(os.path.join(ctx.run_dir, "worker", "netprobe"))
            if ctx.harness == "pi"
            else {"enforced": True, "reason": "not_required", "bwrap": None}
        )
        require_worker_network_isolation(ctx.harness, network)
        manifest = {
            "run_id": ctx.run_id,
            "attempt_id": ctx.attempt.attempt_id,
            "bundle": ctx.bundle.model_dump(mode="json"),
            "task": task_spec.model_dump(mode="json"),
            "model": ctx.deployment.model_dump(mode="json"),
            "model_deployment_digest": ctx.model_digest,
            "harness": ctx.harness_build.model_dump(mode="json"),
            "harness_digest": ctx.harness_build.harness_digest,
            "verifier": {
                "id": task_spec.verifier_id,
                "version": task_spec.verifier_version,
                "hidden_test_hash": hash_hidden_dir(ctx.hidden_case_dir)
                if os.path.isdir(ctx.hidden_case_dir)
                else "",
                # Where the holdout came from: the sealed store (ledgered) or the
                # interim .hidden/ tree, which stays readable and is reported
                # legacy/unsealed rather than silently upgraded.
                "hidden_source": getattr(ctx, "hidden_source", "legacy-unsealed"),
                "sealed_case_id": getattr(ctx, "sealed_case_id", ""),
                "hidden_ref_digest": getattr(ctx, "hidden_ref_digest", ""),
                "holdout_ledger": getattr(ctx, "holdout_ledger", ""),
                "holdout_exposure_count": getattr(ctx, "holdout_exposure_count", 0),
                "holdout_repeated": getattr(ctx, "holdout_repeated", False),
                "holdout_promotion_eligible": getattr(ctx, "holdout_promotion_eligible", True),
            },
        }
        manifest["network"] = network
        if ctx.profile_info is not None:
            manifest["profile"] = {
                "profile_id": ctx.profile_info["profile_id"],
                "profile_digest": ctx.profile_info["profile_digest"],
                "resolution_digest": ctx.profile_info["resolved"].resolution_digest,
                "lock_digest": ctx.profile_info["lock_digest"],
                "compiled_target": ctx.profile_target,
                "compiled_target_digest": ctx.profile_info["artifact_digest"],
                "registry_root": ctx.profile_info["registry_root"],
                "resolved": ctx.profile_info["resolved"].model_dump(mode="json"),
                "lock": ctx.profile_info["lock"].model_dump(mode="json"),
                "compiled_artifact": ctx.profile_info["artifact"].model_dump(mode="json"),
            }
        _write_json(os.path.join(ctx.run_dir, "manifest.json"), manifest)
        ws_span = ctx.tracer.start("workspace.prepare")
        emit(
            "aop-runner",
            EventAuthority.PLATFORM_OBSERVATION,
            "workspace.prepared",
            {"workspace": layout.workspace},
            **ident_kw,
        )
        ctx.tracer.finish(ws_span)
        self.store.set_status(ctx.run_id, RunStatus.RUNNING)

        # 2. Adapter preparation.
        adapter: HarnessLifecycle
        broker: Broker | None = None
        if ctx.harness == "pi":
            # ADR-012: the worker has no host network. Inference is reached only
            # through the per-run broker on a bind-mounted AF_UNIX socket with a
            # per-attempt bearer token; the probe above is the only authority for
            # the enforcement claim, and a failure is terminal infra_error.
            spec = SandboxSpec(
                share_network=False, ro_binds=_harness_ro_binds(ctx.harness_build.executable)
            )
            broker = Broker(
                ctx.deployment,
                ctx.run_id,
                ctx.attempt.attempt_id,
                os.path.join(ctx.run_dir, "worker", "broker"),
                timeout_s=ctx.timeout_s,
            )
            adapter = PiJsonAdapter(
                deployment=ctx.deployment,
                # The probed absolute harness path: the relay must never resolve
                # the harness through the sandbox PATH.
                executable=ctx.harness_build.executable,
                spec=spec,
                compiled_dir=compiled_dir,
                broker=broker,
            )
            broker.start()
        elif ctx.harness.startswith("scripted:"):
            adapter = ScriptedHarness(ctx.harness.split(":", 1)[1])
        else:
            raise InfraError("unknown_harness", f"unknown harness {ctx.harness}")
        try:
            session = adapter.prepare(ctx.bundle.digest, os.path.join(ctx.run_dir, "worker"), "w1")
        except Exception as exc:
            if broker is not None:
                broker.close()
            raise InfraError("adapter_prepare_failure", str(exc)) from exc
        emit(
            "aop-runner",
            EventAuthority.PLATFORM_OBSERVATION,
            "harness.accepted",
            {"harness": ctx.harness},
            **ident_kw,
        )
        return PreparedRun(layout, adapter, session, baseline_manifest, task_spec, ident_kw)

    def _execute_run(self, ctx: RunContext, prepared: PreparedRun) -> ExecutedRun:
        emit = self._events(ctx)
        cancel_seen = ctx.cancel.is_set()
        cancel_event_emitted = False
        if cancel_seen:
            emit(
                "aop-runner",
                EventAuthority.PLATFORM_OBSERVATION,
                "cancel.requested",
                {"source": "pre-run"},
                **prepared.ident_kw,
            )
            emit(
                "aop-runner",
                EventAuthority.PLATFORM_OBSERVATION,
                "run.cancel_requested",
                {"source": "pre-run"},
                **prepared.ident_kw,
            )
            cancel_event_emitted = True
        agent_span = ctx.tracer.start("agent.execute", {"aop.harness": ctx.harness})
        infer_span = ctx.tracer.start(
            "inference.request", {"aop.model_deployment_id": ctx.deployment.deployment_id}
        )
        infer_rec = InferenceRecord(
            deployment_id=ctx.deployment.deployment_id,
            deployment_digest=ctx.model_digest,
            model_id=(ctx.deployment.endpoint.model_id if ctx.deployment.endpoint else ""),
            tokenizer_id="unknown-unexposed-by-pi",
            quantization=ctx.deployment.quantization or "unknown",
            server_build=ctx.deployment.server_build or "unknown",
            context_limit=ctx.deployment.context_length_configured or None,
        )
        emit(
            "aop-runner",
            EventAuthority.PLATFORM_OBSERVATION,
            "inference.request",
            infer_rec.to_attributes(),
            trace_id=ctx.tracer.trace_id,
            span_id=infer_span.span_id,
            parent_span_id=agent_span.span_id,
            **{k: v for k, v in prepared.ident_kw.items() if k not in ("trace_id", "span_id")},
        )
        tool_spans: dict[str, Span] = {}
        tool_starts: dict[str, float] = {}
        first_token_seen = False
        agent_t0 = time.monotonic()

        def on_native(raw: dict) -> None:
            nonlocal first_token_seen
            if ctx.harness == "pi":
                from .harnesses.pi_adapter import native_to_trajectory_kind

                etype, auth = native_to_trajectory_kind(raw)
            else:
                etype, auth = "harness.scripted.event", "harness_observation"
            emit(
                "pi-native" if ctx.harness == "pi" else "scripted",
                EventAuthority(auth),
                etype,
                {"native_type": raw.get("type", "")},
                **prepared.ident_kw,
            )
            rtype = raw.get("type", "")
            native_source = "pi-native" if ctx.harness == "pi" else "scripted"
            if rtype in ("skill_selected", "skill_loaded", "skill_executed", "skill_failed"):
                skill_id = str(raw.get("skillId", raw.get("skill_id", "")))
                skill_version = str(raw.get("skillVersion", raw.get("skill_version", "")))
                skill_digest = (
                    "sha256:" + hashlib.sha256(f"{skill_id}@{skill_version}".encode()).hexdigest()
                    if skill_id
                    else ""
                )
                skill_map = {
                    "skill_selected": "skill.selected",
                    "skill_loaded": "skill.loaded",
                    "skill_executed": "skill.executed",
                    "skill_failed": "skill.failed",
                }
                if rtype == "skill_selected":
                    sspan = ctx.tracer.start("skill.select", {"aop.skill": skill_id})
                elif rtype == "skill_loaded":
                    sspan = ctx.tracer.start("skill.load", {"aop.skill": skill_id})
                else:
                    sspan = agent_span
                emit(
                    native_source,
                    EventAuthority.HARNESS_OBSERVATION,
                    skill_map[rtype],
                    {
                        "skill_id": skill_id,
                        "skill_version": skill_version,
                        "skill_digest": skill_digest,
                        "resources": raw.get("resources", []),
                        "status": raw.get("status", ""),
                    },
                    trace_id=ctx.tracer.trace_id,
                    span_id=sspan.span_id,
                    parent_span_id=agent_span.span_id,
                    skill_id=skill_id,
                    skill_version=skill_version,
                    skill_digest=skill_digest,
                    **{
                        k: v
                        for k, v in prepared.ident_kw.items()
                        if k not in ("trace_id", "span_id")
                    },
                )
                if rtype in ("skill_selected", "skill_loaded"):
                    ctx.tracer.finish(sspan)
            if (
                ctx.harness == "pi"
                and rtype in ("message_update", "message_end")
                and not first_token_seen
            ):
                first_token_seen = True
                # TTFT is client-observed first streaming chunk, not server timing.
                emit(
                    "aop-runner",
                    EventAuthority.PLATFORM_OBSERVATION,
                    "inference.first_token",
                    {
                        "client_observed": True,
                        "note": "client-side first chunk; server prefill timing unavailable",
                    },
                    trace_id=ctx.tracer.trace_id,
                    span_id=infer_span.span_id,
                    parent_span_id=agent_span.span_id,
                    **{
                        k: v
                        for k, v in prepared.ident_kw.items()
                        if k not in ("trace_id", "span_id")
                    },
                )
            if rtype == "tool_execution_start":
                call_id = str(raw.get("toolCallId", raw.get("tool_call_id", "")))
                tool_name = str(raw.get("toolName", raw.get("tool_name", "unknown")))
                args = raw.get("args", {})
                if not isinstance(args, dict):
                    args = {"raw": str(args)}
                tool_starts[call_id] = time.time()
                tsp = ctx.tracer.start(
                    "tool.execute", {"aop.tool": tool_name, "aop.invocation": call_id}
                )
                tool_spans[call_id] = tsp
                call = normalize_tool_call(
                    tool_name,
                    native_source,
                    call_id,
                    "agent-1",
                    args,
                    {},
                    tool_starts[call_id],
                    tool_starts[call_id],
                )
                emit(
                    native_source,
                    EventAuthority.HARNESS_OBSERVATION,
                    "tool.request",
                    {
                        "tool_name": call.tool_name,
                        "invocation_id": call.invocation_id,
                        "args_digest": call.args_digest,
                        "args_summary": call.args_summary,
                    },
                    trace_id=ctx.tracer.trace_id,
                    span_id=tsp.span_id,
                    parent_span_id=agent_span.span_id,
                    tool_id=tool_name,
                    tool_version=native_source,
                    **{
                        k: v
                        for k, v in prepared.ident_kw.items()
                        if k not in ("trace_id", "span_id")
                    },
                )
                emit(
                    native_source,
                    EventAuthority.HARNESS_OBSERVATION,
                    "tool.started",
                    {"tool_name": tool_name, "invocation_id": call_id},
                    trace_id=ctx.tracer.trace_id,
                    span_id=tsp.span_id,
                    parent_span_id=agent_span.span_id,
                    tool_id=tool_name,
                    tool_version=native_source,
                    **{
                        k: v
                        for k, v in prepared.ident_kw.items()
                        if k not in ("trace_id", "span_id")
                    },
                )
            elif rtype in ("tool_execution_end",):
                call_id = str(raw.get("toolCallId", raw.get("tool_call_id", "")))
                tool_name = str(raw.get("toolName", raw.get("tool_name", "unknown")))
                result = raw.get("result", raw.get("output", {}))
                if not isinstance(result, dict):
                    result = {"raw": str(result)[:2000]}
                start = tool_starts.get(call_id, time.time())
                done_span: Span | None = None
                if call_id in tool_spans:
                    done_span = tool_spans.pop(call_id)
                call = normalize_tool_call(
                    tool_name,
                    native_source,
                    call_id,
                    "agent-1",
                    {},
                    result,
                    start,
                    time.time(),
                    exit_status=str(raw.get("status", "ok")),
                )
                payload_refs = []
                try:
                    ref = self.artifacts.put_classified(
                        json.dumps(result, sort_keys=True).encode(), ctx.run_id, "internal"
                    )
                    payload_refs = [ref.digest]
                    call.artifact_refs = payload_refs
                except (OSError, ValueError) as exc:
                    raise InfraError("storage_failure", str(exc)) from exc
                emit(
                    native_source,
                    EventAuthority.HARNESS_OBSERVATION,
                    "tool.completed",
                    {
                        "tool_name": call.tool_name,
                        "invocation_id": call.invocation_id,
                        "result_digest": call.result_digest,
                        "result_summary": call.result_summary,
                        "exit_status": call.exit_status,
                    },
                    trace_id=ctx.tracer.trace_id,
                    span_id=(done_span.span_id if done_span else infer_span.span_id),
                    parent_span_id=agent_span.span_id,
                    tool_id=tool_name,
                    tool_version=native_source,
                    payload_refs=payload_refs,
                    **{
                        k: v
                        for k, v in prepared.ident_kw.items()
                        if k not in ("trace_id", "span_id")
                    },
                )
                if done_span is not None:
                    ctx.tracer.finish(done_span)

        output = prepared.adapter.start(
            ctx.prompt,
            prepared.session,
            prepared.layout.workspace,
            os.path.join(ctx.run_dir, "harness-stdout.log"),
            os.path.join(ctx.run_dir, "harness-stderr.log"),
            ctx.timeout_s,
            ctx.cancel,
            on_native_event=on_native,
        )
        agent_dt = time.monotonic() - agent_t0
        for _tsp in list(tool_spans.values()):
            ctx.tracer.finish(_tsp, status="error")
        infer_rec.duration_s = round(agent_dt, 3)
        infer_rec.finish_reason = (
            "completed" if output.terminal_status == "exited" else output.terminal_status
        )
        if output.terminal_status in ("timeout", "cancelled"):
            infer_rec.error_class = output.terminal_status
        emit(
            "aop-runner",
            EventAuthority.PLATFORM_OBSERVATION,
            ("inference.completed" if output.terminal_status == "exited" else "inference.failed"),
            infer_rec.to_attributes(),
            trace_id=ctx.tracer.trace_id,
            span_id=infer_span.span_id,
            parent_span_id=agent_span.span_id,
            **{k: v for k, v in prepared.ident_kw.items() if k not in ("trace_id", "span_id")},
        )
        ctx.tracer.finish(
            infer_span, status="ok" if output.terminal_status == "exited" else "error"
        )
        emit(
            "aop-runner",
            EventAuthority.PLATFORM_OBSERVATION,
            f"harness.{output.terminal_status}",
            {"exit_code": output.exit_code},
            **prepared.ident_kw,
        )
        # M2 canonical terminal marker alongside the M1 harness.* marker.
        terminal_map = {
            "exited": None,
            "crashed": "run.infra_error",
            "timeout": "run.timeout",
            "cancelled": "run.cancelled",
        }
        if terminal_map.get(output.terminal_status):
            emit(
                "aop-runner",
                EventAuthority.PLATFORM_OBSERVATION,
                terminal_map[output.terminal_status],
                {"exit_code": output.exit_code},
                **prepared.ident_kw,
            )
        emit(
            "harness-report" if ctx.harness == "pi" else "scripted",
            EventAuthority.AGENT_CLAIM,
            "agent.claim",
            {"text": output.agent_claim},
            agent_claim_text=output.agent_claim,
            **prepared.ident_kw,
        )
        emit(
            "aop-runner",
            EventAuthority.PLATFORM_OBSERVATION,
            "agent.completed",
            {"agent_id": "agent-1", "agent_seconds": round(agent_dt, 3)},
            agent_id="agent-1",
            **prepared.ident_kw,
        )
        ctx.tracer.finish(agent_span)
        return ExecutedRun(output, agent_dt, cancel_event_emitted)

    def _verify_run(
        self, ctx: RunContext, prepared: PreparedRun, execution: ExecutedRun
    ) -> VerifiedRun:
        emit = self._events(ctx)
        eval_result: EvaluationResult | None = None
        scope: dict | None = None
        verifier_ran = False
        verifier_dt = 0.0
        if execution.output.terminal_status in ("timeout", "cancelled"):
            outcome = (
                RunOutcome.TIMEOUT
                if execution.output.terminal_status == "timeout"
                else RunOutcome.CANCELLED
            )
            verdict = Verdict.INCONCLUSIVE
            if execution.output.terminal_status == "cancelled":
                if not execution.cancel_event_emitted:
                    emit(
                        "aop-runner",
                        EventAuthority.PLATFORM_OBSERVATION,
                        "cancel.requested",
                        {"source": "during-run"},
                        **prepared.ident_kw,
                    )
                    emit(
                        "aop-runner",
                        EventAuthority.PLATFORM_OBSERVATION,
                        "run.cancel_requested",
                        {"source": "during-run"},
                        **prepared.ident_kw,
                    )
                    execution.cancel_event_emitted = True
                _write_json(
                    os.path.join(ctx.run_dir, "cancellation.json"),
                    {
                        "requested": True,
                        "terminal_status": "cancelled",
                        "exit_code": execution.output.exit_code,
                        "harness": ctx.harness,
                    },
                )
        else:
            self.store.set_status(ctx.run_id, RunStatus.VERIFYING)
            snapshot_dir = os.path.join(ctx.run_dir, "snapshot")
            snapshot_manifest = freeze_workspace(prepared.layout.workspace, snapshot_dir)
            self._safe_artifact(json.dumps(snapshot_manifest, sort_keys=True).encode(), ctx.run_id)
            snap_ref = self._artifact_ref_or_none(
                json.dumps(snapshot_manifest, sort_keys=True).encode(), ctx.run_id
            )
            emit(
                "aop-runner",
                EventAuthority.PLATFORM_OBSERVATION,
                "output.frozen",
                {"files": len(snapshot_manifest)},
                payload_refs=([snap_ref.digest] if snap_ref else []),
                **prepared.ident_kw,
            )
            emit(
                "aop-runner",
                EventAuthority.PLATFORM_OBSERVATION,
                "workspace.snapshot",
                {
                    "files": len(snapshot_manifest),
                    "manifest_digest": (snap_ref.digest if snap_ref else ""),
                },
                payload_refs=([snap_ref.digest] if snap_ref else []),
                **prepared.ident_kw,
            )
            # Patch + frozen diff as content-addressed artifacts.
            patch_text, diff_text = _patch_and_diff(
                prepared.baseline_manifest, snapshot_manifest, prepared.layout.workspace
            )
            patch_ref = self._safe_artifact_ref(patch_text.encode(), ctx.run_id)
            diff_ref = self._safe_artifact_ref(diff_text.encode(), ctx.run_id)
            if patch_text.strip():
                emit(
                    "aop-runner",
                    EventAuthority.PLATFORM_OBSERVATION,
                    "patch.generated",
                    {
                        "patch_digest": patch_ref.digest if patch_ref else "",
                        "changed_files": len(snapshot_manifest),
                    },
                    payload_refs=([patch_ref.digest] if patch_ref else []),
                    **prepared.ident_kw,
                )
            emit(
                "aop-runner",
                EventAuthority.PLATFORM_OBSERVATION,
                "diff.frozen",
                {
                    "diff_digest": diff_ref.digest if diff_ref else "",
                    "diff_bytes": len(diff_text.encode()),
                },
                payload_refs=([diff_ref.digest] if diff_ref else []),
                **prepared.ident_kw,
            )

            scope = check_scope(
                prepared.baseline_manifest,
                snapshot_manifest,
                ctx.task.get("allowed_paths", []),
                ctx.task.get("protected_paths", []),
            )
            _write_json(os.path.join(ctx.run_dir, "scope.json"), scope)
            if scope.get("violations"):
                emit(
                    "aop-runner",
                    EventAuthority.PLATFORM_OBSERVATION,
                    "scope.violation",
                    {"violations": scope["violations"]},
                    **prepared.ident_kw,
                )
                verdict, outcome = Verdict.FAIL, RunOutcome.FAIL
                eval_result = EvaluationResult(
                    run_id=ctx.run_id,
                    attempt_id=ctx.attempt.attempt_id,
                    verifier_version=VERIFIER_VERSION or "scope-gate",
                    verdict=verdict,
                    outcome=outcome,
                    agent_claim=execution.output.agent_claim,
                    details={"scope": scope},
                    error_class="scope_violation",
                )
                _write_json(
                    os.path.join(ctx.run_dir, "evaluation.json"),
                    eval_result.model_dump(mode="json"),
                )
                emit(
                    "aop-runner",
                    EventAuthority.PLATFORM_OBSERVATION,
                    "run.failed",
                    {"reason": "scope_violation"},
                    **prepared.ident_kw,
                )
            else:
                if ctx.verifier_manifest is None:
                    raise ctx.verifier_error or VerifierSetupError(
                        "missing_verifier_fixture",
                        f"no verifier manifest for {ctx.hidden_case_dir}",
                    )
                vspan = ctx.tracer.start(
                    "verifier.execute", {"aop.verifier": ctx.verifier_manifest["version"]}
                )
                emit(
                    "verifier",
                    EventAuthority.VERIFIER_FACT,
                    "verifier.started",
                    {
                        "verifier_id": ctx.verifier_manifest.get("verifier_id", ""),
                        "version": ctx.verifier_manifest["version"],
                    },
                    trace_id=ctx.tracer.trace_id,
                    span_id=vspan.span_id,
                    parent_span_id=ctx.wf.span_id,
                    **{
                        k: v
                        for k, v in prepared.ident_kw.items()
                        if k not in ("trace_id", "span_id")
                    },
                )
                verifier_t0 = time.monotonic()
                eval_result = run_verification(
                    ctx.run_id,
                    ctx.attempt.attempt_id,
                    snapshot_dir,
                    ctx.hidden_case_dir,
                    verifier_version=ctx.verifier_manifest["version"],
                    timeout_s=120.0,
                    work_root=os.path.join(ctx.run_dir, "verify"),
                    manifest=ctx.verifier_manifest,
                    hidden_source=getattr(ctx, "hidden_source", ""),
                )
                verifier_ran = True
                verifier_dt = time.monotonic() - verifier_t0
                eval_result.agent_claim = execution.output.agent_claim
                if getattr(ctx, "hidden_source", "") == "sealed":
                    eval_result.details["holdout"] = {
                        "source": "sealed",
                        "sealed_case_id": getattr(ctx, "sealed_case_id", ""),
                        "hidden_ref_digest": getattr(ctx, "hidden_ref_digest", ""),
                        "ledger": getattr(ctx, "holdout_ledger", ""),
                        "exposure_count": getattr(ctx, "holdout_exposure_count", 0),
                        "repeated": getattr(ctx, "holdout_repeated", False),
                        "promotion_eligible": getattr(ctx, "holdout_promotion_eligible", True),
                    }
                _write_json(
                    os.path.join(ctx.run_dir, "evaluation.json"),
                    eval_result.model_dump(mode="json"),
                )
                try:
                    ev_ref = self.artifacts.put_classified(
                        json.dumps(eval_result.model_dump(mode="json"), sort_keys=True).encode(),
                        ctx.run_id,
                        "internal",
                    )
                    ev_refs = [ev_ref.digest]
                except (OSError, ValueError) as exc:
                    raise InfraError("storage_failure", str(exc)) from exc
                verification = (eval_result.details or {}).get("verification", {})
                outcomes = verification.get("outcomes", {}) if verification else {}
                for nodeid, result in sorted(outcomes.items()):
                    emit(
                        "verifier",
                        EventAuthority.VERIFIER_FACT,
                        ("verifier.test_passed" if result == "passed" else "verifier.test_failed"),
                        {"test": nodeid, "outcome": result},
                        trace_id=ctx.tracer.trace_id,
                        span_id=vspan.span_id,
                        parent_span_id=ctx.wf.span_id,
                        **{
                            k: v
                            for k, v in prepared.ident_kw.items()
                            if k not in ("trace_id", "span_id")
                        },
                    )
                if not outcomes:
                    emit(
                        "verifier",
                        EventAuthority.VERIFIER_FACT,
                        "verifier.test_collected",
                        {"collected": verification.get("collected", [])},
                        trace_id=ctx.tracer.trace_id,
                        span_id=vspan.span_id,
                        parent_span_id=ctx.wf.span_id,
                        **{
                            k: v
                            for k, v in prepared.ident_kw.items()
                            if k not in ("trace_id", "span_id")
                        },
                    )
                emit(
                    "verifier",
                    EventAuthority.VERIFIER_FACT,
                    "evaluation.recorded",
                    {
                        "verdict": eval_result.verdict.value,
                        "error_class": eval_result.error_class,
                        "evidence_digest": eval_result.evidence_digest,
                        "verifier_seconds": round(verifier_dt, 2),
                    },
                    trace_id=ctx.tracer.trace_id,
                    span_id=vspan.span_id,
                    parent_span_id=ctx.wf.span_id,
                    payload_refs=ev_refs,
                    **{
                        k: v
                        for k, v in prepared.ident_kw.items()
                        if k not in ("trace_id", "span_id")
                    },
                )
                emit(
                    "verifier",
                    EventAuthority.VERIFIER_FACT,
                    "verifier.completed",
                    {"verdict": eval_result.verdict.value, "outcome": eval_result.outcome.value},
                    trace_id=ctx.tracer.trace_id,
                    span_id=vspan.span_id,
                    parent_span_id=ctx.wf.span_id,
                    **{
                        k: v
                        for k, v in prepared.ident_kw.items()
                        if k not in ("trace_id", "span_id")
                    },
                )
                ctx.tracer.finish(vspan)
                verdict, outcome = eval_result.verdict, eval_result.outcome
        return VerifiedRun(eval_result, scope, verifier_ran, verifier_dt, outcome, verdict)

    def _finalize_run(
        self,
        ctx: RunContext,
        prepared: PreparedRun,
        execution: ExecutedRun,
        verification: VerifiedRun,
    ) -> dict:
        emit = self._events(ctx)
        required: tuple[str, ...] = REQUIRED_PREFIX_EVENTS
        if verification.verifier_ran:
            required = required + ("evaluation.recorded",)
        completeness = ctx.ledger.completeness(
            required_events=required, terminal_events=TERMINAL_EVENTS
        )
        outcome_key = verification.outcome.value
        policy_comp = ctx.ledger.completeness_for_outcome(outcome_key)
        # M2 required-evidence policy decides promotion eligibility; the legacy
        # M1 completeness stays in the report for compatibility.
        if not policy_comp["complete"] and verification.outcome in (
            RunOutcome.PASS,
            RunOutcome.FAIL,
        ):
            verification.outcome = RunOutcome.TELEMETRY_INCOMPLETE
            emit(
                "aop-runner",
                EventAuthority.PLATFORM_OBSERVATION,
                "telemetry.incomplete",
                {
                    "missing_required": policy_comp["missing_required"],
                    "policy": policy_comp["policy"],
                },
                **prepared.ident_kw,
            )
        elif policy_comp["complete"] and ctx.spool.pending_count(ctx.run_id) == 0:
            pass
        gaps = policy_comp.get("gaps", {})
        if gaps:
            emit(
                "aop-runner",
                EventAuthority.PLATFORM_OBSERVATION,
                "telemetry.gap_detected",
                {"gaps": gaps},
                **prepared.ident_kw,
            )
        if verification.outcome in (RunOutcome.PASS, RunOutcome.FAIL):
            emit(
                "aop-runner",
                EventAuthority.PLATFORM_OBSERVATION,
                ("run.completed" if verification.outcome == RunOutcome.PASS else "run.failed"),
                {"outcome": verification.outcome.value},
                **prepared.ident_kw,
            )
        self.store.finalize_attempt(
            ctx.run_id,
            ctx.attempt.attempt_id,
            ctx.attempt.fencing_token,
            verification.outcome,
            execution.output.agent_claim,
            error_class=(verification.eval_result.error_class if verification.eval_result else ""),
        )
        total_dt = time.monotonic() - ctx.t0
        res = sample_resources()
        res.wall_s = total_dt
        res.model_s = execution.agent_dt
        res.harness_tool_s = execution.agent_dt
        res.verifier_s = verification.verifier_dt
        resources = res.to_dict()
        _write_json(os.path.join(ctx.run_dir, "resources.json"), resources)
        ctx.tracer.finish(
            ctx.wf, status="ok" if verification.outcome == RunOutcome.PASS else "error"
        )
        otel_payload = ctx.tracer.export(os.path.join(ctx.run_dir, "trace-otel.json"))
        for span in otel_payload.get("spans", []):
            ctx.spool.enqueue(
                ctx.run_id,
                {"trace_id": span["trace_id"], "span": span["name"], "span_id": span["span_id"]},
            )
        spool_report = ctx.spool.flush(ctx.run_id)
        if spool_report.get("collector") == "recovered" and spool_report.get("flushed", 0):
            try:
                emit(
                    "aop-runner",
                    EventAuthority.PLATFORM_OBSERVATION,
                    "telemetry.recovered",
                    spool_report,
                    **prepared.ident_kw,
                )
            except (OSError, ValueError) as exc:
                raise InfraError("telemetry_recovery_failure", str(exc)) from exc
        report = {
            "run_id": ctx.run_id,
            "attempt_id": ctx.attempt.attempt_id,
            "case_id": ctx.task["case_id"],
            "bundle_digest": ctx.bundle.digest,
            "model_deployment_id": ctx.deployment.deployment_id,
            "model_deployment_digest": ctx.model_digest,
            "harness_digest": ctx.harness_build.harness_digest,
            "harness": ctx.harness_build.model_dump(mode="json"),
            "verifier": {
                "id": prepared.task_spec.verifier_id,
                "version": prepared.task_spec.verifier_version,
            },
            "inputs": {
                "repo_snapshot_digest": ctx.repo_digest,
                "environment_digest": ctx.env_digest,
            },
            "profile": (
                {
                    "profile_id": ctx.profile_info["profile_id"],
                    "profile_digest": ctx.profile_info["profile_digest"],
                    "resolution_digest": ctx.profile_info["resolved"].resolution_digest,
                    "lock_digest": ctx.profile_info["lock_digest"],
                    "compiled_target": ctx.profile_target,
                    "compiled_target_digest": ctx.profile_info["artifact_digest"],
                    "registry_root": ctx.profile_info["registry_root"],
                }
                if ctx.profile_info
                else {}
            ),
            "outcome": verification.outcome.value,
            "verdict": verification.verdict.value,
            "error_class": (
                verification.eval_result.error_class if verification.eval_result else ""
            ),
            "agent_claim": execution.output.agent_claim,
            "agent_seconds": round(execution.agent_dt, 2),
            "total_seconds": round(total_dt, 2),
            "completeness": completeness,
            "completeness_policy": policy_comp,
            "telemetry_incomplete": policy_comp.get("telemetry_incomplete", False),
            "trace_id": ctx.tracer.trace_id,
            "resources": resources,
            "spool": spool_report,
            "replay": {
                "repo_snapshot_digest": ctx.repo_digest,
                "environment_digest": ctx.env_digest,
                "model_deployment_digest": ctx.model_digest,
                "harness_digest": ctx.harness_build.harness_digest,
                "bundle_digest": ctx.bundle.digest,
                "verifier_id": prepared.task_spec.verifier_id,
                "verifier_version": prepared.task_spec.verifier_version,
                "profile_id": (ctx.profile_info["profile_id"] if ctx.profile_info else ""),
                "profile_digest": (ctx.profile_info["profile_digest"] if ctx.profile_info else ""),
                "lock_digest": (ctx.profile_info["lock_digest"] if ctx.profile_info else ""),
                "compiled_target": (ctx.profile_target if ctx.profile_info else ""),
                "compiled_target_digest": (
                    ctx.profile_info["artifact_digest"] if ctx.profile_info else ""
                ),
                "tool_versions": ["pi-native"],
                "skill_versions": list(ctx.bundle.skill_variant_ids),
                "note": "identity metadata for future replay; stochastic model "
                "generation is not claimed deterministic",
            },
            "run_dir": ctx.run_dir,
            "note": "verdict is verifier-observed; agent_claim is narration only",
        }
        _write_json(os.path.join(ctx.run_dir, "report.json"), report)
        _write_json(
            os.path.join(ctx.run_dir, "trace.json"),
            {
                "trace_id": ctx.tracer.trace_id,
                "spans": [s["name"] for s in otel_payload.get("spans", [])],
                "note": "client-side projection only; server prefill/decode timing "
                "unavailable (see trace-otel.json provenance)",
                "events": len(ctx.ledger.read_all()),
            },
        )
        return report

    def _artifact_ref_or_none(self, data: bytes, run_id: str, classification: str = "internal"):
        """Best-effort artifact write (tolerates legacy test doubles)."""
        try:
            put = getattr(self.artifacts, "put_classified", None)
            if put is not None:
                return put(data, run_id, classification)
            return self.artifacts.put(data, run_id)
        except (OSError, ValueError):
            return None

    # -- failure handling --------------------------------------------------
    def _safe_artifact(self, data: bytes, run_id: str) -> None:
        try:
            self.artifacts.put(data, run_id)
        except Exception as exc:
            raise InfraError("storage_failure", str(exc)) from exc

    def _safe_artifact_ref(self, data: bytes, run_id: str):
        try:
            return self.artifacts.put_classified(data, run_id, "internal")
        except (OSError, ValueError):
            return None

    def _infra_error(self, ctx: RunContext, error_class: str, exc: BaseException) -> dict:
        run_id, run_dir = ctx.run_id, ctx.run_dir
        ledger, seq, attempt, bundle = ctx.ledger, ctx.seq, ctx.attempt, ctx.bundle
        emit = partial(self._emit, ledger, seq, run_id, attempt.attempt_id, bundle.digest)
        cancelled = isinstance(exc, KeyboardInterrupt)
        outcome = RunOutcome.CANCELLED if cancelled else RunOutcome.INFRA_ERROR
        detail = sanitize_attributes(
            {
                "error_class": error_class,
                "error_type": type(exc).__name__,
                "message": str(exc),
                "traceback_tail": traceback.format_exc()[-2000:],
            }
        )
        if run_dir:
            try:
                ensure_owner_dir(run_dir)
                _write_json(os.path.join(run_dir, "error.json"), detail)
            except OSError as error:
                detail["terminal_artifact_error"] = type(error).__name__
        if ledger is not None and seq is not None and attempt is not None and bundle is not None:
            try:
                emit(
                    "aop-runner",
                    EventAuthority.PLATFORM_OBSERVATION,
                    "run.cancelled" if cancelled else "run.infra_error",
                    {"error_class": error_class},
                )
            except (OSError, ValueError) as error:
                detail["terminal_event_error"] = type(error).__name__
        try:
            if cancelled:
                self.store.finalize_attempt(
                    run_id,
                    attempt.attempt_id,
                    attempt.fencing_token,
                    outcome,
                    error_class=error_class,
                )
            else:
                self.store.mark_infra_error(run_id, attempt.attempt_id, error_class)
        except (OSError, ValueError, KeyError, sqlite3.Error) as error:
            detail["terminal_store_error"] = type(error).__name__
        report = {
            "run_id": run_id,
            "case_id": ctx.task.get("case_id", ""),
            "bundle_digest": bundle.digest if bundle else "",
            "outcome": outcome.value,
            "verdict": Verdict.INCONCLUSIVE.value if cancelled else Verdict.ERROR.value,
            "error_class": error_class,
            "run_dir": run_dir,
            "error": detail,
        }
        if run_dir:
            try:
                _write_json(os.path.join(run_dir, "report.json"), report)
            except OSError as error:
                detail["terminal_report_error"] = type(error).__name__
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


def _patch_and_diff(
    baseline: dict[str, str], snapshot: dict[str, str], workspace: str
) -> tuple[str, str]:
    """Minimal unified-ish patch + frozen diff over changed files only.

    Large blobs stay in the artifact store; the diff artifact references
    digests for files too large to inline.
    """
    import difflib

    changed = [rel for rel, digest in snapshot.items() if baseline.get(rel) != digest]
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
            difflib.unified_diff(
                [], content.splitlines(keepends=True), fromfile=f"a/{rel}", tofile=f"b/{rel}"
            )
        )
    text = "".join(patch_lines)
    return text, text


def _symlink_chain(path: str) -> list[str]:
    """The path and every symlink hop it takes, in walk order."""
    chain: list[str] = []
    seen: set[str] = set()
    current = path
    for _ in range(32):
        if not current or current in seen:
            break
        seen.add(current)
        chain.append(current)
        if not os.path.islink(current):
            break
        target = os.readlink(current)
        current = (
            target
            if os.path.isabs(target)
            else os.path.normpath(os.path.join(os.path.dirname(current), target))
        )
    return chain


def _node_install_roots(executable: str) -> list[str]:
    """The outermost ``node_modules`` install roots containing the executable.

    Layout-agnostic: a mise install, a global npm prefix and a local
    ``node_modules`` all resolve to the same shape, so no home directory is
    special-cased. Nested ``node_modules`` directories are dropped when an
    outer root already covers them.
    """
    roots: list[str] = []
    for candidate in (*_symlink_chain(executable), os.path.realpath(executable)):
        current = candidate
        while current and current != "/":
            if os.path.basename(current) == "node_modules" and os.path.isdir(current):
                roots.append(current)
                break
            current = os.path.dirname(current)
    minimal: list[str] = []
    for root in sorted(set(roots), key=len):
        if not any(root == kept or root.startswith(kept + os.sep) for kept in minimal):
            minimal.append(root)
    return minimal


def _harness_ro_binds(executable: str) -> list[tuple[str, str]]:
    """Read-only host paths the pinned harness needs (itself plus its runtime).

    The harness package install root is resolved from the executable's realpath
    chain, so the bundle is reachable inside the namespace regardless of how the
    package manager laid it out; a symlinked install root (``.../latest``) is
    bound at its own path too. Node's resolved runtime is bound so the
    ``#!/usr/bin/env node`` shebang cannot fall back to an incompatible node.
    """
    binds: list[tuple[str, str]] = []
    real = os.path.realpath(executable)
    for root in _node_install_roots(executable):
        binds.append((root, root))
    node = shutil.which("node")
    if node:
        binds.append((os.path.dirname(node), os.path.dirname(node)))
        node_real = os.path.realpath(node)
        node_prefix = os.path.dirname(os.path.dirname(node_real))
        if os.path.isdir(node_prefix):
            binds.append((node_prefix, node_prefix))
    if not binds and os.path.exists(real):
        binds.append((os.path.dirname(real), os.path.dirname(real)))
    # Keep the invoked path reachable when it crosses a symlinked install root.
    for candidate in (*_symlink_chain(executable), node or ""):
        ancestor = os.path.dirname(candidate) if candidate else ""
        while ancestor and ancestor != "/" and not ancestor.startswith("/usr"):
            if os.path.islink(ancestor):
                binds.append((ancestor, ancestor))
            ancestor = os.path.dirname(ancestor)
    seen: set[str] = set()
    unique: list[tuple[str, str]] = []
    for src, dst in binds:
        if src not in seen and os.path.exists(src):
            seen.add(src)
            unique.append((src, dst))
    return unique


def _write_json(path: str, payload) -> None:
    atomic_json(path, payload)
