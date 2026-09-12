# ADR-004: Event authority taxonomy and trajectory-envelope alignment

Date: 2026-09-12.

Status: accepted.

## Context

M1 emitted `TrajectoryEvent` objects with flat `source_id` / `authority` fields
and authorities `trusted_observer`, `agent_report`, `synthetic_fixture`. The
published envelope `specs/trajectory-event.schema.json` instead nests
`source: {id, authority}` and requires a UUID `event_id`. The two had drifted:

- emitted events did not validate against the published schema;
- classifying harness/model output as `trusted_observer` overstated its
  authority, contradicting BUILD_PLAN §11 ("keep model-reported claims separate
  from trusted observer facts").

## Decision

1. The contract now matches the published envelope field-for-field:
   `TrajectoryEvent` has a nested `source`, a UUID `event_id`, and no `kind`
   field (the envelope forbids additional properties).
2. The authority enum is expanded to the four distinctions the architecture
   needs, plus the synthetic fixture marker:

   | Authority | Meaning |
   |---|---|
   | `platform_observation` | control-plane runner observed a process/state transition |
   | `harness_observation` | the harness reported a fact about its own execution |
   | `agent_claim` | model/agent narration; never evidence of task success |
   | `verifier_fact` | trusted verifier result on the frozen snapshot |
   | `synthetic_fixture` | contract fixture, not execution evidence |

   `trusted_observer` and `agent_report` are retired: they conflated platform,
   harness, and model provenance. This is an architectural correction, not a
   schema change made to let existing events pass.

## Consequences

- `native_to_trajectory_kind` maps Pi `message_*` events to `agent_claim` and
  other Pi events to `harness_observation`; the runner's own lifecycle events
  are `platform_observation`; verifier results are `verifier_fact`.
- `tests/test_runner_e2e.py::test_emitted_events_validate_against_published_schema`
  validates real emitted events against the schema with format checking.
- An authority value never implies task success; only `verifier_fact` carries a
  verdict.
