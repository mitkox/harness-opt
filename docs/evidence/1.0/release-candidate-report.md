# HOP 1.0.0 release candidate report

Release candidate revision: **`0123cdc29464`** (branch `esf/hop-1.0`).
Evidence pack: [RELEASE-EVIDENCE.md](RELEASE-EVIDENCE.md). Gate mapping:
[docs/release-checklist.md](../../release-checklist.md). Security sign-off:
[docs/threat-model.md](../../threat-model.md).

## Population

Single-user local coding tool: Pi harness only, local-only inference, evaluation-only
pipeline. Shipped capability is the M4 packs (AOP-020/021/022/025) plus M10
hardening (AOP-049–054) on the closed M0–M3 foundation.

**Optimization, candidate generation, activation and rollback are not part of 1.0.**
AOP-024 and AOP-042 remain out of scope under ADR-011; M5–M9 are not advanced by
this release.

## Commands that qualify the candidate

| Command | Result |
|---------|--------|
| `scripts/dev check` | exit 0, 0 findings, 73 files |
| `python -m pytest tests -m "not live"` | 551 passed, 1 deselected, exit 0 |
| `scripts/hop-gate.sh --rev 0123cdc…` | `hop-gate-20261009-203429-8` PASSED; sandbox lane 99/0/0/0; cleanup 0 |
| `hop run start --case debug-offbyone --model qwen-flash-next` | `run-58f5589ef776`, outcome/verdict pass/pass |
| offline install (sdist + wheel into a clean venv, `--no-index --require-hashes`) | executed (Part B); Part A CI unexecuted |
| `python -m pytest tests/test_deprecation_removal.py` | 12 tests collected (migration-tested deprecation removal) |

## Unexecuted / blocked items with reasons

| Item | Reason |
|------|--------|
| CI jobs (Part A of offline install) | no external CI execution on this host; deviation list recorded in `docs/verification/offline-install.md` |
| Host-side bwrap suite in the agent shell | AppArmor-confined shell cannot create namespaces; executed in the Cube gate instead |
| `scripts/dev test --suite live` green claim | reported by the Lead; not independently re-run in this pack (G8 itself is evidenced by `run-58f5589ef776`) |
| task-48 artifact qualification | in progress; not claimed (G9 open sub-item) |
| Hostile-workload qualification | not claimed: scripted adversaries only |
| Optimizer-vs-interactive scheduling | no optimizer exists in 1.0 |
| Multi-tenant / shared-host isolation | out of scope (single owner, ADR-011) |

## Authorization

Push, PR and tag are **not** performed by this pack. They are the explicit owner
action in gate G11 (task-37/39) and require the owner's authorization; the
checklist records G11 as in progress until then.
