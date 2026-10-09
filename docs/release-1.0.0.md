# HOP 1.0.0 — release description

> **Status: release engineering complete; NOT RELEASED.** No tag, no pushed
> branch, and no artifact publication is claimed by this document. Publication
> is an explicit owner action. The artifacts below were built and qualified
> locally and offline from the approved wheelhouse.

## Summary

HOP 1.0.0 is the first release of the single-user local coding tool defined by
ADR-011: it runs one local user's coding, debugging, review, test, and
dependency-maintenance workflows against locally served models, keeps agent and
verifier work in separate trust domains, and decides every task outcome with a
trusted verifier rather than with agent text.

1.0.0 delivers M4 (local coding evaluation packs), retires the 0.2 compatibility
layer, and completes the release-engineering surface (version, migration guide,
release notes, reconciled status documentation). Optimization and activation are
explicitly **not** part of this release.

## Release artifacts

Built at the **release revision** with

```sh
SOURCE_DATE_EPOCH=1700000000 HOP_WHEELHOUSE=/home/mitko/dev/esf/worktrees/hop-wheelhouse \
  scripts/build_release.sh
```

(no network; PEP 517 backend and every dependency come from the approved local
wheelhouse; the install probe runs with `--no-index --require-hashes`).

| Artifact | sha256 |
| --- | --- |
| `dist/hop-1.0.0-py3-none-any.whl` | `42439320855807682c7bd1b63bb0aa8d6a599d682ea0786b05a605a87644aee6` |
| `dist/hop-1.0.0.tar.gz` | `1c8769cbd3e1a98412fd7a763d3e45533d34bc1b3b07aa62170025d1275ff0bd` |

`dist/MANIFEST.sha256` records both digests; the wheelhouse manifest
(`/home/mitko/dev/esf/worktrees/hop-wheelhouse/MANIFEST.sha256`, 17 dependency
wheels) was refreshed by the same run. The installed wheel reports
`hop version: 1.0.0` and exposes exactly one console script (`hop`); the removed
`aop` alias is absent from `entry_points.txt`.

**These hashes supersede the earlier artifacts.** WP-4.1 (`9acc72e7…` wheel,
`a3a26569…` sdist) and WP-4.5 (`729aeee9…` wheel, `b38611e7…` sdist) were built
before task-51 added `hop/evaluation/legacy.py`, so they did not
cover the release content; they must not be cited as the 1.0.0 artifacts. The
release wheel has **80 entries including `hop/evaluation/legacy.py`**.

**Artifact contents.** The wheel stays minimal: `hop/` plus its dist-info (80
entries, no `docs/`), because the installed CLI diagnostics are self-contained.
The sdist is the operator-facing artifact and ships the release docs — it
contains `CHANGELOG.md`, `README.md`, `docs/migration-1.0.md` (the file every
migration diagnostic points at) and `docs/release-1.0.0.md`, declared in
`MANIFEST.in`.

**Reproducibility.** Setuptools embeds per-file timestamps in the wheel, so a
plain rebuild changes the wheel's bytes even when its contents are identical.
Building with `SOURCE_DATE_EPOCH=1700000000` makes the wheel byte-reproducible:
two consecutive builds at the release revision both produced the `42439320…`
hash above. The sdist's *contents* are stable, but its compressed bytes still
vary between builds, so its digest is recorded as built at this run rather than
as a reproducible value.

Qualification probe results from the build: `hop --help` exit 0;
`hop --workspace <tmp> init` exit 0 (created `hop.toml`); `hop doctor` exit 3 in
a neutral workspace (expected non-zero: no registered deployment inventory or
verifier store). The rebuilt sdist was additionally installed offline in a fresh
venv and reports `1.0.0` with the same single console script and the same
migration exit codes; the full record is
[artifact-qualification.md](verification/artifact-qualification.md).

## What is in scope for 1.0.0

Delivered and independently reviewed:

- **M0–M3** slices, re-verified at the delivery tip; historical acceptance
  matrices (`docs/acceptance-m{1,2,3}.md`) and `docs/evidence/**` are unchanged.
- **HOP-R01–R05 foundation**: baseline and boundary review, pinned offline
  toolchain, typed lifecycle and uniform CLI diagnostics, boundary enforcement
  (no-network workers with a loopback inference broker, cgroup v2 quota
  reporting, verifier candidate isolation), and documentation consolidation.
- **M4 provenance and protected splits (AOP-020)**: typed evaluation contracts,
  deterministic import of repository/group/case-package records, and sealed
  evaluator-owned holdout storage with an access ledger (R-011, R-014, R-016).
- **M4 coding and debugging packs (AOP-021)**: feature, refactor, and
  bug-reproducer packs on frozen snapshots with hidden behavioral/regression
  tests and API/scope review, baseline-vs-candidate failure classification, and
  visible-test-overfit rejection (R-020).
- **M4 review and maintenance packs (AOP-022, AOP-025)**: local diff/commit
  review (R-015) and test/dependency maintenance (R-017).
- **Case-package integrity A-5–A-9 (R-019)**.
- **M10 safety and recovery**: adversarial boundary and reward-hacking suite
  (AOP-050, R-018), evidence-chain backup/restore (AOP-051, R-021, with the two
  reported defects fixed and confirmed in R-023), disconnected installation and
  execution qualification (AOP-052), owner-only local file protection and
  activity history (AOP-049, R-012).

## Breaking changes and migration

The 0.2 compatibility layer is removed. See
[migration-1.0.md](migration-1.0.md) for the full table.

- The `aop` console script is gone; use `hop`.
- The `AOP_*` environment fallback is gone; use `HOP_*`. A removed variable that
  is set fails with `LegacyEnvRemoved` / `legacy_env_removed` (exit 2) instead of
  silently resolving. The `AOP_PI_BASE_URL` worker scrub is intentionally
  retained as a local-only security guarantee.
- Legacy flat command forms and the `hop run --case ...` shorthand are gone;
  they exit 2 with `legacy_command_removed` and a rewrite hint.

Stored evidence is not affected: historical runs, bundles, locks, schemas, and
acceptance records remain byte-identical and readable.

## What is not in 1.0.0

- Optimization loops and any promotion/activation claim (M5–M9; AOP-024 and
  AOP-042 remain out of scope under ADR-011).
- Owner-confirmed local activation and rollback (M8).
- Optional local build-repair fixtures (AOP-023) and calibrated local judges
  (AOP-026).
- Live local-model acceptance was **executed and passed**
  (`run-58f5589ef776`: `outcome: pass`, `verdict: pass`, `agent_seconds 47.71`,
  Pi 1.1.0 qualified, deployment `qwen-flash-next-IQ2_S_-_2.5_bpw`) and re-run at
  the release revision (`run-85d1c3a117ef`: `outcome: pass`, `verdict: pass`,
  `agent_seconds 44.71`); see `docs/evidence/1.0/live-model-acceptance.md`. The pinned Pi 1.1.0 build was requalified
  (task-19). Nothing here is inferred from fixtures.
- The M10 end-to-end lifecycle demonstration (AOP-054) is delivered with gate
  `hop-gate-task32-674b991`: import → protected splits → frozen evaluation →
  trajectory/completeness → profile compile → APM export → evidence read-back,
  plus the four special cases. Its host-side scripted evaluation leg is
  **honestly blocked** (`verifier_sandbox_unavailable`, `telemetry_incomplete`),
  and the trusted-verifier path is evidenced by the Cube sandbox lane.
- Overhead and resource fairness (AOP-053) are measured (gate
  `hop-gate-task31-4cfb317`), including the 120-run bounded-cardinality soak.

## Verification and evidence

- ESF deterministic gate (hop-v1): all conditions satisfied, sandbox suite with
  zero skips, no leftover sandboxes; run ids are recorded in the release
  evidence pack (`docs/evidence/1.0/`, WP-4.2).
- `scripts/dev check` exits 0 with 0 findings; the host unit suite is green.
- Independent verification: the verdict index in
  the internal review index (not published) (R-001–R-023) binds each confirmed claim to a
  commit sha.

## Owner actions

1. Review the evidence pack and release checklist (WP-4.2).
2. Run the freeze gate (WP-5.1) at the frozen sha, including the live
   model-backed acceptance and the G8 owner-run.
3. Then, and only then, tag `v1.0.0`, push the branch, or publish artifacts.
