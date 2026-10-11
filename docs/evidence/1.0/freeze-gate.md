# Freeze-gate evidence at the public release revision (WP-5.1 / WP-5.1b)

Public release revision: **`d1bf6ccbee0c3790fbc757a596e059ddbe7f7db5`** on branch
`public/1.0.0`. No further source changes are planned beyond release evidence.

Every row names who executed it. Nothing is inferred; each run id below was checked
against `.factory/runs/<id>/run.json` at the public release revision.

## Runs

| Step | Run id | Executed by | Revision | Result | Conditions / cleanup |
|------|--------|-------------|----------|--------|----------------------|
| Deterministic hop-v1 gate (agent `noop`) | `rel-hopv1-d1bf6cc` | factory-delivery | `d1bf6cc` | PASSED | Factory SUCCEEDED, Verification SUCCESS, `Verified=True`, 6/6 steps, `GateIntegrity=Unmodified`, `CleanupVerified=True` remaining=0, no live sandboxes; sandbox suite 0 failed / 0 errors / 0 skipped. The non-live suite has exactly 4 **environment-gated** skips (offline wheelhouse evidenced for CPython 3.14 only; Cube runs 3.12.12) with reasons at `tests/test_foundation_bootstrap.py:48,58,70,77` |
| Disconnected profile (AOP-052, agent `noop`) | `rel-disc-d1bf6cc` | factory-delivery | `d1bf6cc` | PASSED | `Verified=True`, 2/2 steps, `GateIntegrity=Unmodified`, cleanup 0. In-namespace probes: DNS blocked (`gaierror`); `1.1.1.1:443`, host LAN and link-local metadata all blocked; loopback positive control connected. Offline install of the **Cube-built** wheel (a fresh in-Cube build, **not** the shipped artifact) and smoke `hop --help` 0 / `init` 0 / `doctor` 3 |
| Live OpenCode Go agent acceptance (ESF end-to-end) | `rel-live-d1bf6cc` | factory-delivery | `d1bf6cc` | PASSED | Factory SUCCEEDED, Verification SUCCESS, Agent SUCCESS exit 0 in 17.75 s, `Verified=True`, 6/6 steps, `GateIntegrity=Unmodified`, `CleanupVerified=True` remaining=0; real patch `src/hop/health.py` + `tests/test_health.py` (patch in `.factory/runs/rel-live-d1bf6cc/`) |
| G8 HOP live local-model acceptance | `run-916a66ca4487` | Lead | `d1bf6cc` | `outcome=pass`, `verdict=pass` | `agent_seconds=44.8`, `total_seconds=47.71`; Pi 1.1.0 `qualified` (executable sha256 `c44ddd4e…`); deployment `qwen-flash-next-IQ2_S_-_2.5_bpw`; recorded in [live-model-acceptance.md](live-model-acceptance.md) |
| Host validation at the public revision | n/a (commands) | Lead | `d1bf6cc` | PASS | `scripts/dev check` exit 0 (`All checks passed!`, `Success: no issues found in 74 source files`) — this requires the approved **ruff 0.16.10** on `PATH`; the fail-closed tool pin correctly refuses the newer mise `latest` ruff 0.17.0. `scripts/dev test --suite all` → `565 passed, 1 deselected`, `suite_exit=0` |

All gate runs report `d1bf6ccbee0c3790fbc757a596e059ddbe7f7db5` as their baseline,
so the freeze-gate evidence is revision-consistent.

Every run in the table reports `remaining=0` / "No live sandboxes".

## Artifacts at the public release revision

The final published artifacts are:

| Artifact | sha256 | Notes |
|----------|--------|-------|
| `dist/hop-1.0.0-py3-none-any.whl` | `42439320855807682c7bd1b63bb0aa8d6a599d682ea0786b05a605a87644aee6` | **80 entries**, includes `hop/evaluation/legacy.py`; byte-identical to the independently confirmed `SOURCE_DATE_EPOCH=1700000000` reproducibility build |
| `dist/hop-1.0.0.tar.gz` | `f3d7342d35f1f3735c644ad0f209b02a93f4d5dc7bf30ac68471e22c349be9f0` | recorded as-built; includes the G2 interpreter fix and its regression tests |

**Superseded (must not be cited as the published artifacts):** the intermediate
release-branch sdists (retained only as this explicitly labelled superseded
record), and the earlier pre-task-51 pair `729aeee9…` wheel /
`b38611e7…` sdist.

## Wheel reproducibility (independently confirmed, R-029)

With `SOURCE_DATE_EPOCH=1700000000`, two scratch builds are byte-identical
(`42439320…`, all zip timestamps normalised); two plain builds 4 s apart differ
only in embedded zip timestamps while all 80 per-entry sha256 values are equal. So
the wheel is byte-reproducible only with `SOURCE_DATE_EPOCH` set; the sdist digest
is recorded as-built.

## Status

- Freeze gate: **PASS** for the deterministic, disconnected and live OpenCode Go
  agent runs, for G8 and for the host validation.
- Gate **G11**: the owner authorized the push/tag on 2026-10-11; execution is
  recorded in the handoff after the push. See
  [docs/release-checklist.md](../../release-checklist.md) and
  [RELEASE-EVIDENCE.md](RELEASE-EVIDENCE.md).
