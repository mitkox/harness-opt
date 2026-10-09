# Freeze-gate evidence at the frozen revision (WP-5.1 / WP-5.1b)

Frozen revision: **`779e73f61f5112b681242e0376ed9c78c6e2d9e1`**, declared by the
Lead (owner's delegate for this delivery). No further source or documentation
changes are planned beyond freeze-gate and handoff evidence.

Every row names who executed it. Nothing is inferred; each run id below was checked
against `.factory/runs/<id>/run.json` at the frozen revision.

## Runs

| Step | Run id | Executed by | Revision | Result | Conditions / cleanup |
|------|--------|-------------|----------|--------|----------------------|
| Deterministic hop-v1 gate (agent `noop`) | `freeze-hopv1-779e73f` | factory-delivery | `779e73f` | PASSED | Factory SUCCEEDED, Verification SUCCESS, `Verified=True`, 6/6 steps, `GateIntegrity=Unmodified`, `CleanupVerified=True` remaining=0, no live sandboxes; sandbox suite 0 failed / 0 errors / 0 skipped. The non-live suite has exactly 4 **environment-gated** skips (offline wheelhouse evidenced for CPython 3.14 only; Cube runs 3.12.12) with reasons at `tests/test_foundation_bootstrap.py:48,58,70,77` |
| Disconnected profile (AOP-052, agent `noop`) | `freeze-disc-779e73f` | factory-delivery | `779e73f` | PASSED | `Verified=True`, 2/2 steps, `GateIntegrity=Unmodified`, cleanup 0. In-namespace probes: `dns_pypi_org` gaierror; `1.1.1.1:443`, LAN `192.168.0.16:9443` and `169.254.169.254:80` all blocked; loopback positive control connected. Offline install of the **Cube-built** wheel (sha256 `9651b4693e203092d786377ca4d786de013eb82cdfc6740c178aab3b164f7992` — this is the disconnected-profile build, **not** the shipped artifact) and smoke `hop --help` 0 / `init` 0 / `doctor` 3 |
| Live OpenCode Go agent acceptance (ESF end-to-end) | `freeze-live-779e73f` | factory-delivery | `779e73f` | PASSED | Factory SUCCEEDED, Verification SUCCESS, Agent SUCCESS exit 0 in 25.35 s, `Verified=True`, 6/6 steps, `GateIntegrity=Unmodified`, `CleanupVerified=True` remaining=0; real patch `src/hop/health.py` + `tests/test_health.py` (patch in `.factory/runs/freeze-live-779e73f/`) |
| G8 HOP live local-model acceptance | `run-85d1c3a117ef` | Lead | `779e73f` (record rebound in `6c7bc87`) | `outcome=pass`, `verdict=pass` | `agent_seconds=44.71`; Pi 1.1.0 `qualified` (executable sha256 `c44ddd4e…`); deployment `qwen-flash-next-IQ2_S_-_2.5_bpw`; recorded in [live-model-acceptance.md](live-model-acceptance.md) |
| Host validation at the frozen sha | n/a (commands) | Lead | `779e73f` | PASS | `scripts/dev check` exit 0 (`All checks passed!`, `Success: no issues found in 74 source files`); `scripts/dev test --suite all` → `557 passed, 1 deselected`, `suite_exit=0`; log `/home/mitko/dev/esf/.factory/host-frozen.log` |
| Freeze-gate document gate (this file) | `hop-gate-task53-711dd8c` | foundation-tooling | `711dd8c` (docs-only on top of the frozen revision) | PASSED | sandbox lane 99/0/0/0; `CleanupVerified=True`, remaining=0 |

All gate runs report `779e73f61f5112b681242e0376ed9c78c6e2d9e1` as their baseline
(except this document's own docs-only gate), so the freeze-gate evidence is
revision-consistent.

Concurrent-sandbox note: a live sandbox observed on the host during this window
belonged to `factory run=hop-gate-20261009-210752-4`, **not** to any run in this
table. Every run listed here reports `remaining=0` / "No live sandboxes".

## Artifacts at the frozen revision (release blocker CLOSED by task-54)

Independent-verifier **R-029** found that the task-50 artifacts predated task-51
(`456aa7b`): the wheel had 79 entries and lacked `hop/evaluation/legacy.py`.

**task-54 (`3b80e9d`) rebuilt and re-qualified at the frozen revision.** Final
hashes, verified here with `sha256sum` against `dist/MANIFEST.sha256`:

| Artifact | sha256 | Notes |
|----------|--------|-------|
| `dist/hop-1.0.0-py3-none-any.whl` | `42439320855807682c7bd1b63bb0aa8d6a599d682ea0786b05a605a87644aee6` | **80 entries**, includes `hop/evaluation/legacy.py`; byte-identical to the verifier's `SOURCE_DATE_EPOCH=1700000000` reproducibility build |
| `dist/hop-1.0.0.tar.gz` | `1c8769cbd3e1a98412fd7a763d3e45533d34bc1b3b07aa62170025d1275ff0bd` | recorded as-built |

The earlier hashes (`729aeee9…` wheel, `b38611e7…` sdist) are **superseded** and
must not appear as final. The G9 row in
[docs/release-checklist.md](../../release-checklist.md) and
[RELEASE-EVIDENCE.md](RELEASE-EVIDENCE.md) §4 now cite the task-54 hashes.

## Wheel reproducibility (independently confirmed, R-029)

With `SOURCE_DATE_EPOCH=1700000000`, two scratch builds from `1be9a5e` are
byte-identical (`42439320…`, all zip timestamps normalised); two plain builds 4 s
apart differ only in embedded zip timestamps while all 80 per-entry sha256 values
are equal. So the wheel is byte-reproducible only with `SOURCE_DATE_EPOCH` set; the
sdist digest is recorded as-built.

## Status

- Freeze gate: **PASS** for the deterministic, disconnected and live OpenCode Go
  agent runs, for G8 and for the host validation.
- Gate **G11 remains IN PROGRESS**: push, PR and tag require the owner's explicit
  authorization. See [docs/release-checklist.md](../../release-checklist.md) and
  [RELEASE-EVIDENCE.md](RELEASE-EVIDENCE.md).
