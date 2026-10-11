# HOP 1.0.0 release evidence pack

Public release revision: **`d1bf6ccbee0c3790fbc757a596e059ddbe7f7db5`** (branch `public/1.0.0`), the revision the
Lead verified with a namespace-capable shell. This pack is the release
qualification evidence; the release checklist is
[docs/release-checklist.md](../../release-checklist.md) and the security sign-off is
[docs/threat-model.md](../../threat-model.md).

Scope statement: **optimization, candidate generation, activation and rollback are
not part of 1.0.** No claim in this pack is inferred from fixtures; every row points
at a run id, a command, or a recorded artifact.

## 1. Gate runs (ESF hop-v1)

| Run id | Revision | Result | Cleanup |
|--------|----------|--------|---------|
| `rel-hopv1-d1bf6cc` | `d1bf6ccbee0c` | PASSED; `Verified=True`, 6/6 steps, sandbox lane 0 failed / 0 errors / 0 skipped | 0 remaining, verified |
| `rel-disc-d1bf6cc` | `d1bf6ccbee0c` | PASSED; disconnected profile 2/2 (DNS/LAN/link-local blocked, loopback connected) | 0 remaining, verified |
| `rel-live-d1bf6cc` | `d1bf6ccbee0c` | PASSED; live OpenCode Go agent, exit 0 in 17.75 s, real patch `src/hop/health.py` + `tests/test_health.py` | 0 remaining, verified |
| `hop-gate-20261009-190107-3` | `4d576a81127f` | PASSED; sandbox lane 86/0/0 (two-phase) | 0 remaining |
| `hop-gate-task32-674b991` | `674b991` | PASSED; sandbox lane 99/0/0; non-live 0 failures | 0 remaining, verified |
| `hop-gate-task43-4f651c9` | `4f651c9` | PASSED; sandbox lane 91/0/0 | 0 remaining |
| `hop-gate-task41-a17fa3a` | `a17fa3a` | PASSED; sandbox lane 86/0/0 | 0 remaining |
| `hop-gate-task31-4cfb317` | `4cfb317` | PASSED; sandbox lane 86/0/0 | 0 remaining |
| `hop-gate-task29-b0a00bb` | `b0a00bb` | PASSED; sandbox lane 86/0/0 | 0 remaining |
| `hop-gate-task25-53f2084` | `53f2084` | PASSED; sandbox lane 86/0/0 | 0 remaining |
| `hop-gate-task22-45d8a2e` | `45d8a2e` | PASSED; sandbox lane 86/0/0 | 0 remaining |
| `hop-gate-task36-e709473` | `e709473` | PASSED; sandbox lane 99/0/0 | 0 remaining |
| `hop-gate-task10-2468b04` | `2468b040146d` | PASSED (foundation gate, G1) | 0 remaining |
| `wp24-deps-1` (+ `-disconnected`) | `81213fc421c4` | PASSED (AOP-025 pack, G5) | 0 remaining |
| `wp03-live-Bgreen-ec83c49` | `ec83c4978965` | PASSED (live agent, G7) | 0 remaining |
| `wp-live-tip-4f651c9` | `4f651c9af69f` | PASSED (live agent, G7) | 0 remaining |
| `live-tip-1248b69` | `1248b694e1e0` | PASSED (live agent, G7) | 0 remaining |

The canonical gate mapping (G1–G11) with one evidence pointer per gate is
[docs/release-checklist.md](../../release-checklist.md).

All gate runs carry the documented `EgressVerified=Unknown (ProbeInconclusive)`
warning; no gate reported a condition it did not evaluate. Each `hop-gate-*` run
also has a `-disconnected` profile run where the listing shows one.

## 2. Host verification at the release revision

Recorded by the Lead at `d1bf6ccbee0c3790fbc757a596e059ddbe7f7db5` (log:
`/home/mitko/dev/esf/.factory/lead-1.0-verify.log`):

```
All checks passed!
Success: no issues found in 73 source files
check_exit=0
565 passed, 1 deselected
suite_exit=0
```

So `scripts/dev check` exits 0 with 0 findings (the fail-closed pin requires the
approved ruff 0.16.10 on `PATH` and refuses the newer mise `latest` 0.17.0) and the
full non-live suite is 565 passed, 1 deselected / 0 failed. Historical bytes are preserved: **no historical file under
`docs/evidence/foundation/` or `docs/acceptance-foundation.md` is modified or
deleted anywhere on the branch — the branch only ADDS new files** (the 1.0 pack,
the new remeasurement and delivery-matrix files). Proven by
`git diff --numstat 4ba5b1a..HEAD -- docs/evidence/foundation docs/acceptance-foundation.md`
showing additions only, and
`git diff --diff-filter=DM --name-only 4ba5b1a..HEAD -- docs/evidence/foundation docs/acceptance-foundation.md docs/acceptance-m1.md docs/acceptance-m2.md docs/acceptance-m3.md specs`
being empty (the broad `-- docs/` form legitimately lists four updated non-historical
docs — `foundation-review.md`, `runbook.md`, `single-user-delivery.md`,
`threat-model.md` — so the claim is scoped to the intent path list). The historical
M1–M3 acceptance records and `specs/` are untouched.

## 3. Live local-model acceptance (G8)

| Field | Value |
|-------|-------|
| Run id (public revision) | `run-916a66ca4487` at `d1bf6cc` |
| Outcome / verdict | `pass` / `pass` (trusted verifier) |
| Harness | `pi` 1.1.0 (qualified; executable sha256 `c44ddd4e…`) |
| Deployment | `qwen-flash-next-IQ2_S_-_2.5_bpw`, `mitko` at `http://127.0.0.1:8000/v1` |
| Agent time / total | 44.8 s / 47.71 s |
| Evidence | [live-model-acceptance.md](live-model-acceptance.md) (Lead-recorded at the public revision); earlier G8 attempts are retained in the internal review record and are superseded by this release-bound run |

Coverage limitation: one live case on one deployment and one harness. This is a
qualification of the installed path, not a generalization claim. The full
freeze-gate run table is in [freeze-gate.md](freeze-gate.md).

## 4. Offline install and release artifacts

- Internal artifact-qualification record (commit `777d753`, not published): **offline
  fresh-venv install of `hop-1.0.0` from the wheel with `--no-index
  --require-hashes` — zero downloads, one console script, `pip check` clean**, and
  every migration exit code observed from the installed artifact
  (`legacy_env_removed` exit 2 naming `HOP_RUNS_DIR`, `legacy_command_removed`
  exit 2 with rewrite hints, `aop` absent, `AOP_PI_BASE_URL` scrub active). The
  wheel still omits `docs/` (D-2, 1.0.1 candidate).
- **Published artifacts at the public release revision `d1bf6cc`.**
  wheel `42439320855807682c7bd1b63bb0aa8d6a599d682ea0786b05a605a87644aee6` — 80 entries including `hop/evaluation/legacy.py` — and sdist
  `f3d7342d35f1f3735c644ad0f209b02a93f4d5dc7bf30ac68471e22c349be9f0` (ships `CHANGELOG.md`, `README.md`, `docs/migration-1.0.md`; deliberately omits
  `docs/release-1.0.0.md` so the archive cannot cite its own hash). Both were re-qualified
  offline in fresh venvs on the release branch (`--no-index --require-hashes`, zero
  downloads; version 1.0.0, one console script, help exits 0, migration exit codes 2, scrub
  active, backup create/verify/chain 0/0/0, `hop/evaluation/legacy.py` installed). These
  **supersede for publication** the earlier pre-task-51 builds
  (`729aeee9…`/`b38611e7…`, `9acc72e7…`/`a3a26569…`) and the intermediate
  release-branch sdists (`ca8e8d91…`/`326800aa…`); the `b71d2a7` cycle is retained
  only as an explicitly labelled superseded record. The wheel digest is
  unchanged from the frozen build because no packaged input changed; only the shipped
  documentation payload moved the sdist digest.
- **Wheel reproducibility (R-029, independently confirmed by independent-verifier):**
  with `SOURCE_DATE_EPOCH=1700000000` two scratch builds from `1be9a5e` are
  byte-identical (`42439320855807682c7bd1b63bb0aa8d6a599d682ea0786b05a605a87644aee6`);
  two plain builds 4 s apart differ only in embedded zip timestamps while all 80
  per-entry sha256 values are equal. So the wheel is byte-reproducible only with the
  epoch set; the sdist digest is recorded as-built.
- Internal offline-install record (not published): **Part B executed end-to-end offline on
  the delivery host** (sdist + wheel built from the tree with the pinned venv,
  installed into a clean venv from a local wheelhouse with `--no-index
  --require-hashes`, `hop --help` / `hop doctor` / smoke command recorded, wheel
  sha256 manifest). **Part A (CI) is recorded as unexecuted** with the
  CI-vs-local deviation list; no CI job is claimed green.
- Documented limitations D-1…D-4 (with mitigations and 1.0.1-candidate status) are
  recorded in [docs/release-checklist.md](../../release-checklist.md); D-1 is
  closed as a doc-reading error, D-2 is being fixed by task-50, D-3/D-4 are
  documented behaviour with 1.0.1 candidates.
- Release notes and the migration contract: `docs/release-1.0.0.md` (corrected by
  the Lead in `1efb2fa` to record AOP-053/054 and G8 as delivered).

## 5. M4 pack results

| Work item | Verdict | Bound sha | Recorded result |
|-----------|---------|-----------|-----------------|
| AOP-021 coding/debugging packs | R-020 confirmed | `d1739b4` | Cube `rv-t21`, `rv-t21b` |
| AOP-022 local diff/commit review pack | R-015 confirmed | `45d8a2e` | 6 labeled cases: 4 TP / 1 FP / 2 FN, precision 0.8, recall 0.6667, duplicates 1, stale 1, inconclusive 1; clean change abstains ([review-pack.md](../../verification/review-pack.md)) |
| AOP-025 test/dependency maintenance pack | R-017 confirmed | `81213fc` | mutation/fault detection measured; offline-approved artifacts only ([deps-pack.md](../../verification/deps-pack.md)) |

## 6. M10 hardening results

| Work item | Verdict | Bound sha / run | Recorded result |
|-----------|---------|-----------------|-----------------|
| AOP-049 owner-only storage, worker isolation, activity history | R-012 confirmed | `53f2084` | modes from real `stat()`; fail-closed insecure mode; tamper/drop/retention detected; worker isolation in Cube ([local-file-protection.md](../../verification/local-file-protection.md)) |
| AOP-050 adversarial boundary and reward-hacking suite | R-018 confirmed | `f759151`, fix `b0a00bb` | 11 refusal cases; four special cases; one recorded open finding (attacker-controlled attribute keys, no metric-label projection) ([adversarial-suite.md](../../verification/adversarial-suite.md)) |
| AOP-051 backup/restore of the evidence chain | R-021 confirmed | `a4c5934` | backup→wipe→restore→replay with two reported defects closed ([backup-restore.md](../../verification/backup-restore.md)) |
| AOP-053 overhead and resource fairness | gate | `hop-gate-task31-4cfb317` | import ~85.6 ms, CLI ~114.6 ms, trajectory page ~2.6 ms/400 events; 120-run soak: 19 bounded attribute keys, 0 metric labels; per-run broker isolation ([overhead-report.md](../../verification/overhead-report.md)) |
| AOP-054 end-to-end lifecycle demonstration | R-024 confirmed | `1248b69`, corrections `6dd0196` | qualified import/splits/profile-export, live G8 reference, honest blocked host leg, four special cases ([README.md](README.md)) |
| Sealed store integrity A-1..A-4 | R-014 confirmed | `931d31f` | tamper/integrity refusals |
| Sealed-store adoption in run/verify | R-016 confirmed | `3dd6a21` | adoption without exposing hidden material |
| A-5/A-6/A-9 typed lifecycle | R-019 confirmed | `d71943b` | typed errors and exit codes |
| D-2 verification RPC socket cleanup | R-023 confirmed | `6edbdf2` | socket removed on every exit |
| Symlinked interpreter install root | R-022 + R-022-HOST | `d920e92`, host closure | Cube confirmed; host path closed by the Lead's run |

## 7. Independent review verdict index

The internal review index (not published) holds R-001–R-027 plus the R-022-HOST closure and
the R-027 addendum. Each entry binds its verdict to a commit sha; the pack cites
those shas above and does not restate them. Confirmed entries include R-001
`f5dc9ed`, R-003 `9bcae6e`, R-005 `3ba4bad`, R-007 `55e9a9e`, R-009 `1beb23d`,
R-010 `105fba5`, R-011 `f412c84`, R-012 `53f2084`, R-013 `189a782`, R-014
`931d31f`, R-015 `45d8a2e`, R-016 `3dd6a21`, R-017 `81213fc`, R-018 `f759151`,
R-019 `d71943b`, R-020 `d1739b4`, R-021 `a4c5934`, R-022 `d920e92` (+ host
closure), R-023 `6edbdf2`, R-024 `1248b69` (corrections cleared at `6dd0196`),
R-025 task-35 release engineering `0123cdc`, R-026 task-48 artifact qualification
`777d753`, R-027 task-36 release evidence pack (this pack) + addendum.
R-008 is retained as the **pre-fix defect record (NOT CONFIRMED)**; G1 cites
R-009, not R-008.

## 8. Unexecuted / blocked (with reasons)

| Item | Status | Reason |
|------|--------|--------|
| CI jobs (Part A of offline install) | unexecuted | no external CI execution on this host; deviation list recorded |
| sdist installation qualification | unexecuted | only the wheel was installed and qualified; the sdist contents were inspected (internal artifact-qualification record §9, not published) and its digest is now `f3d7342d…` at the public release revision |
| `hop run start` from the installed artifact | unexecuted | needs a registered local deployment and a namespace-capable shell; the source-tree equivalent is G8 |
| Sandboxed isolation from the installed artifact | unexecuted | the agent shell cannot create user namespaces; covered for the source tree by the ESF Cube gate |
| Host-side bwrap-dependent suite in the agent shell | blocked | the agent shell is AppArmor-confined and cannot create namespaces; executed in the Cube gate instead |
| Optimization / candidate generation / activation / rollback | not in 1.0 | M5–M9; AOP-024 and AOP-042 out of scope under ADR-011 |
| Optimizer-vs-interactive scheduling | not measured | no optimizer exists in 1.0 |
| Hostile-workload qualification | not claimed | worker isolation is tested against scripted adversaries, not a determined kernel-level adversary |
| Live coverage | limited | one case, one deployment, one harness (G8) |

## 9. Release candidate report

Population: single-user local coding tool, Pi harness only, local-only inference,
evaluation-only pipeline. The M4 packs and M10 hardening above are the release
population; no optimization or activation capability is shipped.

Commands: see each linked artifact; the release-critical commands are
`scripts/dev check`, the non-live suite, the ESF hop-v1 gate, the offline install
qualification, and `hop run start --case debug-offbyone --model qwen-flash-next`
(G8).

Internal consistency: `docs/release-1.0.0.md` was corrected by the Lead to record
AOP-053/AOP-054 and the G8 live run as delivered; this pack and
[docs/release-checklist.md](../../release-checklist.md) cite the corrected doc.
The public release revision is `d1bf6ccbee0c3790fbc757a596e059ddbe7f7db5` on `public/1.0.0`.
