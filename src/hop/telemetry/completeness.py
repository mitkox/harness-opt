"""M2 run completeness policies by terminal state (AOP-012).

A trajectory is never complete merely because sequence numbers are
contiguous. Each terminal outcome has a required-evidence policy; missing
evidence -> telemetry_incomplete=true, usable for debugging but ineligible
for optimization/promotion evidence.
"""

from __future__ import annotations

# Each policy is a list of clauses; a clause is satisfied if ANY of its
# alternatives is present. Aliases keep M1 runs (harness.exited,
# cancel.requested, evaluation.recorded) evaluable under M2 rules.
PASS_POLICY = [
    ("run.admitted",),
    ("run.started", "harness.accepted", "workspace.prepared"),
    ("execution:activity",),  # special: any execution-activity event
    ("verifier.started",),
    ("verifier.completed", "evaluation.recorded"),
    ("run.completed", "harness.exited"),
]

FAIL_POLICY = [
    ("run.admitted",),
    ("run.started", "harness.accepted", "workspace.prepared"),
    ("execution:activity",),
    # The scope gate rejects before verification; scope.violation is the
    # evidence in that path, not missing verifier telemetry.
    ("verifier.started", "scope.violation"),
    ("verifier.completed", "evaluation.recorded", "scope.violation"),
    ("run.failed", "harness.exited", "harness.crashed"),
]

TIMEOUT_POLICY = [
    ("run.admitted",),
    ("run.started", "harness.accepted", "workspace.prepared"),
    ("run.timeout", "harness.timeout"),
]

CANCELLED_POLICY = [
    ("run.admitted",),
    ("run.started", "harness.accepted", "workspace.prepared"),
    ("run.cancel_requested", "cancel.requested"),
    ("run.cancelled", "harness.cancelled"),
]

INFRA_POLICY = [
    ("run.admitted",),
    ("run.infra_error", "harness.crashed"),
]

EXECUTION_ACTIVITY_TYPES = frozenset(
    {
        "harness.exited",
        "harness.crashed",
        "harness.timeout",
        "harness.cancelled",
        "agent.claim",
        "agent.started",
        "agent.completed",
        "inference.request",
        "inference.completed",
        "inference.first_token",
        "tool.request",
        "tool.started",
        "tool.completed",
        "file.read",
        "file.write",
        "patch.generated",
        "patch.applied",
        "workspace.snapshot",
        "output.frozen",
        "harness.pi.message_end",
        "harness.pi.message_update",
        "harness.scripted.event",
    }
)

POLICIES = {
    "pass": PASS_POLICY,
    "fail": FAIL_POLICY,
    "timeout": TIMEOUT_POLICY,
    "cancelled": CANCELLED_POLICY,
    "infra_error": INFRA_POLICY,
    "telemetry_incomplete": PASS_POLICY,
    "inconclusive": PASS_POLICY,
}


def evaluate(types: set[str], outcome: str) -> dict:
    """Evaluate required-evidence policy for a terminal outcome."""
    policy = POLICIES.get(outcome, PASS_POLICY)
    missing: list[str] = []
    for clause in policy:
        if clause == ("execution:activity",):
            if not (types & EXECUTION_ACTIVITY_TYPES):
                missing.append("execution:activity")
        elif not any(alt in types for alt in clause):
            missing.append("|".join(clause))
    return {
        "complete": not missing,
        "missing_required": missing,
        "telemetry_incomplete": bool(missing),
        "policy": outcome,
    }
