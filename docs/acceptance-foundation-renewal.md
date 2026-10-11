# Foundation acceptance renewal (HOP-R01–R05)

Status: **HOP-R01 through HOP-R05 are closed at the delivery tip.** This document
renews acceptance for the current tip; it does not modify the historical milestone
matrices (`docs/acceptance-m1.md`, `docs/acceptance-m2.md`,
`docs/acceptance-m3.md`) or any byte under `docs/evidence/` or `specs/`.

Next roadmap item after this acceptance: **AOP-020** (`BACKLOG.yaml`,
`current_gate.next_roadmap_item_after_acceptance`). M4–M10 remain planned; M5–M9
are not delivered by this acceptance.

## Environment and revision

| Item | Value |
|------|-------|
| Accepted revision (gate) | `2468b040146ddfe293216c89703bb6fd1d87fdfd` |
| Follow-up formatting-only commit | `37ec392a5115fa74c5ab2102ecc9628dcd522ac8` — `ruff format` on `broker_relay.py` + `runner.py`, AST-identical (see Corrections) |
| ESF hop-v1 gate run | **`hop-gate-task10-2468b04`** (`.factory/runs/hop-gate-task10-2468b04`) |
| Gate result | `HOP GATE: PASSED`; Factory `SUCCEEDED`; verification `passed: true` (6/6 mandatory steps) |
| Gate interpreter | CPython **3.12.12** (`probe-python`) |
| Gate sandbox | bubblewrap **0.8.0**; `--unshare-pid --ro-bind / / true` exit 0 |
| Gate leftover sandboxes | **0** (`CleanupVerified=True`, remaining=0) |
| Gate egress condition | `EgressVerified=Unknown (ProbeInconclusive)` — documented warning under the acknowledged open egress policy, never a silent pass |

## Command / result matrix

| Command | Environment | Observed result |
|---------|-------------|-----------------|
| `scripts/dev check` | host, pinned toolchain | **exit 0** after `37ec392`; Ruff check + format pass; `Success: no issues found in 47 source files` (0 findings, 0 BLOCKED). Before `37ec392` it was exit 1 from `ruff format --check` on two unformatted files (mypy was already clean); fixed formatting-only. |
| `pytest tests -m "not live"` (gate step `pytest-nonlive`; developer entry point `scripts/dev test --suite all`) | Cube gate | exit 0; 345 selected, **341 passed, 4 skipped**; all 4 skips are the documented CPython 3.14 environment gate on `tests/test_foundation_bootstrap.py` |
| sandbox lane (`assert-sandbox-suite-ran`) | Cube gate | **84 passed, 0 failed, 0 errors, 0 skipped** |
| `scripts/dev test --suite unit` | host | 254 passed, 92 deselected |
| `scripts/dev test --suite contract` | host | 14 passed, 1 failed (host-only; see below) |
| `scripts/dev test --suite sandbox` | host | 18 failed, 59 passed, 7 errors — **environmental** (this host user cannot create the required namespaces; `unshare -n` is rejected with `Operation not permitted` and bwrap spawns exit 1). The Cube gate runs the same tests green. |
| `bash scripts/build_release.sh` | host, offline | exit 0; sdist + wheel built and installed offline; see [offline-install.md](verification/offline-install.md) |
| `python -c 'yaml.safe_load(.github/workflows/ci.yml)'` | host | parses; jobs `lint`, `types`, `tests` |
| `git diff --numstat -- docs/evidence docs/acceptance-m1.md docs/acceptance-m2.md docs/acceptance-m3.md specs` | host | **empty** (historical bytes preserved) |

The host is not the acceptance environment (repository rule: run project code in
Cube). Host-side sandbox/contract failures above are environmental and are not
counted as acceptance failures; the Cube gate result is authoritative.

## Work-item closure

| Item | Disposition | Evidence |
|------|-------------|----------|
| HOP-R01 baseline and scope reconciliation | closed | `docs/foundation-review.md`, `docs/acceptance-foundation.md` (historical 2026-10-01 matrix, bytes preserved), `docs/acceptance-foundation-delivery-2026-10-09.md` (renewed delivery-branch matrix, WP-4.7), independent sign-off commits |
| HOP-R02 reproducible tests and non-destructive offline setup | closed at toolchain level | `scripts/dev` fail-closed resolution; `scripts/dev check` exit 0; internal O1 request record (not published) |
| HOP-R03 typed lifecycle orchestration and CLI configuration | closed | typed lifecycle interfaces; unified exit codes 0/1/2/3/130; slice tests in the gate run |
| HOP-R04 integrity, isolation and recovery | closed | egress broker (ADR-012), cgroup quotas with honest `unenforced[]` (ADR-013), verifier candidate isolation (ADR-005 revision); negative tests in `tests/security/**`; independent reviews R-008/R-009/R-010 |
| HOP-R05 documentation consolidation and renewed acceptance | closed | this document; `docs/runbook.md`; README/AGENTS/BUILD_PLAN/BACKLOG reconciliation |

## Unexecuted items (with reasons)

- **Live suite (`scripts/dev test --suite live`)** and any local-model acceptance:
  no qualified local model deployment is available (owner input O2 absent). Not
  run, not inferred from fixtures.
- **Live Pi requalification**: the pinned installed Pi build is requalified as a
  separate work item; foundation acceptance does not claim it. Historical `0.85.1`
  literals in fixtures are intentionally version-agnostic and are **not** evidence
  about any installed build.
- **CI jobs** in `.github/workflows/ci.yml`: no network/GitHub runner on the
  delivery host. The workflow is a definition; every job is recorded unexecuted in
  [offline-install.md](verification/offline-install.md).
- **Byte-reproducible release artifacts**: not claimed; the build backend stamps
  archive timestamps.
- **Host-side sandbox/contract suites**: environmental (see matrix).

## Corrections recorded by this acceptance

1. **`scripts/dev check` pipeline artifact.** An initial reading of the check
   result as "exit 0" came from `python3 scripts/dev check | tail` followed by
   `echo $?`, which reports `tail`'s status. The true pre-fix result was **exit 1**
   from `ruff format --check` on two committed files while mypy was already clean
   (`Success: no issues found in 47 source files`). Fixed formatting-only in
   `37ec392a5115fa74c5ab2102ecc9628dcd522ac8`: `ruff format` on
   `src/hop/harnesses/broker_relay.py` and `src/hop/runner.py`, AST-identical
   before/after for both files, zero semantic change. The check is now genuinely
   exit 0 / 0 findings / 47 files when invoked without a pipeline.
2. **Sandbox-marker claim.** See finding 1 below: the WP-4.1a "only 2 tests carry
   the sandbox marker" statement was wrong and is superseded.

## Findings recorded by this acceptance

1. **Sandbox-marker assignment (WP-4.1a, corrected).** The WP-4.1a draft claimed
   "only 2 tests carry `pytest.mark.sandbox`". That was wrong: it counted
   decorators only. `tests/conftest.py::pytest_collection_modifyitems` assigns
   markers dynamically by module basename, so the sandbox lane collects **84/346**
   tests and the `unit + contract` lane 261. The CI lane split therefore works as
   intended. Residual soft finding: the conftest module map is name-based and
   unenforced, so a new bwrap-dependent test file that is not added to the map
   would silently land in the unit lane; the ESF hop-v1 gate compensates with the
   mandatory `assert-sandbox-suite-ran` step (84 passed, 0 skipped). This is a CI
   hygiene defect, not a foundation blocker.
2. **Synthetic 0.85.1 literals are version-agnostic.** Fixture transcripts use
   synthetic identities/hashes; the `0.85.1` string is a placeholder, not a
   qualified build identity. No live claim may be derived from it.
3. **Host vs Cube environment split.** The namespace-dependent suite cannot run as
   this host user; acceptance relies on the Cube gate. Any future host-side run of
   those suites must be reported as environmental, not as a product failure.

## Compatibility / migration notes for 0.2 readers

- Command surface, exit codes (0 pass, 1 verified failure, 2 invalid input,
  3 blocked/inconclusive or infrastructure, 130 cancellation), and path precedence
  are consolidated in [runbook.md](runbook.md).
- The deprecated `aop` alias and `AOP_*` environment variables remain through 1.0
  for file/CLI compatibility; they do not imply multi-user or enterprise features.
- Historical M1/M2/M3 evidence, schemas, and locks stay readable and must not be
  rewritten. The offline toolchain pin (`development-tools.lock.json`) is
  host-specific: the type checker runs under a hash-manifested CPython 3.13
  environment with `--python-version 3.14`; CI uses a knowingly different path.
