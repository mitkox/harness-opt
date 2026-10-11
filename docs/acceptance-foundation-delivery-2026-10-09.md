# Foundation Acceptance Matrix

Date: 2026-10-09.
Delivery branch: `esf/hop-1.0`.
Branch tip measured: `f5dc9ed03d8eae1de9d0fdadb39fae4e6e73369a`.
Historical baseline record: `4e6d6a243e5f46b80318fb35981251ef0392d200` (2026-10-01).
Work items: HOP-R01-R05. Overall gate: **BLOCKED / PARTIAL DELIVERY**.
No M4-M10 implementation and no release qualification is claimed. The release
gate is re-run at the freeze commit; this matrix is a working-branch record.

Every row below states whether it was executed in this pass. Cube rows were
executed by `factory-delivery` and are cited with their run id and artifact
path; they are not re-labelled as this reviewer's runs. Nothing was published,
pushed, merged, or deployed.

## Results (executed at tip `f5dc9ed`)

Commands run from the delivery worktree with the pinned offline environment
(`HOP_WHEELHOUSE=/home/mitko/dev/harness-opt/.vendor/wheels scripts/dev bootstrap`).

| Gate | Command | Observed Result |
|---|---|---|
| Full non-live regression | `scripts/dev test --suite all` | exit 1: **17 failed, 270 passed, 1 deselected, 7 errors** - all red nodes namespace-dependent (see Environment Classification) |
| Unit suite | `scripts/dev test --suite unit` | exit 0: **203 passed, 92 deselected** |
| Contract suite | `scripts/dev test --suite contract` | exit 1: **1 failed** (`test_pi_adapter.py::test_prompt_acceptance_is_not_completion`, bwrap), 14 passed, 280 deselected |
| WP-0.4 focused regressions | `PYTHONPATH=src .venv/bin/python -m pytest tests/test_foundation_fresh_clone.py tests/test_foundation_migration.py tests/test_m2_cli.py::test_migration_keeps_m1_runs_readable tests/test_rename_compat.py::test_historical_m1_runs_readable -o addopts= -q` | exit 0: **9 passed** |
| Static checks | `scripts/dev check` | exit 3: **BLOCKED** - host `ruff` build does not match the approved pin; approved offline `mypy` is not pinned (`status=blocked`), no PATH substitute accepted (HOP-R02) |
| Lint | `ruff check src tests scripts/dev scripts/measure_foundation.py` | exit 0: All checks passed (host ruff 0.16.10) |
| Formatting | `ruff format --check src tests scripts/dev scripts/measure_foundation.py` | exit 0: 84 files already formatted |
| CLI help | `./hop --help` | exit 0 |
| Nested CLI help | `./hop profile --help` from the worktree and from `/tmp` | exit 0, usage printed |
| Measurement utility | `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/measure_foundation.py --baseline <pinned 4e6d6a2 checkout>` | exit 0; baseline source bytes/lines/sha256 reproduce the 2026-10-01 record exactly |

## Cube Gate Results (executed by `factory-delivery`)

| Run | Revision | Result |
|---|---|---|
| `hop-gate-baseline-4ba5b1a3` | `4ba5b1a` | Factory `VERIFICATION_FAILED`; `pytest-nonlive` exit 1 with **18 failed + 7 errors**. Environment-classified (see below). Artifact: `.factory/runs/hop-gate-baseline-4ba5b1a3/`, summary in `.factory/hop-v1/evidence.md` |
| `hop-gate-diag-m1` | `4ba5b1a` | exit 0: the two M1-evidence tests **PASSED** (`test_migration_keeps_m1_runs_readable`, `test_historical_m1_runs_readable`). Confirms the WP-0.1 expected-RED premise does not hold at this revision. Artifact: `.factory/hop-v1/diag-two-tests.toml` |

`./bin/factory sandboxes` reported no remaining sandboxes after both runs
(`CleanupVerified — NoSandboxesRemain`); no writes were made to
`/home/mitko/dev/harness-opt`.

## Environment Classification

Both red sets are attributed to the execution environment, not to product
behavior, and neither is hidden or converted into a pass:

1. **Cube workspace on `tpl-eb9b6ee957064ac09b80cd8e`: `bwrap` not installed.**
   HOP's hardened runner fails closed with `hop.sandbox.SandboxUnavailable:
   bwrap missing; refusing to run unisolated`, producing the runner/verification/
   inference/boundary failures and `error_class='verifier_sandbox_unavailable'`.
   `bwrap` is installable in this template and the sandbox-marked suite is green
   there once installed (independent-verifier task-5); the gate workspace had not
   installed it.
2. **Cube template interpreter CPython 3.12.12 vs the CPython 3.14/Linux
   x86-64 wheelhouse.** The three `test_foundation_bootstrap.py` failures follow
   directly from the offline wheelhouse refusing a non-3.14 interpreter.
3. **Host (this session): user namespaces denied.** `bwrap` is present but
   `apparmor_restrict_unprivileged_userns=1` and `unshare --user` fails, so the
   agent's outer sandbox denies namespace creation. This is the same class of
   limitation recorded historically ("sandbox integration required execution
   outside the tool's outer sandbox").

Fixing (1) and (2) belongs to the delivery tooling and boundary work items
(WP-0.2/WP-1.2/WP-1.4). No sandbox test was weakened to obtain a green result.

## Measurements

Reproduce the historical baseline and the current tip:

```sh
# Pinned historical baseline (4e6d6a2), extracted read-only into a scratch dir:
git archive 4e6d6a243e5f46b80318fb35981251ef0392d200 | tar -x -C <scratch>
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/measure_foundation.py --baseline <scratch>
```

Raw samples, hashes and the full remeasurement block: `docs/evidence/foundation/measurements.json`.

| Category | Historical (2026-10-01, `4e6d6a2`) | Re-measured baseline (`4e6d6a2`, 2026-10-09) | Current tip (`f5dc9ed`) |
|---|---:|---:|---:|
| Python source bytes | 353,395 | 353,395 | 383,408 |
| Python source lines | 8,342 | 8,342 | 10,390 |
| Source sha256 | `df79e9a9...` | `df79e9a9...` | `409da094...` |
| Help startup median | 26.40 ms | 29.15 ms | 30.05 ms |
| Profile compile median | 7.44 ms | 7.27 ms | 7.27 ms |
| First trajectory page median | 433.95 ms | 482.01 ms | 90.63 ms |
| Trajectory peak Python allocation median | 47,143,569 B | 47,139,841 B | 1,266,964 B |

Source bytes/lines/sha256 reproduce the historical baseline exactly, which is
the reproducibility check for this pass. Wall-clock medians differ by host load
and patch interpreter version (CPython 3.14.4 historically, 3.14.8 now); they
are not a model-speed or production-performance claim. The trajectory case
returns 100 of 10,000 events while validating the whole chain and remains
O(history) in CPU time; it is not an indexed random-access claim.

The historical `runtime_installation` block (allocated environment bytes and
locked package counts) is retained in `measurements.json` and was **not
re-measured** in this pass.

## Unexecuted in This Pass

| Not executed | Reason |
|---|---|
| All namespace-dependent tests (sandbox marker): the 17 host failures + 7 fixture errors, and the Cube 18 failures + 7 errors | No usable user namespaces in either environment used in this pass: the host denies them via AppArmor, and the default Cube workspace does not install `bwrap` (installable there). Re-run required on a namespace-capable workspace/template. |
| `scripts/dev test --suite live` / installed-Pi contract | Owner input O2 (loopback local model) absent; Pi version requalification is owned by HOP-R04/task-9. Historical observation (Pi 1.0.0 vs the 0.85.1 assertion) remains unrequalified. |
| `scripts/dev check` type checking | Approved offline `mypy` artifact not available (HOP-R02, owner input O1). Recorded as BLOCKED, exit 3 - not a pass. |
| `HOP_WHEELHOUSE=... scripts/dev bootstrap --run-tests` | Not re-run in this pass. On the current environments it cannot pass, because it runs the full non-live suite (same namespace limitation); the historical 2026-10-01 pass is retained as history, not as a current result. |
| Runtime-only bootstrap (`--runtime`) | Historical row retained; not re-run in this pass. |
| Independent security review | Not performed. Required by HOP-R01/R04 before any closure of the three open boundary findings. |
| Full local-model runs, external observability services, extra adapters, signing, promotion | Out of scope for this foundation pass. |

## Outstanding Exit Criteria

* HOP-R01/R04: independent review and closure of worker egress, aggregate
  descendant resource limits, and current-case verifier isolation. All three
  remain **OPEN** in `docs/foundation-review.md`; the independent-review block
  there is unsigned.
* HOP-R02: approved offline `mypy` artifact and full transitive development
  lock; `scripts/dev check` must exit 0 offline.
* HOP-R03: typed request/result/error interfaces, consistent diagnostics across
  legacy handlers, and early-admission terminal-evidence ownership.
* HOP-R04: automated pending-write recovery remains unqualified; incomplete
  evidence is retained and rejected rather than silently repaired.
* HOP-R05: renewed M0-M3 acceptance on a qualified Pi/local-model host.

Next work is foundation closure. AOP-020 remains the next roadmap root only
after foundation acceptance. All later original acceptance criteria remain in
`BACKLOG.yaml`; scoped historical delivery does not erase deferred requirements.
