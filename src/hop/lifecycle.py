"""Typed per-run state and owned cancellation resources."""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, TypedDict

from .contracts.harness import HarnessBuild, HarnessCapabilities
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
from .harnesses.base import HarnessOutput, PreparedSession

# Uniform handler exit codes (HOP-R03). Legacy 0/1 behaviour is preserved by
# ``exit_code_for_outcome(..., legacy=True)``.
EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2
EXIT_BLOCKED = 3
EXIT_CANCELLED = 130

_OUTCOME_EXIT = {
    RunOutcome.PASS.value: EXIT_OK,
    RunOutcome.FAIL.value: EXIT_FAIL,
    RunOutcome.CANCELLED.value: EXIT_CANCELLED,
}


class LifecycleError(RuntimeError):
    """Typed handler failure: a stable diagnostic code plus its exit code.

    Raised for invalid input, blocked capability and refusal paths so every
    handler is reported through one channel instead of ad-hoc ``return 2``.
    """

    def __init__(self, code: str, message: str, exit_code: int = EXIT_USAGE) -> None:
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code


@dataclass(frozen=True)
class RunRequest:
    """Typed public run request accepted by the lifecycle owner."""

    case_id: str
    model_alias: str = ""
    harness: str = "pi"
    timeout_s: float | None = None
    idempotency_key: str = ""
    profile: str = ""
    profile_target: str = "pi"
    variant_overrides: dict[str, str] | None = None
    legacy: bool = False


@dataclass
class RunResult:
    """Typed run result: the durable report plus its uniform exit code."""

    request: RunRequest
    report: dict

    @property
    def outcome(self) -> str:
        return str(self.report.get("outcome", ""))

    @property
    def exit_code(self) -> int:
        return exit_code_for_outcome(self.outcome, legacy=self.request.legacy)


def exit_code_for_outcome(outcome: str, *, legacy: bool = False) -> int:
    """Map a terminal outcome to the uniform exit codes 0/1/3/130.

    ``legacy`` keeps the documented 0.1 ``hop run`` contract: 0 on pass, 1 on
    anything else.
    """
    if legacy:
        return EXIT_OK if outcome == RunOutcome.PASS.value else EXIT_FAIL
    return _OUTCOME_EXIT.get(outcome, EXIT_BLOCKED)


class ProfileIdentityKw(TypedDict):
    """Typed keyword set for the profile identity carried on every event."""

    profile_id: str
    profile_digest: str
    lock_digest: str
    compiled_target: str
    compiled_target_digest: str


class HarnessLifecycle(Protocol):
    """Structural lifecycle contract the runner actually invokes.

    ``harnesses.base.HarnessAdapter`` declares ``cancel: Event | None`` while
    both concrete adapters require ``Event``; this protocol matches the
    implementations so the runner can type them without a cast.
    """

    adapter_revision: str

    def probe(self) -> HarnessCapabilities: ...

    def prepare(self, bundle_digest: str, layout_root: str, identity: str) -> PreparedSession: ...

    def start(
        self,
        task_prompt: str,
        session: PreparedSession,
        workspace: str,
        stdout_path: str,
        stderr_path: str,
        timeout_s: float,
        cancel: threading.Event,
        on_native_event: Callable[[dict], None] | None = None,
    ) -> HarnessOutput: ...


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
    adapter: HarnessLifecycle
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
