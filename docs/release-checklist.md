# HOP 1.0.0 release checklist (gates G1–G11)

The canonical gate list from the approved release plan, recorded here as the plan
file. Every gate is marked with its evidence pointer; **no gate is claimed without a
run**. Status vocabulary: PASS (evidence exists), PASS with an open sub-item, IN
PROGRESS (not yet claimable).

| Gate | Requirement | Status | Evidence pointer |
|------|-------------|--------|------------------|
| **G1** | Foundation accepted: HOP-R01–R05 exit criteria met | PASS | `docs/acceptance-foundation-renewal.md`; gate `hop-gate-task10-2468b04`; reviews R-004/R-008/R-009/R-010 |
| **G2** | `scripts/dev check` exit 0 with the pinned offline toolchain | PASS | `development-tools.lock.json`; `lead-1.0-verify.log` at `0123cdc` (`All checks passed!`, `Success: no issues found in 73 source files`, `check_exit=0`) |
| **G3** | Full non-live suite green on a fresh clone and inside Cube | PASS | host `551 passed, 1 deselected`, `suite_exit=0` at `0123cdc` (lead log); Cube `lead-tip-0123cdc` (hop-v1 6/6) and `hop-gate-task32-674b991` (sandbox 99/0/0/0) |
| **G4** | Boundary findings closed and independently reviewed | PASS | egress broker R-010 (`105fba5`); cgroup quotas + verifier isolation R-009 (`1beb23d`); ADR-012/013 and ADR-005 revision; `docs/verification/boundary-design.md`, `cube-capabilities.md` |
| **G5** | M4 packs AOP-020/021/022/025 pass passing/failing/inconclusive cases | PASS | R-019 (020 halves, `d71943b`), R-020 (021, `d1739b4`), R-015 (022, `45d8a2e`), R-017 (025, `81213fc`); gates `hop-gate-task22-45d8a2e`, `hop-gate-task32-674b991`, `wp24-deps-1` |
| **G6** | M10 hardening AOP-049–054 re-scoped | PASS | R-012 (049, `53f2084`), R-018 (050, `f759151`), R-021 (051, `a4c5934`), disconnected profile (052, `docs/verification/disconnected-operation.md` + `hop-gate-20261009-190107-3-disconnected`), `hop-gate-task31-4cfb317` (053), R-024 (054, `1248b69`) |
| **G7** | ESF end-to-end with a real model-backed agent produces a patch that passes gates | PASS | `wp03-live-Bgreen-ec83c49`, `wp-live-tip-4f651c9`, `live-tip-1248b69` |
| **G8** | Live Pi contract requalified + one full local-model run outcome/verdict pass | PASS | `run-58f5589ef776` (pass/pass, Pi 1.1.0, `qwen-flash-next-IQ2_S_-_2.5_bpw`) in `docs/evidence/1.0/live-model-acceptance.md`; Pi 1.1.0 requalification task-19. The additional `scripts/dev test --suite live` green claim is **reported by the Lead and not independently re-run in this pack** |
| **G9** | Offline install (sdist/wheel + wheelhouse) into a clean venv with egress denied | PASS with an open sub-item | artifacts `dist/hop-1.0.0-py3-none-any.whl` sha256 `9acc72e73db5653b3e01ba89ec1d89f6b95fa310b2e81c269574a2efe18edaf8`, `dist/hop-1.0.0.tar.gz` sha256 `a3a26569c3b1bfa798d7bf73336ab12cb9e5fbd4787f26b6c415227ea1353ffa` (`docs/release-1.0.0.md`); `hop-gate-20261009-190107-3-disconnected` + `docs/verification/disconnected-operation.md`; `docs/verification/offline-install.md` Part B executed, Part A (CI) unexecuted. **task-48 artifact qualification is in progress and is not claimed** |
| **G10** | Docs coherent; deprecations removed and migration-tested | PASS | `0123cdc`; `tests/test_deprecation_removal.py` (12 tests collected); `git diff --numstat -- docs/evidence docs/acceptance-m1.md docs/acceptance-m2.md docs/acceptance-m3.md specs` empty |
| **G11** | Release evidence pack + threat-model sign-off + owner-authorized push/PR/tag | IN PROGRESS | this pack (`docs/evidence/1.0/`), `docs/threat-model.md`, release candidate report; owner authorization for push/PR/tag (task-37/39) has not been given and is not claimed |

## Claims explicitly not made

- Optimization, candidate generation, activation and rollback are **not in 1.0**
  (M5–M9; AOP-024 and AOP-042 out of scope under ADR-011).
- No CI job is green: CI execution is unexecuted with the deviation list recorded.
- No generalization from the single live G8 case (one case, one deployment, one
  harness).
- No hostile-workload qualification beyond the scripted adversaries.
- G11 is not complete until the owner authorizes push/PR/tag; this pack does not
  authorize or perform it.
