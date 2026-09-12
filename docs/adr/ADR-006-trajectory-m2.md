# ADR-006: M2 trajectory envelope v0.2 and outcome-aware completeness

Date: 2026-09-12.

Status: accepted.

## Context

M1 (ADR-004) established the v0.1 envelope and the authority taxonomy with
per-source sequences, dedup, gap detection, and required-prefix completeness.
M2 requires the full identity set (parent agent, event/observed timestamps,
parent span, model/harness/repo/environment digests, skill/tool identity,
data classification), a useful event taxonomy, tamper-evident storage, and
completeness policies keyed by terminal state.

## Decision

1. Envelope v0.2 adds the M2 identity fields as optional-with-defaults, plus
   `model_output` authority (raw model text, still untrusted) alongside
   `agent_claim`. Schema `specs/trajectory-event.schema.json` accepts both
   `0.1` and `0.2`; v0.1 events read with safe defaults and are never
   rewritten by migration (scripts/migrate_m1_to_m2.py only verifies and
   writes new `.sha256` sidecars).
2. Taxonomy (`telemetry/taxonomy.py`) pins the M2 minimum event set and
   allows namespaced extension (`harness.*`, `skill.*`, …) so new types never
   break stored trajectories. `test.*`/`cancel.*`/`scope.*`/`model.*`/
   `output.*`/`evaluation.*` prefixes preserve M1 readers and tests.
3. Ledger integrity: rolling SHA-256 chain over raw JSONL lines with an
   fsync'd sidecar; replay verifies the chain and raises visibly on tamper
   or malformed lines. M1 runs without sidecars verify as
   `m1-ledger-without-chain`, not as failures.
4. Completeness is outcome-aware (`telemetry/completeness.py`): pass/fail
   require admission, start, execution activity, verifier start/completion,
   and terminal evidence; timeout/cancelled/infra have their own policies.
   The scope gate's `scope.violation` satisfies the verifier clauses of the
   fail policy (rejection before verification is evidence, not missing
   telemetry). Any policy miss sets `telemetry_incomplete=true`: debuggable,
   ineligible for optimization/promotion.

## Consequences

- New emissions carry trace/span linkage, digests, classification, and
  artifact refs; old readers ignore unknown fields only via the v0.2 model.
- `evaluation.recorded` requires `verifier_fact`; harness/model sources
  cannot mint verifier events (ledger rejects visibly).
