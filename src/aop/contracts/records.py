"""Task, bundle, run/trajectory, evaluation, artifact/tool/verifier contracts."""

from __future__ import annotations

from enum import Enum

from pydantic import Field

from .base import AopBase


class TaskSpec(AopBase):
    """Agent-visible task package. Never contains hidden verifier content."""

    kind: str = Field(default="TaskSpec", frozen=True)
    task_id: str = Field(min_length=1)
    workflow: str = Field(default="debugging")
    case_id: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    repo_snapshot_digest: str = Field(default="")
    environment_digest: str = Field(default="")
    time_budget_s: float = Field(default=600.0, gt=0)
    verifier_ref: str = Field(min_length=1, description="Opaque reference only")
    split: str = Field(default="development")

    def assert_no_hidden_content(self, hidden_markers: list[str], haystack: str) -> None:
        for marker in hidden_markers:
            if marker in haystack:
                raise ValueError(f"task payload leaks hidden marker: {marker!r}")


class ExecutionBundle(AopBase):
    """Immutable, content-addressed execution bundle (BUILD_PLAN §1, §4)."""

    kind: str = Field(default="ExecutionBundle", frozen=True)
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    model_deployment_id: str = Field(min_length=1)
    harness: str = Field(min_length=1)
    harness_version: str = Field(min_length=1)
    adapter_revision: str = Field(min_length=1)
    base_prompt_sha256: str = Field(min_length=1)
    policy_id: str = Field(min_length=1)
    skill_variant_ids: list[str] = Field(default_factory=list)
    inference_config_digest: str = Field(default="")
    sandbox_digest: str = Field(default="")
    hardware_envelope: str = Field(default="")
    source_map: dict[str, str] = Field(default_factory=dict)
    deployable: bool = Field(default=False,
                             description="False for draft targets with unresolved inputs")


class RunOutcome(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"
    INFRA_ERROR = "infra_error"
    CANCELLED = "cancelled"
    TELEMETRY_INCOMPLETE = "telemetry_incomplete"
    TIMEOUT = "timeout"


class RunStatus(str, Enum):
    ADMITTED = "admitted"
    PREPARING = "preparing"
    RUNNING = "running"
    VERIFYING = "verifying"
    COMPLETED = "completed"


class AttemptRecord(AopBase):
    attempt_id: str = Field(min_length=1)
    parent_attempt_id: str = Field(default="")
    worker_id: str = Field(default="")
    fencing_token: int = Field(default=0, ge=0)
    status: RunStatus = RunStatus.ADMITTED
    outcome: RunOutcome | None = None
    agent_claim: str = Field(default="", description="What the agent said; never a verdict")
    idempotency_key: str = Field(default="")


class RunRecord(AopBase):
    kind: str = Field(default="RunRecord", frozen=True)
    run_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    bundle_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    status: RunStatus = RunStatus.ADMITTED
    attempts: list[AttemptRecord] = Field(default_factory=list)
    idempotency_key: str = Field(default="")


class EventAuthority(str, Enum):
    TRUSTED_OBSERVER = "trusted_observer"
    AGENT_REPORT = "agent_report"
    SYNTHETIC_FIXTURE = "synthetic_fixture"


class TrajectoryEvent(AopBase):
    """Durable event envelope (BUILD_PLAN §11). Claims vs facts via source.authority."""

    kind: str = Field(default="TrajectoryEvent", frozen=True)
    event_id: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    attempt_id: str = Field(min_length=1)
    agent_id: str = Field(default="")
    source_id: str = Field(min_length=1)
    authority: EventAuthority = EventAuthority.TRUSTED_OBSERVER
    source_sequence: int = Field(ge=0)
    observed_at: str = Field(min_length=1)
    bundle_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    trace_id: str = Field(default="", pattern=r"^[0-9a-f]{32}$")
    span_id: str = Field(default="", pattern=r"^[0-9a-f]{16}$")
    synthetic_fixture: bool = False
    payload_refs: list[str] = Field(default_factory=list)
    attributes: dict = Field(default_factory=dict)
    parent_event_ids: list[str] = Field(default_factory=list)


class Verdict(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"
    ERROR = "error"


class EvaluationResult(AopBase):
    """Trusted verifier output. The only source of the task verdict."""

    kind: str = Field(default="EvaluationResult", frozen=True)
    run_id: str = Field(min_length=1)
    attempt_id: str = Field(min_length=1)
    verifier_version: str = Field(min_length=1)
    verdict: Verdict
    outcome: RunOutcome
    agent_claim: str = Field(default="")
    details: dict = Field(default_factory=dict)
    hidden_test_hash: str = Field(default="")
    contamination_check: str = Field(default="not_checked")


class ArtifactRef(AopBase):
    kind: str = Field(default="ArtifactRef", frozen=True)
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    path: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)
    complete: bool = True


class ToolSpec(AopBase):
    kind: str = Field(default="ToolSpec", frozen=True)
    tool_id: str = Field(min_length=1)
    implementation_hash: str = Field(default="")
    allowed_in_run: bool = True


class VerifierSpec(AopBase):
    kind: str = Field(default="VerifierSpec", frozen=True)
    verifier_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    hidden_test_dir: str = Field(min_length=1)
    command: list[str] = Field(min_length=1)
