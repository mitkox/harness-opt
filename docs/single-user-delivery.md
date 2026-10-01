# Single-User Scope Delivery

Work items: HOP-R01/R05 scope reconciliation; revised AOP-005/006/014/015,
AOP-018/019, M4-M10 dependency and acceptance requirements. Decision: ADR-011.

## Changes

- BUILD_PLAN.md, BACKLOG.yaml, AGENTS.md, README.md and the runbook now define
  one local coding user. No enterprise mode, accounts, tenants, role management,
  fleet deployment, signing service, or provider write-back is planned.
- AOP-024 standalone scanner workflows and AOP-042 provider brokers are marked
  out of scope, not complete. Active dependency paths no longer require them.
  Additional harnesses, local build fixtures and GEPA have optional dependencies.
- Active profiles use local-safety. Its network, exfiltration, telemetry and
  trajectory controls remain mandatory. The exfiltration denial is an effective
  mandatory value, not merely an override guard.
- Collector/dashboard Compose files were removed. Local trajectory, trace and
  spool behavior remains unchanged. The M2 runbook points to current instructions.
- The old enterprise-coding component and example lock moved byte-for-byte to
  tests/fixtures/legacy, outside normal component imports. Persisted schemas,
  org_policy wire identifiers, historical evidence, and identity algorithms were
  not changed. Legacy schema names do not imply enterprise functionality.

## Validation

Commands executed from the isolated worktree via rtk proxy:

| Command | Result |
|---|---|
| `scripts/dev test --suite all` | 284 passed, 1 deselected in 20.92s |
| `.venv/bin/python -m pytest tests/test_single_user_scope.py -o addopts= -q` with `PYTHONPATH=src` | 6 passed in 0.16s |
| `ruff check src tests scripts/dev scripts/measure_foundation.py` | PASS |
| `ruff format --check src tests scripts/dev scripts/measure_foundation.py` | 83 files formatted; PASS |
| `scripts/dev check` | Lint/format pass; aggregate exit 3 because approved offline mypy is unavailable |
| `git diff --check` | PASS |
| `git diff --numstat -- docs/evidence specs src` | Empty for this scope follow-up |
| Original checkout `git status --short` | Only the existing AGENTS.md edit |

New regressions check the default catalog, effective mandatory policy, rejection
of attempts to weaken that policy, active profile references, unchanged legacy
lock bytes/digest, and exclusion of removed/optional features from core gates.
Initial new-test failures exposed stale roadmap dependencies and test assumptions
about policy classes/layer names; all were corrected before the final full run.

## Limits and Next Work

This changes product scope and shipped configuration, not milestone completion.
No live model acceptance was rerun; the prior Pi qualification gap remains.
Offline type tooling, worker egress/resource isolation, verifier protection,
and independent security review still block foundation acceptance. They protect
the owner and are not enterprise features to remove.

Next work remains HOP-R02/R04 foundation closure, followed by AOP-020 and the
core local coding packs. Publishing this source branch is explicitly authorized
by the owner and is not an evaluation side effect or permission to merge/deploy.
