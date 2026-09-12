"""M2 canonical event taxonomy (AOP-012).

Extensible: new types must contain a dot namespace and be added to
EVENT_TAXONOMY. Stored trajectories never break when new types are added
because validation is allowlist + namespaced-prefix rule, not a closed enum
in the JSON schema.
"""
from __future__ import annotations

RUN_LIFECYCLE = (
    "run.admitted",
    "run.started",
    "run.completed",
    "run.failed",
    "run.timeout",
    "run.cancel_requested",
    "run.cancelled",
    "run.infra_error",
    # M1 aliases preserved for backward compatibility (existing runs/tests).
    "cancel.requested",
    "harness.accepted",
    "harness.exited",
    "harness.crashed",
    "harness.timeout",
    "harness.cancelled",
    "workspace.prepared",
    "model.attested",
    "output.frozen",
    "scope.violation",
    "evaluation.recorded",
    "agent.claim",
)

MODEL_EVENTS = (
    "inference.request",
    "inference.first_token",
    "inference.completed",
    "inference.failed",
)

CONTEXT_EVENTS = (
    "context.prepared",
    "context.compacted",
    "context.restored",
)

SKILL_EVENTS = (
    "skill.catalog_exposed",
    "skill.considered",
    "skill.selected",
    "skill.loaded",
    "skill.executed",
    "skill.failed",
)

TOOL_EVENTS = (
    "tool.request",
    "tool.started",
    "tool.completed",
    "tool.failed",
)

FILESYSTEM_EVENTS = (
    "workspace.snapshot",
    "file.read",
    "file.write",
    "patch.generated",
    "patch.applied",
    "diff.frozen",
)

AGENT_EVENTS = (
    "agent.started",
    "agent.completed",
    "subagent.started",
    "subagent.completed",
)

VERIFIER_EVENTS = (
    "verifier.started",
    "verifier.test_collected",
    "verifier.test_passed",
    "verifier.test_failed",
    "verifier.completed",
)

TELEMETRY_EVENTS = (
    "telemetry.gap_detected",
    "telemetry.recovered",
    "telemetry.incomplete",
)

EVENT_TAXONOMY: frozenset[str] = frozenset(
    RUN_LIFECYCLE + MODEL_EVENTS + CONTEXT_EVENTS + SKILL_EVENTS + TOOL_EVENTS
    + FILESYSTEM_EVENTS + AGENT_EVENTS + VERIFIER_EVENTS + TELEMETRY_EVENTS
)

# Namespaces that external/harness-specific events may use without breaking
# stored trajectories (e.g. harness.pi.message_end, harness.scripted.event).
EXTENSIBLE_PREFIXES = ("harness.", "skill.", "tool.", "agent.", "verifier.",
                       "telemetry.", "run.", "inference.", "context.",
                       "workspace.", "file.", "patch.", "diff.", "subagent.",
                       "test.", "cancel.", "scope.", "model.", "output.",
                       "evaluation.")


def is_known_event(event_type: str) -> bool:
    if event_type in EVENT_TAXONOMY:
        return True
    return event_type.startswith(EXTENSIBLE_PREFIXES)


def assert_known_event(event_type: str) -> None:
    if not is_known_event(event_type):
        raise ValueError(f"unknown trajectory event type: {event_type!r}")
