"""Typed per-run state and owned cancellation resources."""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass

from .contracts.harness import HarnessBuild
from .contracts.model import ModelDeployment
from .contracts.records import (
    ArtifactRef,
    AttemptRecord,
    EvaluationResult,
    ExecutionBundle,
    RunOutcome,
    TaskSpec,
    Verdict,
)
from .harnesses.base import HarnessAdapter, HarnessOutput, PreparedSession
from .sandbox import SandboxLayout
from .telemetry.spool import SpoolQueue
from .telemetry.tracing import RunTracer, Span
from .trajectories import EventLedger


@dataclass
class RunContext:
    task: dict
    prompt: str
    repo_src: str
    case_dir: str
    budget: float
    timeout_s: float
    t0: float
    deployment: ModelDeployment
    harness_build: HarnessBuild
    bundle: ExecutionBundle
    run_id: str
    run_dir: str
    ledger: EventLedger
    seq: dict[str, int]
    attempt: AttemptRecord
    harness: str
    cancel: threading.Event
    cancel_file: str | None
    verifier_manifest: dict | None
    verifier_error: Exception | None
    hidden_case_dir: str
    repo_digest: str
    env_digest: str
    model_digest: str
    tracer: RunTracer
    wf: Span
    spool: SpoolQueue
    prompt_ref: ArtifactRef | None
    catalog_digest: str
    profile_info: dict | None
    profile_target: str


class CancellationWatch:
    def __init__(self, cancel: threading.Event, path: str | None):
        self.cancel = cancel
        self.path = path
        self.stopped = threading.Event()
        self.thread: threading.Thread | None = None

    def __enter__(self):
        if self.path and os.path.exists(self.path):
            self.cancel.set()
        if self.path and not self.cancel.is_set():
            self.thread = threading.Thread(target=self._watch, name="hop-cancel-watch", daemon=True)
            self.thread.start()
        return self

    def _watch(self):
        while not self.stopped.wait(0.1):
            if self.cancel.is_set():
                return
            if self.path and os.path.exists(self.path):
                self.cancel.set()
                return

    def __exit__(self, *_):
        self.stopped.set()
        if self.thread:
            self.thread.join()


@dataclass
class PreparedRun:
    layout: SandboxLayout
    adapter: HarnessAdapter
    session: PreparedSession
    baseline_manifest: dict[str, str]
    task_spec: TaskSpec
    ident_kw: dict


@dataclass
class ExecutedRun:
    output: HarnessOutput
    agent_dt: float
    cancel_event_emitted: bool


@dataclass
class VerifiedRun:
    eval_result: EvaluationResult | None
    scope: dict | None
    verifier_ran: bool
    verifier_dt: float
    outcome: RunOutcome
    verdict: Verdict
