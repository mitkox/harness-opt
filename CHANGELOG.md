# Changelog

All notable changes to HOP are documented here. Tags: `m1-foundation`,
`m2-observability`, `hop-namespace`, `v0.1.0-alpha`, `m3-profiles`.

## v0.1.0-alpha (2026-09-12)

Public alpha. Local-only execution foundation plus trajectory observability.

Implemented (M0/M1):

- Versioned contracts for model deployments, harness builds, tasks, bundles,
  runs, and evaluation results.
- Local discovery of model deployments and harness builds
  (`hop discover`, `scripts/discover.py`).
- Pinned hash-locked dependency set with an offline wheelhouse and a
  reproducible lock-environment build (`scripts/build_lock_env.sh`).
- Pi headless adapter over the native JSONL/RPC interface; local-only
  endpoint enforcement with redirect refusal and served-model attestation.
- `bwrap`-isolated agent and verifier worker processes with scrubbed
  credentials and proxy environment.
- Immutable run manifests, durable SQLite ledger, content-addressed artifact
  store, patch/diff collection, and independent positive verification
  (machine-readable per-test report, pinned expected tests).
- Two synthetic debugging benchmarks (`debug-offbyone`, `debug-wordcount`)
  with visible and hidden tests.

Implemented (M2):

- Schema-valid trajectories with per-source sequence numbers, deduplication,
  gap detection, and outcome-aware completeness.
- Local OpenTelemetry tracing with disk spool/recovery and a Collector
  compose file for local visualization.
- Secret redaction and data classification before telemetry export.
- Investigation CLI: `hop run show|trajectory|artifacts|trace|verify|skills|
  tools|completeness|replay-check <run-id>`.
- Replay metadata and checks; historical AOP/HOP compatibility
  (`hop` CLI with deprecated `aop` alias, `HOP_*` env with `AOP_*` fallback).

Renamed AOP (Agent Optimization Platform) to HOP (Harness Optimization
Platform); canonical CLI `hop`, Python package `hop`, env prefix `HOP_*`.
See `docs/adr/ADR-008-rename-aop-to-hop.md`.

Implemented (M3):

- First-class `Profile` / `ResolvedProfile` / `Lockfile` contracts, kept
  separate from the `ExecutionBundle`.
- Immutable, content-addressed component registry (prompts, skills, agents,
  tool/context/org policies, harness overlays, hooks, MCP) with collision and
  tamper detection.
- Skill packages with canonical instructions, resources, executable scripts,
  and selector-driven model/harness variants.
- Deterministic dependency resolution, cycle/conflict detection, a lockfile
  with no floating references, and a classified policy merge where mandatory
  rules cannot be weakened.
- Deterministic profile compiler with a real Pi target and fail-closed
  extension points for Prime, OpenCode v2, and DeepSeek Harness.
- APM export with HOP provenance plus round-trip `hop profile verify-export`
  (real `apm lock` / `apm pack` exercised by the demo).
- Profile-aware runs: run records, manifests, reports, and trajectory events
  carry profile/lock/compiled digests; M1/M2 evidence stays readable.
- CLI: `hop component|skill|registry|profile` verbs (`validate`, `resolve`,
  `lock`, `deps`, `explain`, `diff`, `compile`, `materialize`, `export`,
  `verify-export`).

Not implemented (planned): M4 enterprise workflow eval packs, M5 multi-harness
support, M6 model qualification, M7 continuous optimization, M8
promotion/rollback/release, M9+ dynamic routing and lifecycle hardening.
Automatic optimization does not exist yet; HOP currently evaluates, observes,
and freezes configuration identity.
