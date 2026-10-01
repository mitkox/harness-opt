# ADR-001: SQLite + filesystem ledger for M0/M1, PostgreSQL deferred

Scope update: ADR-011 retains SQLite permanently for the single-user product;
the historical PostgreSQL migration proposal below is no longer in scope.

Date: 2026-09-12.

BUILD_PLAN §3 recommends PostgreSQL for registry metadata, job state, and a
transactional outbox. For the M0/M1 vertical slice we use SQLite
(`runs/ledger.db`, WAL mode) plus a content-addressed filesystem artifact
store and JSONL trajectory files.

Rationale: single-host slice, zero new services, same semantics
(idempotent admission via idempotency keys, fencing tokens on attempts,
atomic stage-then-finalize artifacts). The `RunStore` interface is the seam:
a PostgreSQL implementation can replace the SQLite one without changing
adapters, verifier, or CLI.

Consequence: multi-writer concurrency and crash-safe outbox delivery beyond
single-host SQLite WAL are explicitly out of scope until M2.
