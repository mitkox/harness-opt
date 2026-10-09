# HOP Foundation Review

Historical baseline: `4e6d6a243e5f46b80318fb35981251ef0392d200` (2026-10-01).
Delivery branch: `esf/hop-1.0`.
Branch tip reviewed: `f5dc9ed03d8eae1de9d0fdadb39fae4e6e73369a` (2026-10-09).
Work items: HOP-R01 through HOP-R05.

Scope follow-up: ADR-011 removes enterprise services, signing infrastructure,
and multi-user authorization from the product. The local safety findings below
remain open where marked; historical future-integration references are not
current requirements.

This is the implementer's review and regression evidence at the recorded tip.
It does not constitute independent security verification or renewed M0-M3
release qualification. No M4-M10 implementation and no release qualification is
claimed from this pass; the release gate is re-run at the freeze commit.

## Findings and Disposition

Evidence column distinguishes *verified at this tip* from *historical (run
2026-10-01, not re-executed in this pass)* and *not executable in this pass*
(namespace-dependent tests were blocked by the environment, see below).

| Priority | Finding | Disposition and Evidence |
|---|---|---|
| High | Worker environment denylisting inherited arbitrary shell functions and host configuration. | Fixed: explicit allowlist, hostile-environment boundary regression exists (`tests/security/test_boundaries.py`). Not executable in *this* pass: the test needs user namespaces; the host denies them (`apparmor_restrict_unprivileged_userns=1`) and the default Cube workspace does not install `bwrap`. In Cube eb9 `bwrap` installs and works, so the sandbox-marked suite is executable there once the workspace installs it (independent-verifier, task-5); this pass did not use such a workspace. |
| High | Run admission and attempt fencing used separately locked reads/writes; independent SQLite connections could lose updates. | Fixed: BEGIN IMMEDIATE transactions; concurrent admission and unique-token regressions pass in the 203-passing unit suite at this tip (`tests/test_foundation_integrity.py`). |
| High | Artifact owner/digest paths could escape their store; existing corrupt blobs could acquire new references. | Fixed: identifier/digest/containment checks, symlink-escape and tampered-blob regressions, atomic reference publication; covered by the unit suite at this tip. |
| High | A failed first event write could be read as legacy data without an integrity chain. | Fixed: durable pending marker and atomic chain publication; interrupted writes remain incomplete. Automated pending-write recovery remains unqualified (see HOP-R04). |
| High | Resource-limit setup swallowed errors and used preexec_fn in a threaded process. | Fixed for per-process limits: fresh limit-setting process before exec; failures stop child execution. Aggregate descendant quotas remain a separate open finding. |
| Medium | A collector write followed by a crash before acknowledgement duplicated delivered spans. | Fixed: locked atomic deduplicated local projection; injected acknowledgement-failure regression passes in the unit suite at this tip. |
| Medium | Corrupt profile indexes became empty indexes; replay could fall back from a missing exact lock. | Fixed: explicit corruption errors, strict digest paths, serialized index updates, exact-reference replay; covered by the unit suite at this tip. |
| Medium | Migration crashed on an absent runs directory and classified arbitrary corruption as legacy success. | Fixed, and extended at `f5dc9ed`: an absent or non-directory `--runs-dir` is an expected empty set (exit 0), present-but-corrupt evidence still exits 1 and is never rewritten, and the M1-evidence tests skip with a reason when the tracked fixture is unavailable. Targeted run: 9 passed. |
| Medium | Nested CLI help failed and execution/investigation resolved paths differently. | Fixed: real subparsers, shared Paths. At this tip `./hop --help` and `./hop profile --help` exit 0 from the worktree and from `/tmp`. |
| Medium | Tests required machine-local deployment records and an installed Pi version; a CLI test rewrote a committed lockfile. | Fixed for the test harness: synthetic runner identities, separate live marker, temporary lock output, historical files unchanged. Installed-Pi contract requalification remains open (HOP-R04). |
| Medium | Bootstrap deleted its destination before proving replacement viability. | Fixed: staged offline environments and managed link switch; failure-preservation tests exist. `scripts/dev check` is currently BLOCKED on approved tool artifacts (HOP-R02), not on this behavior. |
| Medium | Completed runs left cancellation polling threads alive. | Fixed: scoped cancellation watch joined on every exit. Cancellation regressions are namespace-dependent and were not executable in this pass; historical run passed them. |
| High / **OPEN** | Pi workers share host networking; endpoint validation is not OS-level general egress enforcement. | **OPEN.** No external-network security qualification is claimed. Closure is owned by task-9 (egress broker design in `docs/adr/ADR-012-worker-egress-broker.md`). |
| High / **OPEN** | RLIMITs are per-process/per-user controls, not aggregate per-run CPU/memory/disk quotas; output truncation occurs after execution. | **OPEN.** Requires resource accounting/enforcement for descendants before hostile-workload qualification. Closure is owned by task-9 (design in `docs/adr/ADR-013-aggregate-resource-quotas.md`). |
| High / **OPEN** | Verifier imports candidate code in the same evaluator process; ADR-005 documents current-case visibility. | **OPEN.** Requires independent threat-model review before any sealed-evaluation claim. Closure is owned by task-9 (revision plan in `docs/adr/ADR-005-revision-plan.md`). No sealed-store implementation was added. |

## Delivery-Branch Baseline (2026-10-09)

### WP-0.4 fresh-clone hygiene — CLOSED

Commit `f5dc9ed`. `scripts/migrate_m1_to_m2.py` treats an absent or
non-directory `--runs-dir` as an expected empty set (exit 0); present-but-corrupt
evidence still exits non-zero and is never rewritten. The two tests that need
historical M1 evidence materialise the committed copy under
`docs/evidence/m1/real-pi-run` and skip with an explicit reason when it is
unavailable. `tests/test_foundation_fresh_clone.py` adds four focused
regressions (absent runs dir, runs path that is a file, corrupt evidence,
end-to-end skip in an evidence-less checkout). Before/after: the two nodeids in
a `docs/`-less checkout gave 2 errors before the change and 2 skips after;
`--runs-dir` pointing at a file gave `NotADirectoryError` (exit 1) before and
`[]` (exit 0) after.

The WP-0.1 premise that the two named tests fail because of the gitignored
`runs/` does **not** hold at `4ba5b1a`: they consume the tracked fixture. This
was confirmed independently inside Cube (run `hop-gate-diag-m1`: both tests
PASSED, exit 0) and on a genuine fresh checkout in the pinned venv.

### Fresh-clone suite status — RED, environmental

The full non-live suite is **not green** at this tip. Both observed red sets are
attributed to the execution environment, not to product behavior:

* **ESF Cube, revision `4ba5b1a`**, template `tpl-eb9b6ee957064ac09b80cd8e`
  (run `hop-gate-baseline-4ba5b1a3`, recorded by `factory-delivery` in
  `.factory/hop-v1/evidence.md`): `pytest-nonlive` exit 1 with **18 failed +
  7 errors**. Root causes: (1) the default Cube workspace does not install
  `bwrap`, so HOP's hardened runner fails closed with `SandboxUnavailable`
  (`bwrap` is installable in the template and the sandbox suite is green there
  once installed -- independent-verifier task-5, probe-2-probe-hop-eb9, 76
  passed); (2) the template interpreter is CPython **3.12.12** while the pinned
  offline wheelhouse is evidenced for **CPython 3.14/Linux x86-64**, producing
  the three `test_foundation_bootstrap.py` failures. Neither root cause is a
  product defect; both are gate-environment/toolchain gaps owned by WP-0.2b and
  WP-1.2.
* **Host (this session), tip `f5dc9ed`**: `scripts/dev test --suite unit` is
  green (203 passed, 92 deselected, exit 0). `--suite contract` is 1 failed /
  14 passed and `--suite all` is 17 failed / 270 passed / 7 errors; every
  failure traces to the same missing-namespace condition. `bwrap` exists on the
  host but `apparmor_restrict_unprivileged_userns=1` and `unshare --user` fails,
  so the agent's outer sandbox denies user namespaces. The historical acceptance
  note that sandbox integration must execute outside the tool's outer sandbox
  still applies.

No sandbox test was weakened to obtain a green result. A present-but-unusable
`bwrap` currently surfaces as a task FAIL (`verification_no_result`) rather than
an infra error; that classification issue is owned by task-9 and is **not**
fixed here.

## Boundary Findings — OPEN until task-9

Status of the three HOP-R04 records above: **OPEN**. They stay open until the
implementations land, their negative/adversarial tests fail before and pass
after, and independent review confirms the enforcement actually in effect.
Design inputs already committed: `docs/adr/ADR-012-worker-egress-broker.md`,
`docs/adr/ADR-013-aggregate-resource-quotas.md`,
`docs/adr/ADR-005-revision-plan.md`, and
the internal boundary-design study (task-11, not published). No enforcement is claimed.

### Independent review of the boundary records

Status: **SIGNED** — `independent-verifier`, 2026-10-09, revision `f5dc9ed`
(review text `5006cf7`; full detail in the internal review index, R-004, not published).
The reviewer independently reproduced the OPEN dispositions, confirmed
`git diff 4ba5b1a..f5dc9ed -- src/hop` and the boundary tests are empty, and
required one evidence-text clarification (which is applied above).

```
Reviewer: independent-verifier (task-5 / task-9 independent verification)
Date: 2026-10-09
Revision reviewed: f5dc9ed03d8eae1de9d0fdadb39fae4e6e73369a
Verdict: [x] agree   [ ] disagree
```

Attestation (verbatim):

> Reviewed as independent-verifier at f5dc9ed03d8eae1de9d0fdadb39fae4e6e73369a
> (review text 5006cf7): all three High boundary findings are correctly OPEN
> with no enforcement claimed that is not in effect, the bwrap
> present-but-unusable misclassification and the three closures are correctly
> assigned to task-9, and no boundary test was weakened — subject to the single
> evidence-text clarification above.

Carried to task-9 from the same review (internal review index, R-002, not published):

* ADR-012 §Decision 1 references `ATTEMPT_KIND == "agent"`, which does not exist
  anywhere in `src/hop` at `f5dc9ed`; task-9 must introduce the guard or name the
  real enforcement point.
* ADR-013's "expected local outcome is unavailable until the Cube template is
  probed" now has its probe: Cube `eb9` and `e0b` both have a writable, fully
  delegated cgroup v2 root. The blocked path remains correct for the host, but
  must not be used to describe the Cube gate environment.

## Review Coverage

Contracts and identity, native Pi interfaces, run state and cancellation,
sandbox environment/network/process boundaries, frozen verification, artifact
and profile storage, trajectory integrity, telemetry projection/redaction,
deterministic resolution/compiler/APM export, CLI, setup, tests, and milestone
evidence were inspected. At this tip the 203-passing unit suite exercises
compiler determinism, policy merging, tampering, redaction, verifier-bypass
attempts, storage recovery, and export exclusions. Full-file model
fingerprints, additional harness qualification, signing, and production
promotion remain assigned to later milestones.

## Remaining Dependencies

* HOP-R02 needs the approved offline type-checker artifact and dependency lock.
  `scripts/dev check` exits 3 (BLOCKED) at this tip: the host `ruff` build does
  not match the approved pin and no approved `mypy` is configured. No PATH
  substitute is accepted.
* HOP-R03 (typed lifecycle and uniform diagnostics) is not implemented in this
  pass.
* HOP-R04 needs closure of the three open boundary findings plus independent
  review.
* Current Pi/local-model acceptance is outstanding; no local model endpoint
  (owner input O2) was available, so no live inference or live qualification
  was executed.
* The release gate is re-run at the freeze commit; nothing here is a release
  claim. No optimization, promotion, provider writes, public scans, or
  deployment were performed.
