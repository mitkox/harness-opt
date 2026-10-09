# Freeze-gate evidence at the frozen revision (WP-5.1 / WP-5.1b)

Frozen revision: **`779e73f61f5112b681242e0376ed9c78c6e2d9e1`**, declared by the
Lead (owner's delegate for this delivery). No further source or documentation
changes are planned beyond freeze-gate and handoff evidence.

Every row below names who executed it. Nothing is inferred; an unrun step is
recorded as in progress rather than given a fabricated run id.

## Runs

| Step | Run id | Executed by | Revision | Result | Conditions / cleanup |
|------|--------|-------------|----------|--------|----------------------|
| Deterministic hop-v1 gate (agent `noop`) | `freeze-hopv1-779e73f` | factory-delivery | `779e73f` | PASSED | All conditions `True` except the documented `EgressVerified=Unknown (ProbeInconclusive)`; `Verified=True`, `GateIntegrity=True`; `CleanupVerified=True`, 0 remaining sandboxes; sandbox lane 99 ran / 0 failed / 0 skipped |
| Disconnected profile (AOP-052) | `freeze-disc-779e73f` | factory-delivery | `779e73f` | PASSED | Same condition set (only `EgressVerified=Unknown`); `CleanupVerified=True`, 0 remaining sandboxes |
| Live OpenCode Go agent acceptance (ESF end-to-end) | **pending** | factory-delivery | `779e73f` | **IN PROGRESS — placeholder, not a run id** | `freeze-live-779e73f` run directory exists with `baseline.json`, `task.json`, `inventory.json`, `hardening/` but **no `run.json` yet**; this row must be replaced with the real id and condition table when factory-delivery reports |
| G8 HOP live local-model acceptance | `run-85d1c3a117ef` | Lead | `779e73f` (record rebound in `6c7bc87`) | `outcome=pass`, `verdict=pass` | `agent_seconds=44.71`; Pi 1.1.0 `qualified` (executable sha256 `c44ddd4e…`); deployment `qwen-flash-next-IQ2_S_-_2.5_bpw`; recorded in [live-model-acceptance.md](live-model-acceptance.md) |
| Host validation at the frozen sha | n/a (commands) | Lead | `779e73f` | PASS | `scripts/dev check` exit 0 (`All checks passed!`, `Success: no issues found in 74 source files`); `scripts/dev test --suite all` → `557 passed, 1 deselected`, `suite_exit=0`; log `/home/mitko/dev/esf/.factory/host-frozen.log` |

The two factory-delivery gate runs and the Lead's G8 run all report
`779e73f61f5112b681242e0376ed9c78c6e2d9e1` as their baseline, so the freeze-gate
evidence is revision-consistent.

## Release-blocking finding: artifacts predate the frozen content

Independent-verifier **R-029** found that the artifacts recorded earlier as final
predate task-51 (`456aa7b`, the last code change before the freeze):

- `dist/hop-1.0.0-py3-none-any.whl` (`729aeee9…`) has **79 entries** and does **not**
  contain `hop/evaluation/legacy.py`; the sdist `b38611e7…` also lacks it;
- a fresh build at the frozen revision has **80 entries** and includes it.

Therefore the hashes currently named in the release checklist G9 row,
`docs/release-1.0.0.md` and `dist/MANIFEST.sha256` are **not** the final content
hashes. **task-54 (foundation-runtime) is rebuilding the wheel and sdist at the
frozen revision and re-qualifying the installed artifact**; those three places must
then be updated with the new hashes and the sdist recorded as-built. This document
does not invent replacement hashes, and the live-agent row above remains a
placeholder until factory-delivery reports.

## Wheel reproducibility (independently confirmed)

R-029 confirmed the reproducibility property: with
`SOURCE_DATE_EPOCH=1700000000`, two scratch builds from `1be9a5e` are byte-identical
(`42439320855807682c7bd1b63bb0aa8d6a599d682ea0786b05a605a87644aee6`, all zip
timestamps normalised to 2023-11-14T22:13:20Z); plain rebuilds 4 s apart differ
(`d52732c4…` vs `0ab678d8…`) while **content is identical** (80 entries, every
per-entry sha256 equal). So the wheel is byte-reproducible only with
`SOURCE_DATE_EPOCH` set; the sdist digest is recorded as-built.

## Status

- Freeze gate: **PASS** for the deterministic and disconnected runs and for G8 and
  the host validation; the live OpenCode Go agent acceptance is **in progress**
  (factory-delivery) and must be recorded here before G11 can move.
- Gate **G11 remains IN PROGRESS**: push, PR and tag require the owner's explicit
  authorization. See [docs/release-checklist.md](../../release-checklist.md) and
  [RELEASE-EVIDENCE.md](RELEASE-EVIDENCE.md).
