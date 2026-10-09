# ADR-013: Privilege-delegated cgroup v2 quotas for aggregate descendants

Date: 2026-10-09.

Status: **draft (proposed)** — design review artifact for HOP-R04; not yet
implemented. A run may only be reported as quota-*enforced* after the capability
probe in this ADR passes and the values are read back from the kernel.

Closes finding: *"RLIMITs are per-process/per-user controls, not aggregate
per-run CPU/memory/disk quotas; output truncation occurs after execution"*
(`docs/foundation-review.md`, High/Open).

## Context

`src/hop/limit_exec.py` is a fresh process that applies `RLIMIT_CPU`,
`RLIMIT_NPROC`, and `RLIMIT_AS` and then `execvpe`s the sandboxed command.
`spawn_confined` always wraps the command with it, so limits are at least applied
before exec and a limit-setup failure stops execution (that was the earlier
finding's fix).

Those primitives do not provide per-run aggregate quotas:

* `RLIMIT_CPU` is a soft CPU-seconds budget **per process**; every descendant
  gets its own budget, so N forked children get N times the CPU.
* `RLIMIT_NPROC` counts processes for the **whole real uid**, not the run. It
  interferes with the control plane, other runs, and the login session, and it is
  ineffective for a worker that can change uid or is otherwise over the limit.
* `RLIMIT_AS` caps *virtual address space*, not resident memory. Modern runtimes
  (Node, JVM, Go) reserve large virtual ranges, so it both over-triggers and
  under-protects; it is not a working-set cap.
* There is no I/O bound at all; `SandboxSpec.output_limit_bytes` truncates
  stdout/stderr **after** the run, so a disk-filling child runs to completion
  first.
* A child that double-forks and leaves the process group is not covered by the
  existing `_kill_group` SIGTERM/SIGKILL cancellation either.

cgroup v2 provides exactly the aggregate, descendant-inclusive enforcement
needed, but only when the host delegates a writable subtree. A cgroup
`/sys/fs/cgroup` mount can be read-only, controllers may be missing, or a
container runtime may refuse subtree delegation. The design must therefore
detect capability and report `blocked`/`limited` honestly instead of claiming a
quota it did not establish. (On the current delivery host `/sys/fs/cgroup` is
mounted `ro` — verified — so the blocked path is the expected local outcome until
the Cube template is probed.)

## Decision

1. **Per-run cgroup v2 subtree, created through a delegation probe.**
   `hop.capabilities.probe_cgroup_v2(parent)` returns a structured report and is
   the only authority for whether aggregates can be enforced. It performs real
   operations, not `os.access` checks (which are unreliable on cgroupfs):
   * `/sys/fs/cgroup` is `cgroup2fs` (`statfs` magic `0x63677270`);
   * the chosen parent is writable **in practice**: create
     `<parent>/hop-probe-<pid>`, write `cgroup.subtree_control` to enable the
     needed controllers, create a child, read each file back, then remove the
     probe tree in a `finally` block. No residue may remain on any path;
   * `cgroup.controllers` contains the subset being requested;
   * each limit file accepts the write **and reads back identically**;
   * placement works: a probe child's pid appears in `cgroup.procs`.
   The report records `mount`, `writable`, `controllers`, `subtree_control`,
   `enforceable{memory,pids,cpu,io}`, `reasons[]`, and `mode ∈
   {enforced, partial, unavailable}`.

2. **Enforcement file set** for a run/attempt cgroup
   `<delegated-parent>/hop/<run_id>-<attempt_id>`:

   | Dimension | File | Value | If unavailable |
   |---|---|---|---|
   | Memory | `memory.max` | `SandboxSpec.memory_bytes` | `memory` unenforced |
   | Swap bypass | `memory.swap.max` | `0` when present | record `swap_unenforced` |
   | Processes/threads | `pids.max` | `SandboxSpec.max_processes` | `pids` unenforced |
   | CPU | `cpu.max` | `<quota> <period>` from a new `SandboxSpec.cpu_quota_us`/`cpu_period_us` (default e.g. `200000 100000` = 2 CPUs) | `cpu` unenforced; keep `RLIMIT_CPU` |
   | Block I/O | `io.max` | `<major>:<minor> rbps=… wbps=… riops=… wiops=…` for the run root's backing device (`os.stat(root).st_dev`) | `io` unenforced (record device too) |
   | Kill | `cgroup.kill` | write `1` on cancel/timeout | fall back to `_kill_group` |

   `cpu.max` replaces CPU *seconds* as the aggregate control; `RLIMIT_CPU` is a
   per-process budget and must not masquerade as the run quota.

3. **Race-free placement before exec.** `limit_exec.py` gains a
   `--cgroup <path>` argument. Before `execvpe`, it writes its own pid to
   `<path>/cgroup.procs`, reads the file back, and requires its pid to be present
   (or the write to have succeeded and `cgroup.procs` to contain it). If
   placement cannot be verified, it exits non-zero and the command **never
   execs**. Descendants then inherit the cgroup by construction. This preserves
   the "limits applied in a fresh process before exec; failures stop execution"
   guarantee established by the earlier finding.

4. **Honest run status.** `manifest.json` gains
   `resources: {mode, enforceable, unenforced[], cgroup_path, probe}` and the run
   record carries the same summary. Rules:
   * `mode="enforced"` only when every requested dimension was written and read
     back in effect for this run;
   * `mode="limited"` when cgroups are partially available: the run proceeds
     with the enforceable dimensions active, `RLIMIT` fallbacks retained, and
     `unenforced[]` listing exactly what is not enforced;
   * `mode="blocked"` when the caller requires aggregate enforcement
     (`SandboxSpec.require_aggregate_quota=True`, or `HOP_REQUIRE_QUOTAS=1`) and
     the probe cannot satisfy it: terminal `infra_error`
     `aggregate_quota_unavailable`, run not started.
   No code path may write `enforced` without a successful read-back for this run.

5. **RLIMIT migration.** Keep `limit_exec.py` and retain
   `RLIMIT_CPU`/`RLIMIT_AS`/`RLIMIT_NPROC` **only** for dimensions the probe
   reports unenforced, and document the double-limit semantics:
   * `memory` enforced by cgroup v2 → drop `RLIMIT_AS` for that run (it breaks
     legitimate large virtual reservations and is not RSS);
   * `pids` enforced by cgroup v2 → drop `RLIMIT_NPROC` (it was per-uid and
     host-wide);
   * `cpu` enforced by `cpu.max` → drop `RLIMIT_CPU` (it was per-process);
   * `io` has no RLIMIT equivalent; when unenforced it is reported, not faked.
   The policy identity for new bundles becomes `local-default-v3` at the point
   task-9 changes runtime defaults; historical bundles and evidence keep
   `local-default-v2` and remain readable.

6. **Descendant kill.** On cancel/timeout, prefer writing `cgroup.kill` so
   double-forked and daemonized descendants are reaped regardless of process
   group. `_kill_group` remains the fallback and its exit semantics are
   unchanged.

7. **Output bounding** is acknowledged as post-hoc in this ADR: the existing
   `output_limit_bytes` truncation (with `truncated=True`) stays, and the run
   reports it. A future change may stream-bound stdout; that is not claimed here.

## Capability checks (exact)

| Check | Pass condition | Failure behavior |
|---|---|---|
| cgroup v2 mounted | `statfs(/sys/fs/cgroup).f_type == CGROUP2_SUPER_MAGIC` | `unavailable` |
| parent writable in practice | probe `mkdir` + `subtree_control` write succeed | `unavailable`; do not claim |
| controllers present | requested ⊆ `cgroup.controllers` | that dimension `unenforced` |
| subtree delegation | child cgroup can be created and enabled | `unavailable`/`partial` |
| limit accepted and in effect | each limit file reads back the written value | that dimension `unenforced` |
| placement works | probe pid appears in `cgroup.procs` | `unavailable` (never exec unplaced) |
| `cgroup.kill` | file exists and write succeeds | fall back to `_kill_group`, record `kill_fallback` |

Residue discipline: the probe removes its tree on every path; a test asserts
that a failed probe leaves no `hop-probe-*` entry.

## Migration plan

1. **Phase 1 (task-9, measurement only).** Add `hop.capabilities.probe_cgroup_v2`
   and record its report in the manifest; no behavior change. Counterfactual
   evidence: on this host the report must be `unavailable` with reason
   `read_only_mount` (mount verified `ro`); in the Cube template it must reflect
   the real result measured in task-5.
2. **Phase 2 (task-9).** Enable cgroup placement when the probe is `enforced`
   for the requested dimensions, with RLIMIT fallbacks retained for the rest and
   `mode` recorded. Add negative tests (below).
3. **Phase 3 (task-9).** Retire the RLIMITs that a verified cgroup replaced and
   switch new bundles to `local-default-v3`; keep reading old locks/evidence.
4. **Phase 4 (owner).** Decide whether live agent runs require
   `require_aggregate_quota=True` (blocked on non-delegating hosts) or accept
   `limited` with recorded gaps. Until the owner decides, agent runs are
   `limited` where cgroups are unavailable; verifier runs keep their current
   RLIMIT behavior plus a cgroup attempt.

## What remains unenforced / residual risk

* On a host that cannot delegate, no aggregate quota exists; the run says so and
  `unenforced[]` is the honest record. Reading `limited` must never be described
  as enforcement.
* `memory.max` is a hard cap (OOM kill), not admission control; a run can still
  be killed at the cap, and memory pressure on the host outside the cgroup is
  not modelled.
* `io.max` needs a real block device; overlay/tmpfs run roots may make it
  unavailable, which is reported.
* The cgroup subtree is created by a same-uid process; a compromised control
  plane is out of scope, and a worker cannot move itself out because it lacks
  write access to the delegated parent by construction (verify in the probe).
* CPU quota is a scheduler share, not a wall-clock timeout; `timeout_s` remains
  the wall-clock control.

## Consequences

* Memory/CPU/PID/I/O consumption is charged to a per-run subtree that includes
  every descendant, closing the RLIMIT gap.
* The honest status model adds a manifest field and a possible `infra_error`
  path; operators get a precise reason instead of an over-claim.
* The prior finding's guarantee ("fresh process, failures stop execution") is
  preserved and extended: an unplaceable child never execs.
