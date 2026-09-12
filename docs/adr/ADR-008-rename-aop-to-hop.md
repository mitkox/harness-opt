# ADR-008: AOP → HOP rename, identifier stability and compatibility

Date: 2026-09-12.

Status: accepted.

Rename note: this ADR was written as part of the rename itself; "AOP" below
refers to the pre-rename product name, "HOP" to the post-rename one.

## Context

The project is renamed from AOP / Agent Optimization Platform to HOP /
Harness Optimization Platform (package `hop`, CLI `hop`, env prefix `HOP_*`).
M0–M2 persisted data (run records, trajectory events, artifacts,
content-addressed objects, bundle identities) must remain readable, and
historical evidence must stay valid without a data migration.

## Decision

Identifiers are classified as follows; only class A and C are renamed.
Class B (stable wire/storage identifiers) is frozen at its `aop.*` v1 form.
No `hop.*` duplicate schema is introduced (one vocabulary, no silent
rewrite of historical data).

| Persisted identifier | Class | Choice |
|---|---|---|
| Event source id `aop-runner` (events.jsonl) | B — storage | Keep emitting `aop-runner`; readers unchanged |
| Span attributes `aop.run_id`, `aop.bundle_digest`, `aop.task_id`, `aop.harness`, `aop.model_deployment_id`, `aop.skill`, `aop.tool`, `aop.invocation`, `aop.verifier` | B — wire | Keep emitting `aop.*`; no dual vocabulary |
| Event types (`run.*`, `harness.*`, `tool.*`, …) | B — storage | Product-neutral already; unchanged |
| `--aop-report` pytest flag (runner → verifier subprocess) | B — internal wire | Keep; renaming buys nothing and risks verifier skew |
| BACKLOG work-item IDs (`AOP-001` …) referenced by code docstrings, ADRs, and acceptance evidence | B — traceability | Keep; renaming would break evidence traceability |
| SQLite table `runs`, ledger `record` JSON | B — storage | No `aop` in names; no migration |
| Bundle digest (model+harness+prompt+policy+skills+inference+sandbox+hardware) | B — identity | Contains no product branding; algorithm unchanged |
| Artifact digests (`sha256:` of content bytes) | B — identity | Content-only; rename cannot alter them |
| Package/imports `hop.*`, CLI `hop`, env `HOP_*`, docs/product prose | A/C | Renamed (this change) |
| Temp-dir prefixes, thread names | C — ephemeral | Renamed where touched; never persisted |

New `hop.*` event/span identifiers may only be introduced through an
explicit schema-version bump with a tested migration — not as part of
this rename.

## Consequences

- M1/M2 runs, trajectories, artifacts, and bundle/run identities validate
  and resolve byte-identically after the rename (no migration, idempotency
  N/A, no rollback data procedure needed beyond normal backups).
- The `aop` CLI entry point (`hop.cli:legacy_main`) and `AOP_*` env fallback
  (`hop.envcompat`, `HOP_*` wins) are the only intentional legacy surfaces;
  both warn and share one implementation. Removal target: M4.
- `HOP_PI_BASE_URL` joins `AOP_PI_BASE_URL` in the worker-env scrub set;
  neither is ever honored (M1 local-only endpoint protection intact).
- Intentional remaining `aop` references are listed in the rename work
  report; everything else is an accident.
