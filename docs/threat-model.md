# Threat model — M0/M1 trust boundaries (AOP-003, revised after M1 review)

Status: implemented for the M1 vertical slice. PostgreSQL/OTel/publisher
deferred (ADR-001, ADR-005). This document describes what the code actually
guarantees, not the aspirational M4+ design.

## Principals and processes

| Principal | Identity | Filesystem (namespace view) | Network | Credentials |
|---|---|---|---|---|
| Control plane / CLI (`hop`) | invoking user | repo + `runs/` ledger | loopback inference only (`policy.assert_local_url`) | user env, never forwarded |
| Agent worker (Pi) | `bwrap` mount/PID namespace | run root + pinned harness/Node runtime (ro) + `/tmp`; **no** repo, `.hidden`, or other runs | loopback only (deployment endpoint); proxies/keys scrubbed | none |
| Trusted verifier | `bwrap` mount/PID namespace | frozen snapshot (ro) + one hidden case (ro) + verifier bootstrap (ro) + scratch out | none (`--unshare-net`) | none |
| Publisher | not implemented in M0/M1 | — | — | no signing keys exist yet |

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

- Container/VM isolation or a separate UNIX identity for the verifier; M1 uses
  `bwrap` namespaces for the invoking user. VM/Kata-class isolation for
  untrusted repositories remains M5+/M10.
- Egress policy beyond loopback inference: the agent namespace shares the host
  network when a real model call is required. Host-level egress enforcement is
  M10.
- PostgreSQL transactional outbox, OTel projection, signed releases, publisher
  broker, multi-tenant RBAC: see BACKLOG M2–M10.
- Candidate code imported by hidden tests can read the current case's hidden
  tests during verification only; sealed evaluator storage with an access
  ledger is M4.
- `prime` on PATH is the cloud Prime Intellect CLI, not the Prime Agent
  harness; DeepSeek `dsh` is a developer preview. Both are recorded as
  discovered/blocked, never auto-qualified.

## Adversarial fixtures (tests/security/, tests/test_verification.py)

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
