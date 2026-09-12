# Local Agent Optimization Platform — implementation handoff

This bundle is a coding-agent plan and draft contract pack. It contains no implemented platform, installed `aop` executable, measured model rankings, or production-qualified profiles.

Start with `BUILD_PLAN.md`, then supply `AGENTS.md` to the coding agent and assign the dependency-ready items in `BACKLOG.yaml`. The first implementation target is M0/M1: one local model, Pi, an isolated debugging task, durable trajectories, and independent verification.

Contents:

- `BUILD_PLAN.md`: complete architecture, lifecycle, trust boundaries, evaluation design, observability, implementation milestones, and primary-source references.
- `AGENTS.md`: build-agent constraints and work protocol.
- `BACKLOG.yaml`: dependency-ordered implementation work items with outputs and acceptance criteria.
- `specs/`: draft JSON Schemas for skill specifications, model target declarations, and trajectory-event envelopes.
- `examples/`: non-deployable design examples that validate against those draft schemas; the target model entries deliberately have no qualified local deployments.
- `VALIDATION_REPORT.md`: structural validation performed on this handoff, not platform tests.

All schema names, `aop` interfaces, milestone gates, and example policies are project-specific proposals. Upstream runtime settings and interfaces must be probed against pinned builds. The plan is local-only across inference, evaluation, optimization, and observability.

## Implementation status

M0/M1 are implemented in `src/aop/` with the Pi headless adapter, local-only
inference, `bwrap`-isolated agent and verifier processes, a trusted positive
verifier, durable run/artifact storage, schema-valid trajectories, scoped
candidate changes, and a pinned dependency lock. Evidence and the full
requirement-to-probe matrix are in `docs/evidence/m1/` and
`docs/acceptance-m1.md`; operating instructions are in `docs/runbook-m1.md`.
M2 and later are not implemented.
