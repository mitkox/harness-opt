# ADR-003: Hidden tests live in an evaluator-owned directory outside the workspace

Date: 2026-09-12 (revised after M1 review).

## Context

BUILD_PLAN §9 requires the sealed holdout to live in a separate
evaluator-owned store, never in an agent-readable location. M1 has no evaluator
service yet, so the hidden cases live in `.hidden/hidden-tests/` in this
repository. The original ADR argued they were sealed because the runner only
copied the case `repo/` and because contamination checks and path scans passed.

That is not sufficient: same-UID processes can read any file on the
filesystem, so "outside the workspace" is not a boundary by itself.

## Decision

The seal is now enforced by namespace isolation, not by path convention:

- **Agent worker.** `bwrap` gives the Pi process a fresh mount namespace that
  binds only its own run root (plus the pinned harness/Node runtime read-only)
  and `/tmp`. `.hidden/`, the repository, and every other run's directory are
  not present, so they cannot be read or written. Writes to verifier code fail.
- **Trusted verifier.** Verification runs in its own `bwrap` namespace that
  mounts only the frozen snapshot, the single hidden case, and verifier
  bootstrap/plugin (read-only) plus a scratch output directory. Other cases
  and the repository are absent.
- **Contamination checks remain** as defence in depth: hidden filenames and
  content hashes are scanned for in every snapshot before execution.
- `.hidden/hidden-tests/<case>/verifier.json` pins the expected/mandatory test
  ids and minimum count used by positive verification (ADR-005).

## What this does not yet guarantee

Candidate code imported during verification can read the *current* case's
hidden tests (they must be mounted to run). It cannot read other cases, the
repository, or verifier code, and the candidate snapshot is already frozen, so
there is no feedback loop. Sealed evaluator-owned storage with an access ledger
and signed case manifests remains M4 (AOP-020) work.
