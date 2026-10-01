# Foundation Acceptance Matrix

Date: 2026-10-01. Baseline: `4e6d6a243e5f46b80318fb35981251ef0392d200`.
Worktree: `/tmp/hop-foundation`, branch `esf/hop-foundation`.
Work items: HOP-R01-R05. Overall gate: **BLOCKED / PARTIAL DELIVERY**.
No M4-M10 implementation or renewed live release qualification is claimed.

## Results

Commands ran from the isolated worktree using the approved offline wheelhouse
at `/home/mitko/dev/harness-opt/.vendor/wheels`. Shell commands were invoked
through `rtk proxy`. Sandbox integration required execution outside the tool's
outer sandbox, which otherwise prevents local socket binding and namespaces.
This is not disabling HOP's worker sandbox.

| Gate | Command or Regression | Observed Result |
|---|---|---|
| Full non-live regression | `scripts/dev test --suite all` | 278 passed, 1 deselected in 20.38s; exit 0 |
| Fresh offline developer bootstrap | `HOP_WHEELHOUSE=/home/mitko/dev/harness-opt/.vendor/wheels scripts/dev bootstrap --run-tests` | Pinned install, help validation and non-live suite passed; managed launcher switched; exit 0 |
| Runtime-only bootstrap | `HOP_WHEELHOUSE=/home/mitko/dev/harness-opt/.vendor/wheels HOP_LOCK_VENV=.venv-runtime scripts/dev bootstrap --runtime` | 11 pinned packages, including trusted-verifier dependencies; exit 0 |
| Runtime verification | `PYTHONPATH=src .venv-runtime/bin/python -m pytest --noconftest tests/test_verification.py -o addopts= -q` | 17 passed in 1.61s; real verifier isolation, baseline/repair and adversarial cases; exit 0 |
| Lint and formatting | `scripts/dev check` | Ruff lint and formatting pass; type checker unavailable; aggregate exit 3 |
| Measurement utility static checks | `ruff check src tests scripts/dev scripts/measure_foundation.py`; corresponding `ruff format --check` | PASS with pinned Ruff 0.16.8 |
| Installed Pi contract | `scripts/dev test --suite live` | 1 failed, 276 deselected at probe time: observed 1.0.0, historical assertion requires 0.85; exit 1 |
| Nested help from another directory | `/tmp/hop-foundation/hop profile --help` from `/tmp` | PASS; all five groups additionally covered by tests |
| CLI and offline setup regressions | `tests/test_foundation_cli.py`, `test_foundation_bootstrap.py` | Included in 278-pass suite; exclusive init, path precedence, missing model, failed replacement preservation |
| Trust and recovery regressions | `tests/test_foundation_integrity.py`, `test_runner_e2e.py` | Concurrent independent stores, stale workers, corrupt paths/indexes/blobs, pending writes, empty sidecars, spool redelivery, cancellation and terminal store failure |
| Historical readers | `tests/test_foundation_migration.py`, M2/M3 suites | Committed fixtures copied to temporary directories; malformed data remains an error; history is not rewritten |
| Backlog reconciliation | YAML parse plus unique-ID/dependency reference checks | 59 unique items; all referenced dependencies exist; stable AOP IDs retained |
| Historical file preservation | `git diff --numstat -- docs/evidence specs examples requirements.lock AGENTS.md` before new evidence was added | Empty; M1-M3 evidence, schemas, examples and original dependency lock unchanged |
| Original worktree preservation | `git status --short` in original checkout | Only the pre-existing `M AGENTS.md` |
| Diff hygiene | `git diff --check` | PASS |

The fresh-bootstrap success preceded the final runtime-dependency regression
test; the final 278-pass suite covers that additional check. The successful
runtime bootstrap independently validates the corrected dependency split.

## Failed Attempts Preserved

Two earlier fresh-bootstrap attempts encountered real ENOSPC failures: `/tmp`
reached 1,048,576 used inodes while free byte capacity remained. One attempt had
3 failures and 4 setup errors; a retry with test scratch on another filesystem
had 10 APM failures writing checkout-local profile state. Both returned exit 3,
removed only their unpublished candidate environment, and preserved the active
launcher. The APM fixture now supplies a temporary home. After host inode
capacity recovered, fresh bootstrap and the final non-live suite passed.
No unrelated temporary files or historical environments were deleted.

## Measurements

Reproduce with:

```sh
.venv/bin/python scripts/measure_foundation.py --baseline /home/mitko/dev/harness-opt
du -s -B1 /home/mitko/dev/harness-opt/.venv-m1 .venv-runtime/
```

Raw samples and source hashes: `docs/evidence/foundation/measurements.json`.
Same Python 3.14.4 executable, warm local filesystem, ten help timings after
warmup, five profile compilations after warmup, five trajectory samples.
The trajectory case returns 100 of 10,000 events while validating the entire
chain. The old path constructs the full ledger and event list; the new path
retains only a page plus source counters. It remains O(history) in CPU time;
it is not an indexed random-access claim.

| Category | Baseline | Current |
|---|---:|---:|
| Python source bytes | 353,395 | 383,408 |
| Python source lines | 8,342 | 10,390 |
| Runtime environment allocated bytes | 46,616,576 | 42,323,968 |
| Locked runtime packages | 16 including developer packages | 11 including verifier |
| Help startup median | 26.40 ms | 30.58 ms |
| Profile compile median | 7.44 ms | 7.34 ms |
| First trajectory page median | 433.95 ms | 84.02 ms |
| Trajectory peak Python allocation median | 47,143,569 bytes | 1,266,471 bytes |

Source grew with explicit lifecycle records, correctness controls, CLI commands,
and mechanical formatting; this is not a line-count reduction. Installation
shrinks about 9.2%, not the preliminary 26 MB experiment, which incorrectly
omitted the trusted verifier and was corrected before delivery. Environment
size excludes system interpreter targets, source, wheelhouse and retained run
data. Help is modestly slower; compilation is essentially unchanged in this
sample. No inference, GPU, statistical quality or production-speed claim is made.

## Outstanding Exit Criteria

- HOP-R01/R04: independent review and closure of worker egress, aggregate
  descendant resource limits, and current-case verifier isolation findings.
- HOP-R02: approved offline mypy artifact and full transitive development lock.
  The installed Ruff binary is pinned; a portable developer tool distribution
  is not yet complete. No packages were downloaded to hide this gap.
- HOP-R03: uniform typed request/result/error interfaces, consistent diagnostics
  across all legacy handlers, and early-admission terminal-evidence ownership.
- HOP-R04: automated pending-write recovery remains unqualified; incomplete
  evidence is retained and rejected rather than silently repaired.
- HOP-R05: renewed M0-M3 acceptance on a qualified Pi/local-model host. The
  historical matrices are evidence, not a substitute for a current run.
- Full local-model runs, external observability services, additional adapters,
  signing and promotion were not executed. Independent security review has
  not occurred. Nothing was published, pushed, merged or deployed.

Next work is foundation closure, starting with HOP-R02 offline tooling and
HOP-R04 boundary design/review. AOP-020 remains the next roadmap root only after
foundation acceptance. All later original acceptance criteria remain in
`BACKLOG.yaml`; scoped historical delivery does not erase deferred requirements.
