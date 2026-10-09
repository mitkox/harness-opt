# ADR-005 revision plan: candidate-process isolation for the trusted verifier

Date: 2026-10-09.

Status: **plan (proposed)** — revision of the accepted
[ADR-005](ADR-005-verifier-isolation.md) for HOP-R04; not yet implemented.
ADR-005 remains the accepted baseline until task-9 lands this change and an
independent reviewer confirms the boundary.

Closes finding: *"Verifier imports candidate code in the same evaluator process;
ADR-005 documents current-case visibility"* (`docs/foundation-review.md`,
High/Open).

## Why ADR-005 is not sufficient

ADR-005 correctly replaced exit-status verdicts with a machine-readable report
and made candidate `conftest.py` / `pytest.ini` / `sitecustomize.py` irrelevant.
It explicitly accepted one residual limitation: candidate code imported by the
hidden tests can read the current case's hidden tests, because those tests must
be mounted for the run to execute the candidate.

Reading the current case is the documented part. The same-process design also
creates **write-side** authority problems that ADR-005 does not document and
that are not covered by adversarial tests:

1. `run_verification` passes the report path in `sys.argv` to the bootstrap
   (`[snapshot, tests_dir, report, vbin, *test_files]`), and the report lives in
   `out_dir`, which is mounted read-write into the confined verifier namespace.
   Candidate code imported by a hidden test can read `sys.argv`, learn
   `report_path`, write a forged all-`passed` report, then call `os._exit(0)`.
   The parent then finds an existing, well-formed report and `evaluate_report`
   can return `PASS`. The existing `test_os_exit_zero_is_not_pass` covers the
   no-forgery variant only. This is a source-level analysis of the current code
   path, not an executed exploit; task-9 must confirm it with the RED regression
   in §"Required regression" before claiming the fix closes it.
2. Candidate code shares the pytest interpreter, so it can mutate the
   verifier-owned plugin state, `_STATE`, pytest hooks, and `sys.modules` before
   the report is written.
3. Candidate code can read the verifier scaffold (`vbin` is mounted read-only
   but present), including the bootstrap and plugin source, and can discover
   `tests_dir`.

Namespace isolation around *both* parties does not fix this: the candidate is
inside the same process as the report authority. The boundary must be a process
boundary between the trusted report authority and candidate execution.

## Decision (proposed)

1. **Two processes, one frozen snapshot.**
   * **Verifier host (trusted).** Runs `python -I` with the verifier-owned
     pytest config and plugin. It contains `expected_tests`, the collection
     policy, and the report authority. It **never imports candidate code**.
   * **Candidate worker (untrusted).** A separate process in its own `bwrap`
     mount/PID/network namespace (`--unshare-net`), which mounts the frozen
     candidate snapshot read-only, a private scratch, and nothing else —
     specifically **not** the hidden-test directory, not the verifier scaffold,
     and not the report directory.
   * The frozen snapshot digest (`freeze_workspace` manifest + `hash_tree`) is
     recorded before either process starts; the worker's mount is read-only, so
     imported bytes must match the recorded digest.

2. **Candidate execution moves behind a versioned RPC.**
   The hidden tests remain the evaluator's pinned pytest files and are not
   rewritten. The verifier host installs a `sys.meta_path` finder that resolves
   candidate module names to **proxy modules**: attribute access, calls,
   iteration, and exceptions are serialized as JSON over a UNIX socket to the
   candidate worker, which imports the real module from the frozen snapshot and
   returns results. The hidden tests keep their existing imports and assertions.
   * A worker crash, timeout, protocol violation, or unserializable result is a
     **test failure** (or `infra_error` for evaluator faults), never a PASS.
   * `os._exit(0)` in candidate code now kills only the worker; the host records
     the test as failed and continues, so it cannot suppress other tests.
   * RPC limits (message bytes, per-call timeout, total worker wall clock) reuse
     the existing `SandboxSpec`/`timeout_s` controls.

3. **Report authority stays in the host.** The report is written only by the
   verifier-owned plugin in the host process, to a path the candidate worker has
   no mount for. `evaluate_report` and the pinned-manifest checks are unchanged.

4. **Isolation mode is probed and recorded.** Preferred mode is *nested*:
   the verifier host runs inside its own `bwrap` namespace and spawns the
   candidate worker with a nested `bwrap`. Where nested unprivileged user
   namespaces are unavailable, the recorded fallback is *host-driver*: the
   trusted host runs as a control-plane process (still `python -I`,
   verifier-owned config, still importing no candidate code) and spawns the
   single-level `bwrap` candidate worker. `probe_nested_user_namespaces()`
   decides; the chosen mode is written to `verification.json` and
   `manifest.json`. A run must never claim *nested* when the fallback was used.

5. **Current-case visibility becomes an explicit negative guarantee.**
   After this revision, candidate code cannot read the current case's hidden
   tests, cannot read the verifier scaffold or report path, and cannot write the
   report. ADR-005's Consequences bullet that accepts current-case visibility as
   a limitation is replaced by this guarantee plus the residual risks below.

## Capability checks (exact)

| Check | Interface | Pass condition | Fallback |
|---|---|---|---|
| bwrap available | existing `sandbox.bwrap_available()` | present | `verifier_sandbox_unavailable` |
| net namespace | existing verifier spec | `--unshare-net` created | `infra_error`; never shared net |
| nested user namespace | `probe_nested_user_namespaces()` | nested `bwrap` probe exits 0 | `mode="host-driver"` recorded |
| worker mounts exclude hidden material | `contamination_check` + mount assertions | hidden dir/report path absent from worker view | `infra_error` |
| snapshot frozen | `freeze_workspace` manifest/hash | worker's imported bytes match manifest | verdict rejection |
| RPC protocol | versioned `hop.verifier_rpc` | host/worker versions match | `infra_error` (`verifier_rpc_mismatch`) |

## Required regression: current-case visibility (exact)

`tests/security/test_verifier_candidate_isolation.py::test_candidate_cannot_read_current_case_hidden_tests`

Test shape (fails before this change, passes after):

1. Freeze a benchmark snapshot and run verification with an added candidate
   module that, on import, tries to locate the current hidden case:
   * reads `sys.argv` and any environment for a path containing `hidden`;
   * walks `/`, the snapshot, and the cwd for names matching
     `test_hidden_*.py` and the known test-file basenames/hashes;
   * reads any discovered file and, if found, creates a marker file with its
     content.
2. Assert the marker is absent, and assert the candidate reported "not found"
   through a value the hidden tests observe.
3. Assert the verdict is still derived from the real hidden-test outcome (the
   baseline benchmark must still FAIL).
4. Assert the report path and verifier scaffold are absent from the candidate's
   view, and that a forged `report.json` written by candidate code does not
   change the verdict (`test_candidate_cannot_forge_report`).

These are the negative/adversarial tests that must be RED before task-9's
verifier change and GREEN after; the full test inventory is in
`docs/verification/boundary-design.md`.

## ADR-005 text changes to apply (task-9 owns `docs/adr/` for this work)

* Keep Context items 1–3; add item 4: *the candidate ran in the same process as
  the report authority, so candidate code could discover the report path from
  `sys.argv`, forge a report, mutate plugin state, and `os._exit(0)`.*
* Replace the Decision bullet *Namespace isolation* with the two-process model
  above (host + candidate worker), including that the hidden tests are executed
  by the host and candidate code is reached only through the RPC proxy.
* Add Decision bullets for: candidate worker mount set (snapshot ro, scratch,
  nothing evaluator-owned); report path unreachable from the worker; mode probe
  and recorded mode.
* Replace the Consequences bullet beginning "Candidate code can still read the
  current case's hidden tests" with the closed guarantee and the residual risks
  below.
* Bump the verifier revision identifier (`debug-verifier-v2` →
  `debug-verifier-v3`) and record it in `verification.json`; historical
  `verification.json` files keep v2 and stay readable.

## What remains unenforced / residual risk

* **Same process family, same uid.** The host and worker share the invoking
  user. A kernel/nested-namespace escape is out of scope (M5+/M10, unchanged).
* **Proxy fidelity.** The RPC proxy mirrors the candidate module surface used by
  the pinned hidden tests; an API shape the proxy cannot represent is an
  `infra_error`, not a silent PASS. Hidden tests must not be rewritten to use
  richer types without extending the versioned protocol.
* **Host-driver fallback.** In that mode the trusted host is not itself
  namespace-confined (only the candidate worker is). This is recorded; it is
  weaker than nested mode and must not be reported as nested.
* **Timing/side effects.** Candidate code still executes real code with real
  side effects inside its scratch and ro snapshot; the worker has no network and
  no evaluator mounts, but it can consume its own runtime. Quotas from ADR-013
  apply to the worker.
* **Sealed evaluator storage** (AOP-020) remains future work; this revision
  removes the current-case leak, it does not create an access ledger.

## Consequences

* A forged report, plugin mutation, `os._exit(0)`, or import-time probe can no
  longer influence the verdict; the verdict authority is a process the candidate
  cannot reach.
* The verifier gains a small RPC transport and a proxy finder (stdlib), plus a
  mode probe; hidden test files and pinned `verifier.json` manifests are
  unchanged.
* Baseline/repair behaviour, collection policy, contamination check, and
  evidence digests are preserved; `os._exit(0)` and forged-report regressions
  become explicit and permanent.
