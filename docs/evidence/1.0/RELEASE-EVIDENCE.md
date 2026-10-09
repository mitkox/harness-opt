# HOP 1.0.0 release evidence pack

Release candidate revision: **`0123cdc29464`** (branch `esf/hop-1.0`), the tip the
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
| `hop-gate-20261009-203429-8` | `0123cdc29464` | PASSED; sandbox lane 99 ran / 0 failed / 0 skipped | 0 remaining, verified |
| `lead-tip-0123cdc` | `0123cdc29464` | PASSED; sandbox lane 99/0/0 | 0 remaining |
| `lead-tip-0123cdc-disconnected` | `0123cdc29464` | disconnected profile PASSED | 0 remaining |
| `hop-gate-20261009-190107-3` | `4d576a81127f` | PASSED; sandbox lane 86/0/0 (two-phase) | 0 remaining |
| `hop-gate-task32-674b991` | `674b991` | PASSED; sandbox lane 99/0/0; non-live 0 failures | 0 remaining, verified |
| `hop-gate-task43-4f651c9` | `4f651c9` | PASSED; sandbox lane 91/0/0 | 0 remaining |
| `hop-gate-task41-a17fa3a` | `a17fa3a` | PASSED; sandbox lane 86/0/0 | 0 remaining |
| `hop-gate-task31-4cfb317` | `4cfb317` | PASSED; sandbox lane 86/0/0 | 0 remaining |
| `hop-gate-task29-b0a00bb` | `b0a00bb` | PASSED; sandbox lane 86/0/0 | 0 remaining |
| `hop-gate-task25-53f2084` | `53f2084` | PASSED; sandbox lane 86/0/0 | 0 remaining |
| `hop-gate-task22-45d8a2e` | `45d8a2e` | PASSED; sandbox lane 86/0/0 | 0 remaining |
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

Recorded by the Lead at `0123cdc29464` (log:
`/home/mitko/dev/esf/.factory/lead-1.0-verify.log`):

```
All checks passed!
Success: no issues found in 73 source files
check_exit=0
551 passed, 1 deselected in 51.24s
suite_exit=0
```

So `scripts/dev check` exits 0 with 0 findings and the full non-live suite is
551 passed / 0 failed. Historical bytes are unchanged:
`git diff --numstat -- docs/evidence docs/acceptance-m1.md docs/acceptance-m2.md
docs/acceptance-m3.md specs` is empty.

## 3. Live local-model acceptance (G8)

| Field | Value |
|-------|-------|
| Run id | `run-58f5589ef776` |
| Outcome / verdict | `pass` / `pass` (trusted verifier) |
| Harness | `pi` 1.1.0 (qualified) |
| Deployment | `qwen-flash-next-IQ2_S_-_2.5_bpw`, `mitko` at `http://127.0.0.1:8000/v1` |
| Agent / total | 47.71 s / 50.66 s |
| Evidence | [live-model-acceptance.md](live-model-acceptance.md) (Lead-recorded, revision `138a4ef`) |

Coverage limitation: one live case on one deployment and one harness. This is a
qualification of the installed path, not a generalization claim.

## 4. Offline install and release artifacts

- `docs/verification/offline-install.md`: **Part B executed end-to-end offline on
  the delivery host** (sdist + wheel built from the tree with the pinned venv,
  installed into a clean venv from a local wheelhouse with `--no-index
  --require-hashes`, `hop --help` / `hop doctor` / smoke command recorded, wheel
  sha256 manifest). **Part A (CI) is recorded as unexecuted** with the
  CI-vs-local deviation list; no CI job is claimed green.
- Release notes and the migration contract: `docs/release-1.0.0.md`.

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

`docs/verification/reviews.md` holds R-001–R-024 plus the R-022-HOST closure. Each
entry binds its verdict to a commit sha; the pack cites those shas above and does
not restate them. Confirmed entries include R-001 `f5dc9ed`, R-003 `9bcae6e`,
R-005 `3ba4bad`, R-007 `55e9a9e`, R-009 `1beb23d`, R-010 `105fba5`, R-011
`f412c84`, R-012 `53f2084`, R-013 `189a782`, R-014 `931d31f`, R-015 `45d8a2e`,
R-016 `3dd6a21`, R-017 `81213fc`, R-018 `f759151`, R-019 `d71943b`, R-020
`d1739b4`, R-021 `a4c5934`, R-022 `d920e92` (+ host closure), R-023 `6edbdf2`,
R-024 `1248b69` (corrections cleared at `6dd0196`).

## 8. Unexecuted / blocked (with reasons)

| Item | Status | Reason |
|------|--------|--------|
| CI jobs (Part A of offline install) | unexecuted | no external CI execution on this host; deviation list recorded |
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
The release candidate is `0123cdc29464`; the branch tip later moved for
verification addenda (`reviews.md` R-024 addendum) without changing the shipped
code paths.
