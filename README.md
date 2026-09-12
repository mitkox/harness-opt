# HOP — Harness Optimization Platform

Continuous evaluation, optimization, qualification, and distribution of
models, skills, prompts, tools, and agent configurations for local coding
harnesses.

HOP optimizes more than the harness binary itself. HOP owns local model
qualification, harness integration, execution bundles, trajectories and
traces, evaluations, skill/prompt/agent optimization, profile
qualification, and promotion/rollback. APM remains the downstream
package/distribution layer introduced in M3.

This repository was renamed from AOP / Agent Optimization Platform to HOP /
Harness Optimization Platform at the M2→M3 boundary (see
`docs/adr/ADR-008-rename-aop-to-hop.md`). The canonical Python package is
`hop`, the canonical CLI is `hop`, and the canonical environment prefix is
`HOP_*`. A deprecated `aop` CLI alias and `AOP_*` environment fallback are
retained until M4; persisted M0–M2 run/artifact data was not migrated and
remains readable as-is.

## Implementation status

M0/M1/M2 are implemented in `src/hop/` with the Pi headless adapter,
local-only inference, `bwrap`-isolated agent and verifier processes, a
trusted positive verifier, durable run/artifact storage, schema-valid
trajectories, scoped candidate changes, a pinned dependency lock, local
OTel tracing with spool/recovery, redaction, and outcome-aware telemetry
completeness. Evidence and the full requirement-to-probe matrix are in
`docs/evidence/m1/`, `docs/evidence/m2/`, `docs/acceptance-m1.md`, and
`docs/acceptance-m2.md`; operating instructions are in `docs/runbook-m1.md`
and `docs/runbook-m2.md`. M3 and later are not implemented.

## Quick start

```bash
PYTHONPATH=src python3 -m hop.cli discover
PYTHONPATH=src python3 -m hop.cli run --case debug-offbyone --harness pi
hop run show <run-id>
hop run trajectory <run-id>
hop run artifacts <run-id>
hop run trace <run-id>
hop run verify <run-id>
hop run skills <run-id>
hop run tools <run-id>
hop run completeness <run-id>
hop run replay-check <run-id>
```
