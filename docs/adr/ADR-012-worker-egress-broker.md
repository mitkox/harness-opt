# ADR-012: Worker network isolation and the loopback inference broker

Date: 2026-10-09.

Status: **accepted and implemented** (WP-1.4d, task-9). The probe in this ADR is
the only authority for an enforcement claim; a run whose probe fails is terminal
`infra_error` (`worker_network_isolation_unavailable`) and never shared-network.
Implementation: `src/hop/sandbox.py` (`probe_worker_network_isolation`),
`src/hop/harnesses/broker.py`, `src/hop/harnesses/broker_relay.py`,
`src/hop/harnesses/pi_adapter.py`, `src/hop/runner.py`.

Closes finding: *"Pi workers share host networking; endpoint validation is not
OS-level general egress enforcement"* (`docs/foundation-review.md`, High/Open).

## Context

Today the agent worker for the only implemented harness (Pi) is spawned from
`runner.py` with `SandboxSpec(share_network=True, ro_binds=_harness_ro_binds(...))`,
so the pi process runs in the host network namespace. Only the application-level
policy in `policy.py`/`inference.py` restricts destinations: `assert_local_url`
requires a loopback host, redirects are refused by `_NoRedirect`, and the Pi
provider base URL is derived only from the registered `ModelDeployment`.
`scrub_worker_env` removes proxy and credential variables.

That is not an OS-level boundary. Once the worker shares the host network it can
open sockets itself (raw Python, `curl`, a child process, a post-install hook)
to any reachable address, including the LAN, the Cube proxy, and any cloud
endpoint. Loopback endpoint validation constrains what HOP *tells* Pi to do; it
does not constrain what the worker *can* do. HOP already contains the strongest
primitive for this boundary: `build_bwrap_cmd` emits `--unshare-net` whenever
`share_network` is false, and the trusted verifier already runs under it.

The blocker is that the worker legitimately needs model inference, and with a
private network namespace there is no route to the host's loopback inference
endpoint.

## Decision

1. **Agent workers run with no network.** The Pi worker `SandboxSpec` uses
   `share_network=False`, so `bwrap` creates a fresh network namespace
   (`--unshare-net`) whose only interface is loopback. The real guard is
   `sandbox.spawn_isolated`, which raises `ValueError` whenever a spec requests
   `share_network=True`: no code path (agent, test, or tool) can spawn an
   isolated worker with host networking. The runner additionally requires
   `probe_worker_network_isolation()` to report `enforced` before the harness
   starts, via `runner.require_worker_network_isolation()`, which raises
   `InfraError("worker_network_isolation_unavailable")` otherwise. There is no
   `ATTEMPT_KIND` symbol in `src/hop`; the enforcement does not depend on one.

2. **Inference is reached through a per-run broker over a bind-mounted UNIX
   socket.** A trusted control-plane process, `hop.broker` (stdlib only),
   listens on `AF_UNIX` `SOCK_STREAM` at `<run_dir>/worker/broker/inference.sock`
   (mode `0600`, directory owned by the run root). The socket directory is
   bind-mounted into the worker's mount namespace. The broker is the only
   component that opens a socket to the inference server; the worker never has a
   route to the inference port.

3. **Per-run bearer token.** At attempt start the control plane generates
   `secrets.token_urlsafe(32)`. The token is written to the Pi provider
   configuration (`models.json` → provider `apiKey`) inside the run's
   `PI_CODING_AGENT_DIR` and is bound to `(run_id, attempt_id)` in the broker.
   The broker accepts a request only with a live token for that run; a token from
   another run, a revoked attempt token, or a missing/duplicate token is refused
   with `401` and a closed connection. Tokens are rotated per attempt and
   revoked when the attempt is fenced or finalized. The token is an
   anti-confusion / anti-stale-worker control, **not** a control against the
   run's own worker (see "What remains unenforced").

4. **Transport adaptation to Pi.** Pi's OpenAI-compatible provider takes an HTTP
   `baseUrl`, so the design supports two transports and selects one only after a
   probe:
   * *unix-base*: if `probe_pi_unix_base()` shows the installed Pi build accepts
     a UNIX-socket base URL, the broker socket is the base URL directly;
   * *relay* (default, guaranteed): a pinned, read-only `hop/broker_relay.py`
     (stdlib) runs as the first process inside the worker namespace, brings the
     namespace loopback up (see capability check), listens on an ephemeral
     `127.0.0.1` port, and forwards bytes to the bind-mounted UNIX socket. Pi's
     `baseUrl` is `http://127.0.0.1:<port>`. The relay is a **dumb forwarder**:
     it holds no token and adds no header, so the bearer token stays with the
     worker as required and the relay cannot be used as an authority.
   The relay is dumped-forward only; it does no HTTP parsing and is not a policy
   point. The broker is the policy point.

5. **Broker policy.** The broker:
   * starts only after `verify_served_model(deployment)` has passed in the
     control plane for this attempt (existing call site `runner.py`), and binds
     to the exact attested `127.0.0.1` endpoint; it re-runs `assert_local_url`
     on that endpoint and refuses to start otherwise;
   * allow-lists `POST /v1/chat/completions`, `GET /v1/models`, `GET /props`
     (Pi's OpenAI-completions surface), refuses absolute-form request targets,
     `CONNECT`, other methods, and any other path;
   * pins the request `model` field to the deployment's `model_id` and refuses a
     mismatch (no silent substitution);
   * never follows redirects (reuses the `_NoRedirect` semantics), never uses a
     proxy, and strips hop-by-hop headers;
   * bounds request bytes, response bytes, and connect/read time to the
     `SandboxSpec` limits, and surfaces them as explicit errors;
   * records one audit line per request (run, attempt, path, model, status,
     bytes, duration) into the run directory so the manifest can prove the
     worker's only egress touched the broker.

6. **Capability check, fail closed.** Before an agent run starts, the control
   plane runs `probe_worker_network_isolation()` in a throwaway namespace and
   records the result. The probe must show all of:
   * `bwrap --unshare-net` can be created;
   * only `lo` exists in the namespace and both the host-side inference port
     and a public address (`1.1.1.1:443`) are unreachable;
   * loopback can be made operational inside the namespace (see below), because
     the relay needs `127.0.0.1`;
   * a UNIX socket bind-mounted from the run root can be connected to.
   If any check fails, the run is terminal `infra_error` with
   `worker_network_isolation_unavailable` and a capabilities record. **There is
   no fallback to `share_network=True`.** A run that cannot prove network
   isolation does not execute.

   Bringing `lo` up must not add an `iproute2` dependency: the relay helper uses
   `fcntl.ioctl(SIOCSIFFLAGS)` with `IFF_UP` (stdlib) and only falls back to
   `ip link set lo up` when the ioctl path is unavailable. The ioctl needs
   `CAP_NET_ADMIN` in the namespace's owning user namespace; whether bwrap's
   invocation mode provides that is exactly what the probe measures.

## Capability checks (exact)

| Check | Interface | Pass condition | Failure behavior |
|---|---|---|---|
| bwrap present | `sandbox.bwrap_available()` | path found | existing `SandboxUnavailable` |
| net namespace creatable | `probe_worker_network_isolation()` | probe exits 0 under `--unshare-net` | `worker_network_isolation_unavailable` |
| no host route | same probe | TCP connect from namespace to host `127.0.0.1:<inference>` refused; no non-`lo` interface | blocked |
| loopback operational | same probe | `SIOCSIFFLAGS|IFF_UP` (or `ip link set lo up`) succeeds and a listener can be reached via `127.0.0.1` | blocked |
| unix socket visible | same probe | connect to the bind-mounted probe socket succeeds | blocked |
| Pi unix base URL | `probe_pi_unix_base()` | black-box help/probe evidence | relay transport selected |
| served model attested | `inference.verify_served_model()` | model id (and weight path when reported) matches | existing `ModelIdentityError` -> `infra_error` |

Probe results (with bwrap version and input digests) are stored in
`manifest.json` under `network` and in the run's evidence; a run whose manifest
does not carry `network.enforced == true` must not be reported as
network-isolated.

## Threat-model delta

`docs/threat-model.md` currently has one worker-network row and three deferred
limitations. Apply this delta when the ADR is implemented (task-9 owns the file):

* Principal table, *Agent worker* row, Network column:
  `none (--unshare-net); inference only via bind-mounted AF_UNIX broker`.
* Add a principal row:
  *Control-plane inference broker (`hop.broker`)* — identity invoking user —
  filesystem: run root + broker socket — network: loopback to the attested
  endpoint only — credentials: per-run token registry (issuer).
* Add enforced-boundary items:
  * *Worker egress* — the worker namespace has no non-loopback interface; the
    only inference path is the broker socket; broker requests require the run
    token and are allow-listed/pinned; refusal is `infra_error`, never a
    shared-network fallback.
  * *Broker audit* — per-request audit lines are part of run evidence.
* Replace the deferred bullet "Egress policy beyond loopback inference…" with a
  residual-risk bullet: the token is held by the worker it authenticates; the
  broker and worker share a UNIX identity, so the boundary is the network and
  mount namespace plus the broker's fixed upstream — not uid separation.
* Add adversarial fixtures: worker cannot reach a host listener or public DNS;
  broker rejects a foreign/revoked token; broker refuses non-allow-listed
  paths/methods/redirects; a probe failure produces `infra_error`, not a
  shared-network run.

## What remains unenforced (residual risk)

* **Same-UID local controls.** The worker, the relay, the broker, and the
  control plane run as the invoking user. A compromised worker can read its own
  token, kill or read its own relay, and consume its own broker allowance. The
  token cannot distinguish the worker from the control plane; it distinguishes
  *runs/attempts*.
* **Relay process.** The relay runs inside the untrusted namespace as a sibling
  of the harness. A hostile worker can kill it (availability) or talk to its
  port directly; it cannot redirect it (argv/config are read-only) and the
  broker still pins the upstream and model.
* **Kernel/namespace escape.** Not addressed here; VM/Kata-class isolation
  remains M5+/M10.
* **Non-Pi harnesses.** Any future harness that requires general network
  (package install, provider calls) is out of scope and must be explicitly
  blocked rather than given `share_network=True`.
* **Broker as a parser.** The broker does minimal HTTP parsing; malformed or
  oversized requests must fail closed, and its audit line is evidence, not a
  content-inspection guarantee.
* **Capability honesty.** On hosts where `--unshare-net` cannot be created
  (for example a nested unprivileged-user-namespace restriction), agent runs are
  blocked. This is intended: an unenforceable boundary is reported, not waived.

## Migration and compatibility

* `SandboxSpec.share_network` stays (default `False`); the runner stops passing
  `True` for `agent` attempts. `share_network=True` on an agent attempt is a
  hard error, not a warning.
* Historical runs, manifests, schemas, and policy ID `local-default-v2` are
  unchanged. Agent runs that would have used shared networking now record
  `network.enforcement ∈ {enforced, blocked}`; a new bundle policy ID
  (`local-default-v3`) is introduced only when task-9 changes runtime defaults,
  so old runs remain readable.
* `verify_served_model` remains exactly the existing loopback/identity check; the
  broker is additive and must not weaken or bypass it.

## Consequences

* A worker can no longer reach the LAN, cloud endpoints, or a package mirror
  from inside its own execution, regardless of what the agent writes.
* Live agent runs gain a hard prerequisite: `--unshare-net` plus a working
  in-namespace loopback. On this host the ESF Cube template must be probed
  (task-5 `docs/verification/cube-capabilities.md`); if the template cannot
  create the namespace, the honest outcome is a blocked live path, not a
  qualified one.
* One additional control-plane process per run, one extra ro mount, and a small
  amount of broker policy code — all stdlib.
