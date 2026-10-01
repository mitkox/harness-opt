"""Task, bundle, run/trajectory, evaluation, artifact/tool/verifier contracts."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from .base import AopBase

# RFC-4122 textual UUID; the published trajectory schema requires format: uuid.
UUID_PATTERN = (
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


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
    verifier_id: str = Field(default="")
    verifier_version: str = Field(default="")
    split: str = Field(default="development")
    allowed_paths: list[str] = Field(default_factory=list)
    protected_paths: list[str] = Field(default_factory=list)

    def assert_no_hidden_content(self, hidden_markers: list[str], haystack: str) -> None:
        for marker in hidden_markers:
            if marker in haystack:
                raise ValueError(f"task payload leaks hidden marker: {marker!r}")


class ExecutionBundle(AopBase):
    """Immutable, content-addressed execution bundle (BUILD_PLAN §1, §4)."""

    kind: str = Field(default="ExecutionBundle", frozen=True)
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    model_deployment_id: str = Field(min_length=1)
    model_deployment_digest: str = Field(default="")
    harness: str = Field(min_length=1)
    harness_version: str = Field(min_length=1)
    harness_digest: str = Field(default="")
    adapter_revision: str = Field(min_length=1)
    base_prompt_sha256: str = Field(min_length=1)
    policy_id: str = Field(min_length=1)
    skill_variant_ids: list[str] = Field(default_factory=list)
    inference_config_digest: str = Field(default="")
    sandbox_digest: str = Field(default="")
    hardware_envelope: str = Field(default="")
    # M3: distributable agent-side identity. Empty for legacy M1/M2 runs.
    profile_id: str = Field(default="")
    profile_digest: str = Field(default="")
    lock_digest: str = Field(default="")
    compiled_target: str = Field(default="")
    compiled_target_digest: str = Field(default="")
    source_map: dict[str, str] = Field(default_factory=dict)
    deployable: bool = Field(
        default=False, description="False for draft targets with unresolved inputs"
    )


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
    INFRA_ERROR = "infra_error"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


class AttemptRecord(AopBase):
    attempt_id: str = Field(min_length=1)
    parent_attempt_id: str = Field(default="")
    worker_id: str = Field(default="")
    fencing_token: int = Field(default=0, ge=0)
    status: RunStatus = RunStatus.ADMITTED
    outcome: RunOutcome | None = None
    agent_claim: str = Field(default="", description="What the agent said; never a verdict")
    error_class: str = Field(default="")
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
    # Input identity: every run must identify the exact snapshot it evaluated.
    repo_snapshot_digest: str = Field(default="")
    environment_digest: str = Field(default="")
    verifier_id: str = Field(default="")
    verifier_version: str = Field(default="")
    model_deployment_digest: str = Field(default="")
    harness_digest: str = Field(default="")
    # M3 profile identity. Optional/empty so M1/M2 records stay readable.
    profile_id: str = Field(default="")
    profile_digest: str = Field(default="")
    lock_digest: str = Field(default="")
    compiled_target: str = Field(default="")
    compiled_target_digest: str = Field(default="")
    error_class: str = Field(default="")


class EventAuthority(str, Enum):
    """Who produced the bytes. Harness/model output is never automatically trusted.

    - platform_observation: control-plane runner observed a process/state transition
    - harness_observation: the harness itself reported a fact about its own execution
    - agent_claim: model/agent narration; never evidence of task success
    - model_output: raw model text as observed (still untrusted, distinct from claim)
    - verifier_fact: trusted verifier result on the frozen snapshot
    - synthetic_fixture: contract fixture, not execution evidence
    """

    PLATFORM_OBSERVATION = "platform_observation"
    HARNESS_OBSERVATION = "harness_observation"
    AGENT_CLAIM = "agent_claim"
    MODEL_OUTPUT = "model_output"
    VERIFIER_FACT = "verifier_fact"
    SYNTHETIC_FIXTURE = "synthetic_fixture"


class EventSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    authority: EventAuthority


class TrajectoryEvent(AopBase):
    """Durable event envelope (BUILD_PLAN §11), M2 v0.2.

    Field-for-field compatible with ``specs/trajectory-event.schema.json``
    v0.2. v0.1 events (without the M2 identity/classification fields) still
    validate: new fields carry safe defaults and migration fills them.
    Note there is deliberately no ``kind`` field: the published envelope does
    not carry one and the schema forbids additional properties.
    """

    schema_version: str = Field(default="0.2", frozen=True)
    event_id: str = Field(pattern=UUID_PATTERN)
    event_type: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    attempt_id: str = Field(min_length=1)
    agent_id: str = Field(default="")
    parent_agent_id: str = Field(default="")
    source: EventSource
    source_sequence: int = Field(ge=0)
    event_timestamp: str = Field(default="")
    observed_at: str = Field(min_length=1)
    trace_id: str = Field(default="0" * 32, pattern=r"^[0-9a-f]{32}$")
    span_id: str = Field(default="0" * 16, pattern=r"^[0-9a-f]{16}$")
    parent_span_id: str = Field(default="")
    model_deployment_digest: str = Field(default="")
    harness_digest: str = Field(default="")
    bundle_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    repo_snapshot_digest: str = Field(default="")
    environment_digest: str = Field(default="")
    # M3: distributable profile identity carried on every event (empty for
    # legacy M1/M2 events; additive, backward compatible).
    profile_id: str = Field(default="")
    profile_digest: str = Field(default="")
    lock_digest: str = Field(default="")
    compiled_target: str = Field(default="")
    compiled_target_digest: str = Field(default="")
    skill_id: str = Field(default="")
    skill_version: str = Field(default="")
    skill_digest: str = Field(default="")
    tool_id: str = Field(default="")
    tool_version: str = Field(default="")
    data_classification: str = Field(
        default="internal", pattern=r"^(public|internal|confidential|secret)$"
    )
    synthetic_fixture: bool = False
    payload_refs: list[str] = Field(default_factory=list)
    attributes: dict = Field(default_factory=dict)
    parent_event_ids: list[str] = Field(default_factory=list)

    def authority_allows_verdict(self) -> bool:
        """Only verifier_fact carries task verdicts; never infer from claims."""
        return self.source.authority.value == "verifier_fact"


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
    error_class: str = Field(default="")
    evidence_digest: str = Field(default="")


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
    expected_tests: list[str] = Field(default_factory=list)
    mandatory_tests: list[str] = Field(default_factory=list)
    minimum_test_count: int = Field(default=1, ge=0)
