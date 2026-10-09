# Threat model — M0/M1 trust boundaries (AOP-003, revised after M1 review)

Scope: one local owner (ADR-011). The boundaries below describe the delivered
M1 slice with foundation corrections; they are not full hostile-workload
qualification. Current open findings are tracked in foundation-review.md.

Revision 2026-10-09 (HOP-R04): ADR-012 (worker egress broker), ADR-013
(aggregate cgroup v2 quotas), and the ADR-005 revision plan (verifier
candidate-process isolation) are **approved designs, not implemented code**.
This document separates what the baseline code enforces from the approved target
boundary, marks superseded baseline wording instead of rewriting it, and states
the evidence required before any target control may be cited as enforcement.
Until the implementing work-items land (worker egress and Pi requalification in
WP-1.4d; verifier candidate isolation and aggregate quotas in WP-1.4c) and the
named probes pass on the evidenced host, the marked baseline text remains the
honest description of runtime behaviour. No hostile-workload qualification is
claimed.

## Principals and processes

Baseline (delivered slice at commit `4ba5b1aa67607e2723ca8bb8644fd8772740d26b`):

| Principal | Identity | Filesystem (namespace view) | Network | Credentials |
|---|---|---|---|---|
| Control plane / CLI (`hop`) | invoking user | repo + `runs/` ledger | loopback inference only (`policy.assert_local_url`) | user env, never forwarded |
| Agent worker (Pi) | `bwrap` mount/PID namespace | run root + pinned harness/Node runtime (ro) + `/tmp`; **no** repo, `.hidden`, or other runs | host network shared; only inference URLs are restricted; general egress remains open | none |
| Trusted verifier | `bwrap` mount/PID namespace | frozen snapshot (ro) + one hidden case (ro) + verifier bootstrap (ro) + scratch out | none (`--unshare-net`) | none |

> **Superseded by approved design (2026-10-09).** The *Agent worker (Pi)* Network
> cell above ("host network shared") and the single-process *Trusted verifier*
> row describe runtime behaviour at the baseline commit. ADR-012 replaces the
> network cell with the target below; the ADR-005 revision splits the verifier
> row into a trusted host and a candidate worker. Baseline text is retained as
> history and is **not** an enforcement claim for those target controls.

Approved-design principals (ADR-012, ADR-013, ADR-005 revision). These rows are
the operative model task-9 implements; none of them is in effect until the
probes in §"Approved boundary designs" pass and are recorded:

| Principal | Identity | Filesystem (namespace view) | Network | Credentials |
|---|---|---|---|---|
| Inference broker (`hop.broker`, control plane) | invoking user | run root + broker socket (`0600`) | loopback to the attested model endpoint only | per-run token registry (issuer) |
| Agent worker (Pi) — target | `bwrap` mount/PID/net namespace | run root (rw) + pinned harness/Node runtime (ro) + broker socket dir (rw) + `/tmp`; **no** repo, `.hidden`, or other runs | none (`--unshare-net`); inference only through the broker `AF_UNIX` socket with a per-attempt bearer token | per-attempt token (held by the worker it authenticates) |
| Verifier host (trusted) — target | invoking user; `python -I` | snapshot (ro), hidden case (ro), verifier scaffold (ro), report dir (rw) | none | none |
| Candidate worker (verifier) — target | `bwrap` mount/PID/net namespace | frozen snapshot (ro) + private scratch only; **no** hidden tests, verifier scaffold, or report path | none (`--unshare-net`) | none |

## Boundaries enforced in code

1. **Agent isolation** (`src/hop/sandbox.py`): `spawn_isolated` runs the worker
   under `bwrap` with `--die-with-parent`, `--unshare-pid`, `--unshare-uts`,
   `--unshare-ipc`, rlimits, and a mount namespace that binds only the run
   root (rw) plus explicit read-only binds (system dirs, harness, Node).
   `.hidden/`, the repository, and other runs are absent. Proxies, API keys,
   `SSH_AUTH_SOCK`, and `HOP_PI_BASE_URL`/`AOP_PI_BASE_URL` are scrubbed; `check_no_proxy_leak`
   asserts before spawn. `assert_no_hidden_material` rejects hidden markers and
   symlinks in the workspace. `bwrap` is required; missing `bwrap` fails closed
   (`SandboxUnavailable` -> `infra_error`).
   *Baseline limits:* `--unshare-net` is emitted only when `share_network` is
   false, and the agent path sets `share_network=True`; the applied
   `limit_exec.py` RLIMITs are per-process/per-uid, not aggregate quotas. Both
   are superseded by ADR-012 and ADR-013 respectively (see §"Approved boundary
   designs"); the baseline text is retained as history.
2. **Local-only inference** (`src/hop/policy.py`, `src/hop/inference.py`):
   every endpoint URL must parse to a loopback host; redirects are refused
   (`_NoRedirect`); off-host targets and non-2xx responses fail closed. Pi's
   base URL/model id/context/serving params derive only from the registered
   `ModelDeployment`. `verify_served_model` attests the served model id (and
   weight path when reported) before a run; mismatch is `infra_error`. No cloud
   fallback exists.
3. **Positive verification** (`src/hop/verification.py`, ADR-005): the verdict
   comes from a machine-readable per-test report produced inside the verifier
   namespace, never from a process exit code. Collection must exactly match the
   pinned `expected_tests`, mandatory tests must execute and pass, and
   zero/all-skipped/deselected/collect-only/malformed runs are rejected.
4. **Verifier isolation** (ADR-005): evaluator-owned bootstrap, plugin, and
   `pytest.ini`; `python -I` prevents candidate `sitecustomize`/`usercustomize`
   and cwd import; hidden tests are copied outside the snapshot tree so
   candidate `conftest.py`/`pytest.ini` cannot affect collection.
   *Superseded target:* this baseline still executes candidate code in the same
   evaluator process that owns the report, and the report path is reachable from
   candidate `sys.argv`. The ADR-005 revision moves candidate execution into a
   separate namespace and keeps the report authority in a host process that
   never imports candidate code (see §"Approved boundary designs", 3).
5. **Trajectory integrity** (`src/hop/trajectories.py`): per-source monotonic
   sequences, at-least-once dedup by `event_id`, gap detection, and required
   event/terminal presence. A missing required terminal event marks the run
   `telemetry_incomplete` even when sequences are contiguous.
6. **Authority separation** (`contracts/records.py`, ADR-004): platform,
   harness, agent, and verifier provenance are distinct; agent text is never a
   verdict.
7. **Run identity and scope** (`src/hop/runner.py`): every run records
   repository, environment, verifier, model-deployment, and harness digests;
   out-of-scope modifications and protected-file edits are rejected
   (`scope_violation`). Infrastructure failures are terminal `infra_error` runs
   with a durable `error.json` and `report.json`.

## Explicitly not guaranteed in M1 (deferred, not approved)

These are the baseline limitations. The three marked **[superseded by approved
design]** items now have an approved target boundary, but the target is **not
implemented**, so the limitation text remains the honest runtime position until
task-9 lands and the named probes pass. Superseded residual risk is restated in
§"Residual risk after the approved designs".

- Container/VM isolation or a separate UNIX identity for the verifier; M1 uses
  `bwrap` namespaces for the invoking user. VM/Kata-class isolation for
  untrusted repositories remains M5+/M10.
- **[superseded by approved design — ADR-012; not yet in effect]** Egress policy
  beyond loopback inference: the agent namespace shares the host network when a
  real model call is required. Host-level egress enforcement is an open
  foundation defect, not waived by the single-user scope.
- **[superseded by approved design — ADR-013; not yet in effect]** Aggregate
  descendant CPU/memory/disk quotas are not established by RLIMITs.
- Publisher services and multi-user authorization are out of scope, not future
  required trust principals. Local trace projection is implemented.
- **[superseded by approved design — ADR-005 revision; not yet in effect]**
  Candidate code imported by hidden tests can read the current case's hidden
  tests during verification only; sealed evaluator storage with an access
  ledger is M4. The revision also closes the undocumented same-process
  write-side vector (forged report), which the baseline threat model did not
  record.
- `prime` on PATH is the cloud Prime Intellect CLI, not the Prime Agent
  harness; DeepSeek `dsh` is a developer preview. Both are recorded as
  discovered/blocked, never auto-qualified.

## Approved boundary designs (ADR-012 / ADR-013 / ADR-005 revision)

**Status: approved design, not implemented.** No control in this section may be
cited as enforcement until its mechanism is in the code, its capability probe
passes for the evidenced host, and the evidence listed for it is present in the
run. Each item names the mechanism, the capability probe, the fallback/blocked
behaviour, and the exact evidence required before an "enforced" claim. Full
detail: [ADR-012](adr/ADR-012-worker-egress-broker.md),
[ADR-013](adr/ADR-013-aggregate-resource-quotas.md),
[ADR-005 revision plan](adr/ADR-005-revision-plan.md), and
[boundary-design.md](verification/boundary-design.md).

### 1. Worker network boundary — no network namespace, broker-only inference

*Mechanism.* Agent workers run with `bwrap --unshare-net`: the namespace has no
non-loopback interface and no route to the host or the LAN. The only inference
path is a per-run broker (`hop.broker`, control plane) listening on an
`AF_UNIX` socket at `<run_dir>/worker/broker/inference.sock` (`0600`), bind
-mounted into the worker mount namespace. The broker starts only after
`verify_served_model` passes, targets the exact attested loopback endpoint,
pins the request `model` to the registered deployment, allow-lists
`POST /v1/chat/completions` and `GET /v1/models`/`/props`, refuses
absolute-form targets, `CONNECT`, other paths/methods, redirects, and proxies,
and writes one audit line per request. The worker authenticates with a
per-attempt `secrets.token_urlsafe(32)` bearer token bound to
`(run_id, attempt_id)` in the broker; the token is delivered as the Pi provider
`apiKey` and rotated/revoked per attempt. The existing loopback URL checks
(`policy.assert_local_url`) and served-model attestation are preserved unchanged
and apply to the broker's upstream.

*Capability probe.* `probe_worker_network_isolation()` must show: a `--unshare-net`
namespace can be created; only `lo` exists and the host inference port is
unreachable from it; loopback can be made operational (stdlib `SIOCSIFFLAGS`
`IFF_UP`, `ip` fallback); and a bind-mounted UNIX socket is connectable.

*Fallback / blocked.* If any probe check fails the run is terminal `infra_error`
(`worker_network_isolation_unavailable`) and does **not** execute. There is no
fallback to `share_network=True`; `share_network=True` on an agent attempt is a
hard error.

*Evidence required before claiming enforcement.* Manifest `network.enforced ==
true` with the probe record (bwrap version, namespace/socket checks) for this
run, plus broker audit lines for every inference request. A run without that
record is reported as blocked, not isolated.

### 2. Aggregate descendant resource boundary — cgroup v2, enforced/limited/blocked

*Mechanism.* One cgroup v2 subtree per run/attempt under a delegated parent,
with `memory.max`, `memory.swap.max=0` (when present), `pids.max`, `cpu.max`
(a CPU share, replacing per-process `RLIMIT_CPU`), `io.max` for the run root's
backing device, and `cgroup.kill` on cancel/timeout. `limit_exec.py --cgroup`
writes its own pid into `cgroup.procs` and verifies placement **before** `exec`,
so an unplaceable child never runs and descendants inherit the subtree.
RLIMITs remain only for dimensions the probe could not enforce.

*Capability probe.* `probe_cgroup_v2(parent)` attempts real operations (a real
`mkdir`/`subtree_control` write; `os.access` is not trustworthy on cgroupfs),
verifies controllers, writes each limit and reads it back, confirms probe
placement in `cgroup.procs`, and removes its probe tree on every path.

*Fallback / blocked.* Dimensions the probe cannot enforce are listed in
`unenforced[]` with `mode="limited"` (RLIMIT fallback retained). When the caller
requires aggregate enforcement (`HOP_REQUIRE_QUOTAS=1` or
`SandboxSpec.require_aggregate_quota`), an unsatisfiable probe is terminal
`infra_error` (`aggregate_quota_unavailable`) and the run is not started.

*Evidence required before claiming enforcement.* For this run,
`resources.mode == "enforced"` **and** per-dimension read-back proof that each
requested limit is in effect; otherwise the mode is `limited`/`blocked` with the
exact `unenforced[]` dimensions. A host with a read-only cgroup mount (the
current delivery host) must report `unavailable`/`limited`, never enforcement.

### 3. Verifier boundary — trusted host plus candidate namespace

*Mechanism.* The verifier is two processes over one frozen snapshot. The
**trusted host** runs `python -I` with the evaluator-owned pytest config and
plugin, owns the pinned `expected_tests`/mandatory policy, and writes the report;
it **never imports candidate code**. The **candidate worker** runs in its own
`bwrap` mount/PID/net namespace with the frozen snapshot read-only plus private
scratch, and **not** the hidden tests, verifier scaffold, or report path.
Candidate execution is reached only through a versioned RPC proxy, so existing
hidden tests are unchanged. `freeze_workspace`/`hash_tree` digests are recorded
and the snapshot is read-only.

*Two regressions this protects against (both must be RED before and GREEN
after).*
1. **Current-case visibility** — candidate code read the current case's hidden
   tests (the documented ADR-005 limitation), and could discover evaluator
   paths from `sys.argv`.
2. **Report forgery in the verifier process** — candidate code could read the
   report path from `sys.argv`, write a forged all-`passed` report into the
   read-write `out_dir`, and `os._exit(0)`; the baseline threat model did not
   record this write-side vector. It is closed by moving report authority into
   the host process.

*Capability probe.* `probe_nested_user_namespaces()` selects `mode="nested"`
(host and worker both confined) or the recorded weaker fallback
`mode="host-driver"` (trusted host as a control-plane process; only the
candidate worker is namespace-confined). The mode is recorded in
`verification.json`/`manifest.json`; a run must never claim nested when the
fallback was used.

*Fallback / blocked.* `bwrap` unavailable or namespace creation refused is
`verifier_sandbox_unavailable`/`infra_error`; there is no unisolated fallback.
An RPC protocol mismatch between host and worker is `infra_error`
(`verifier_rpc_mismatch`), never a PASS.

*Evidence required before claiming isolation.* The two regressions above
present and green, the recorded isolation mode, frozen-snapshot digests, and a
`verification.json` whose `verifier_version` is the post-revision identifier
(`debug-verifier-v3`); pre-revision `verification.json` files remain readable
as `debug-verifier-v2` history.

## Residual risk after the approved designs

Even with all three designs implemented, these limitations stay explicit and
must not be described as closed:

- **No hostile-workload qualification.** The boundaries are `bwrap` namespaces
  for the invoking user, not container/VM isolation and not a separate UNIX
  identity. Kernel/namespace escape and VM/Kata-class isolation remain
  M5+/M10.
- **Broker token is held by the worker it authenticates.** It distinguishes
  runs/attempts and prevents cross-run or stale-worker use; it does not separate
  the worker from the control plane (same uid). The relay inside the worker
  namespace is a dumb forwarder, but a hostile worker can kill or talk to it.
- **Quota honesty on non-delegating hosts.** Where cgroups cannot be delegated
  the mode is `limited`/`blocked` with named unenforced dimensions; that is not
  enforcement, and `memory.max`/`io.max` gaps remain possible. `output_limit_bytes`
  truncation stays post-hoc.
- **Verifier `host-driver` fallback is weaker than nested** and is recorded as
  such. Proxy fidelity is limited to the candidate API surface the pinned hidden
  tests use; an unrepresentable shape is `infra_error`, not a PASS.
- **Sealed evaluator storage / access ledger** (AOP-020) and host credential
  protection beyond the current mounts remain future work.
- **Host-specific gaps.** Whether a given host/template can create the network
  namespace or delegate cgroups is measured per host (WP-0.5
  internal Cube capability record, not published); a template that cannot do so yields
  blocked/limited status, not a qualified live path. Measured so far: Cube
  template `tpl-eb9b6ee957064ac09b80cd8e` has cgroup v2 fully delegated
  (`mkdir`/`subtree_control`/limit writes/placement/`rmdir` all succeed), so the
  gate there can prove `enforced`; the current delivery host mounts
  `/sys/fs/cgroup` read-only, so it must report `unavailable`/`limited`. Neither
  measurement is an acceptance result on its own.
- **Live model acceptance.** Local-model qualification and the Pi 1.1.0
  requalification remain outstanding until owner input O2 is available; no
  fixture qualifies a model or the installed harness.

## Adversarial fixtures (tests/security/, tests/test_verification.py)

Implemented at baseline:

- `os._exit(0)` in candidate code: rejected (`verification_no_result`).
- Candidate `sitecustomize.py` / `usercustomize.py`: not imported; no PASS.
- Candidate `conftest.py` / `pytest.ini`: ignored by the verifier; no PASS.
- Skip-all / zero-collected / deselected / collect-only reports: rejected.
- Agent reading/writing hidden material or verifier code: path absent/denied.
- Agent reading another run's workspace: absent/denied.
- Forged success claim with failing hidden tests: verdict FAIL.
- Endpoint env injection (`HOP_PI_BASE_URL`, legacy `AOP_PI_BASE_URL`): scrubbed and ignored.
- Remote endpoint / model identity mismatch: fail closed.
- Tampered artifact (flipped byte): hash verification fails.
- Observer gap or missing terminal event: run marked `telemetry_incomplete`.

Required new fixtures for the approved designs (must be RED before and GREEN
after the corresponding implementation; inventory and commands in
internal boundary-design study §5, not published):

- Worker cannot reach a host listener, public DNS, or any non-loopback address
  under `--unshare-net`.
- Broker rejects a missing, foreign-run, or revoked attempt token; refuses
  non-allow-listed paths/methods, `CONNECT`, absolute-form targets, redirects,
  model-id mismatch, and a non-loopback upstream.
- A failed network-isolation probe is `infra_error`
  (`worker_network_isolation_unavailable`); no shared-network fallback exists.
- cgroup capability probe leaves no residue; a missing controller or a limit
  whose read-back does not match is reported `unenforced`/`limited`, never
  `enforced`; `HOP_REQUIRE_QUOTAS=1` fails closed.
- A descendant that double-forks is reaped by `cgroup.kill`; `pids.max` denies a
  fork bomb; unplaceable children never `exec`.
- Candidate cannot read the current case's hidden tests, the verifier scaffold,
  or the report path, and cannot forge a report (`os._exit(0)` after writing
  one).
- Torn trajectory writes: recover only when the event line and chain sidecar are
  both complete and mutually consistent; otherwise retain-and-reject. Both
  branches are tested.

Honesty requirement for sandbox/security suites: a missing `bwrap`, a refused
namespace, or absent cgroup delegation must be reported as `blocked`/
`infra_error` (or fail the test), **never** as a silent skip that leaves the
suite green. A green sandbox suite on a host where the boundary could not be
created proves nothing and must not be recorded as boundary evidence.

---

# 1.0.0 security sign-off (WP-4.2, task-36)

This section is the release sign-off. The design sections above are the historical
M1/AOP-003 record and are **not rewritten**; where they say "approved design, not
implemented", that status is superseded for 1.0.0 by the independent verdicts
below. Every control cited here points at a review entry in
the internal review index (not published).

## Approved designs: implemented and independently confirmed

| Design | Status in 1.0.0 | Independent evidence |
|--------|-----------------|----------------------|
| ADR-012 worker egress broker (no-network worker + per-run broker socket/token) | implemented | R-010 at `105fba5`; broker isolation test in the internal overhead report (not published); gate `hop-gate-task31-4cfb317` |
| ADR-013 aggregate cgroup v2 quotas (enforced/limited/blocked with `unenforced[]`) | implemented; host reports limited/unavailable honestly | R-009 at `1beb23d`; internal Cube capability record (not published) |
| ADR-005 revision: verifier trusted host + candidate namespace | implemented | R-009 at `1beb23d`; R-016 at `3dd6a21`; `hop-gate-task32-674b991` (sandbox 99/0/0/0) |
| Sealed evaluator storage, splits and access ledger | implemented and adopted in the run/verify path | R-011 at `f412c84`, R-014 at `931d31f`, R-016 at `3dd6a21` |
| Owner-only storage, worker path isolation, activity history | implemented | R-012 at `53f2084` |
| Reward-hacking/adversarial suite | implemented | R-018 at `f759151` (fix `b0a00bb`) |
| Backup/restore of the evidence chain | implemented | R-021 at `a4c5934` |
| Symlinked interpreter install root; verification RPC socket cleanup | implemented | R-022 at `d920e92` (+ R-022-HOST closure); R-023 at `6edbdf2` |

## Residual risks for 1.0.0 (explicit)

1. **Broker token scope.** The per-attempt bearer token is the only credential on
   the broker `AF_UNIX` socket (0600). It separates runs/attempts; it does not
   separate the worker from the control plane (same uid). Any same-uid process that
   can read the socket/token can use the pinned upstream.
2. **Same-uid model.** Owner-only modes (0700/0600) and fail-closed
   `InsecurePermissions` protect against *other* users, not against a compromised
   process running as the owner, which can chmod or read the owner's files.
3. **No hostile-workload qualification.** Isolation is `bwrap` namespaces for the
   invoking user, proven against scripted adversaries (no-network, contamination,
   forged reports, tamper), not container/VM isolation and not a separate UNIX
   identity. Kernel/namespace escape remains out of 1.0 scope.
4. **Live-model coverage is one case.** G8 is one case on one deployment
   (`qwen-flash-next-IQ2_S_-_2.5_bpw`) and one harness (Pi 1.1.0); no
   generalization to other models, quantizations or adapters is claimed.
5. **Egress posture inconclusive at the gate.** The ESF gate records
   `EgressVerified=Unknown (ProbeInconclusive)`; worker network denial is proven by
   sandbox tests, but the gate probe cannot conclusively prove host posture.
6. **Quota honesty on non-delegating hosts.** Where cgroups cannot be delegated the
   mode is `limited`/`blocked` with named `unenforced[]` dimensions; that is not
   enforcement. `output_limit_bytes` truncation remains post-hoc.
7. **CI vs local deviation.** The CI type-check path deliberately differs from the
   pinned local `local_environment` pin, and CI execution is unexecuted on this
   host. No CI job is claimed green.
8. **Attribute-cardinality finding (open).** Attacker-controlled keys in
   `InferenceRecord.extra` become OTel attribute keys; 1.0 emits no metric labels,
   so impact is attribute cardinality only. Recorded in R-018 and the
   adversarial-suite report.
9. **Hidden material is host-resident.** Hidden tests remain on the host filesystem
   outside the agent workspace; the sealed store is adopted in the run/verify path,
   but the interim namespace is not a hardware boundary.
10. **Single-user assumption.** No multi-tenant isolation, role administration or
    shared-host threat model is claimed. Optimization/activation do not exist in
    1.0, so no optimizer-contention control is claimed either.

## Sign-off statement

The controls above are implemented and independently confirmed at the cited shas;
the release gates are tracked in [docs/release-checklist.md](release-checklist.md)
(G1–G11) and the evidence in
[docs/evidence/1.0/RELEASE-EVIDENCE.md](evidence/1.0/RELEASE-EVIDENCE.md).

This sign-off **does not** authorize push, PR, tag or publication (gate G11, an
explicit owner action), and does not cover optimization, activation, multi-tenancy
or hostile-workload qualification, none of which are part of 1.0.

