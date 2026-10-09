# HOP 1.0.0 release checklist (gates G1–G11)

The canonical gate list from the approved release plan, recorded here as the plan
file. Every gate is marked with its evidence pointer; **no gate is claimed without a
run**. Status vocabulary: PASS (evidence exists), PASS with an open sub-item, IN
PROGRESS (not yet claimable).

| Gate | Requirement | Status | Evidence pointer |
|------|-------------|--------|------------------|
| **G1** | Foundation accepted: HOP-R01–R05 exit criteria met | PASS | `docs/acceptance-foundation-renewal.md`; gate `hop-gate-task10-2468b04`; reviews R-004/R-009/R-010 (R-008 is the **pre-fix defect record, NOT CONFIRMED**, retained as history) |
| **G2** | `scripts/dev check` exit 0 with the pinned offline toolchain | PASS | `development-tools.lock.json`; `lead-1.0-verify.log` at `0123cdc` (`All checks passed!`, `Success: no issues found in 73 source files`, `check_exit=0`) |
| **G3** | Full non-live suite green on a fresh clone and inside Cube | PASS | host `551 passed, 1 deselected`, `suite_exit=0` at `0123cdc` (lead log); Cube `lead-tip-0123cdc` (hop-v1 6/6) and `hop-gate-task32-674b991` (sandbox 99/0/0/0) |
| **G4** | Boundary findings closed and independently reviewed | PASS | egress broker R-010 (`105fba5`); cgroup quotas + verifier isolation R-009 (`1beb23d`); ADR-012/013 and ADR-005 revision; internal boundary-design study and Cube capability record (not published) |
| **G5** | M4 packs AOP-020/021/022/025 pass passing/failing/inconclusive cases | PASS | R-019 (020 halves, `d71943b`), R-020 (021, `d1739b4`), R-015 (022, `45d8a2e`), R-017 (025, `81213fc`); gates `hop-gate-task22-45d8a2e`, `hop-gate-task32-674b991`, `wp24-deps-1` |
| **G6** | M10 hardening AOP-049–054 re-scoped | PASS | R-012 (049, `53f2084`), R-018 (050, `f759151`), R-021 (051, `a4c5934`), disconnected profile (052, internal disconnected-operation record (not published) + `hop-gate-20261009-190107-3-disconnected`), `hop-gate-task31-4cfb317` (053), R-024 (054, `1248b69`) |
| **G7** | ESF end-to-end with a real model-backed agent produces a patch that passes gates | PASS | `wp03-live-Bgreen-ec83c49`, `wp-live-tip-4f651c9`, `live-tip-1248b69` |
| **G8** | Live Pi contract requalified + one full local-model run outcome/verdict pass | PASS | `run-58f5589ef776` (pass/pass, Pi 1.1.0, `qwen-flash-next-IQ2_S_-_2.5_bpw`) in `docs/evidence/1.0/live-model-acceptance.md`; Pi 1.1.0 requalification task-19. The additional `scripts/dev test --suite live` green claim is **reported by the Lead and not independently re-run in this pack** |
| **G9** | Offline install (sdist/wheel + wheelhouse) into a clean venv with egress denied | PASS with an open sub-item | internal artifact-qualification record (commit `777d753`, not published): offline fresh-venv install of `hop-1.0.0` from the wheel with `--no-index --require-hashes`, **zero downloads**, exactly one console script, `pip check` clean, and every migration exit code observed from the installed artifact (`legacy_env_removed` exit 2 naming `HOP_RUNS_DIR`; `legacy_command_removed` exit 2 with rewrite hints; `aop` absent; `AOP_PI_BASE_URL` scrub active). **Published-candidate artifacts (release/1.0.0): wheel `42439320855807682c7bd1b63bb0aa8d6a599d682ea0786b05a605a87644aee6` (80 entries, includes `hop/evaluation/legacy.py`), sdist `326800aac8e4f80ad9b3ad519efe2cf421660b08495987d00f08f814d4de543e` (ships `docs/migration-1.0.md`, `CHANGELOG.md`, `README.md`; deliberately omits `docs/release-1.0.0.md` so the archive cannot cite its own hash). Both re-qualified offline in fresh venvs on the release branch and they supersede the frozen-revision pair (wheel `42439320…`, sdist `1c8769cbd3e1a98412fd7a763d3e45533d34bc1b3b07aa62170025d1275ff0bd`) and the pre-task-51 artifacts for publication.** **Reproducibility (R-029, independently confirmed by independent-verifier):** the wheel is byte-reproducible only with `SOURCE_DATE_EPOCH=1700000000` (two builds byte-identical `42439320…`); plain rebuilds 4 s apart differ only in embedded zip timestamps while all 80 per-entry sha256 values are equal; the sdist digest is recorded as-built. Open sub-item: the wheel omits `docs/` (D-2). Also `hop-gate-20261009-190107-3-disconnected`, internal disconnected-operation and offline-install records (not published) (Part B executed, Part A CI unexecuted) |
| **G10** | Docs coherent; deprecations removed and migration-tested | PASS | `0123cdc`; `tests/test_deprecation_removal.py` (12 tests collected); **no historical file under `docs/evidence/foundation/` or `docs/acceptance-foundation.md` is modified or deleted anywhere on the branch — the branch only ADDS new files** (the 1.0 pack, the new remeasurement and delivery-matrix files). Proven by `git diff --numstat 4ba5b1a..HEAD -- docs/evidence/foundation docs/acceptance-foundation.md` showing additions only and `git diff --diff-filter=DM --name-only 4ba5b1a..HEAD -- docs/evidence/foundation docs/acceptance-foundation.md docs/acceptance-m1.md docs/acceptance-m2.md docs/acceptance-m3.md specs` being empty (the broad `-- docs/` form legitimately lists four updated non-historical docs, so the claim is scoped to this intent path list) |
| **G11** | Release evidence pack + threat-model sign-off + owner-authorized push/PR/tag | IN PROGRESS | this pack (`docs/evidence/1.0/`), `docs/threat-model.md`, release candidate report; owner authorization for push/PR/tag (task-37/39) has not been given and is not claimed |

## Documented limitations (recorded, not omitted)

From the internal artifact-qualification record §8 (not published). None of these is a
correctness defect in the shipped artifact; they are recorded so the checklist is
not cleaner than reality.

| Id | Limitation | Mitigation / status |
|----|-----------|---------------------|
| **D-1** | A `hop version` verb/flag does not exist, so `hop version` and `hop --version` exit 2 | **Documented limitation / 1.0.1 convenience candidate** (not a defect and not "closed"): the CLI genuinely lacks the verb, while the installed version is unambiguous (`importlib.metadata.version("hop")` → `1.0.0`; wheel `METADATA` `Version: 1.0.0`). Adding the verb is a source change and a 1.0.1 candidate |
| **D-2** | Migration diagnostics name `docs/migration-1.0.md`; the **wheel omits `docs/`** (0 docs entries) | **task-50 complete**: the **sdist ships `hop-1.0.0/docs/migration-1.0.md`** (verified in R-026 and re-checked here), so an sdist user has the guide. The installed **wheel** still lacks it, but the diagnostic prints the replacement spelling, so it is self-contained in practice. Shipping the guide in the wheel (or a fully self-contained diagnostic) remains a **1.0.1 candidate** |
| **D-3** | A removed `AOP_*` variable only fails when HOP actually reads it (e.g. `AOP_RUNS_DIR` exits 2; `AOP_COLLECTOR_DOWN` on the `doctor` path exits 3 with no legacy diagnostic) | The variable is never honoured as configuration (`is_flag_set` raises when called); read-triggered detection is the implemented behaviour. A startup-wide `AOP_*` scan is a **1.0.1 candidate** |
| **D-4** | `hop doctor` is a presence check only, not an execution check | `doctor`'s own note says presence only; execution is proven separately by G8 (`run-58f5589ef776`) and the ESF gate. An execution check is a **1.0.1 candidate** |

## Claims explicitly not made

- Optimization, candidate generation, activation and rollback are **not in 1.0**
  (M5–M9; AOP-024 and AOP-042 out of scope under ADR-011).
- No CI job is green: CI execution is unexecuted with the deviation list recorded.
- No generalization from the single live G8 case (one case, one deployment, one
  harness).
- No hostile-workload qualification beyond the scripted adversaries.
- G11 is not complete until the owner authorizes push/PR/tag; this pack does not
  authorize or perform it.
