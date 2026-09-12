# Threat model — M0 trust boundaries (AOP-003)

Status: implemented for the M1 vertical slice. PostgreSQL/OTel deferred (see ADR-002).

## Principals and processes

| Principal | Identity | Filesystem | Network | Credentials |
|---|---|---|---|---|
| Control plane / CLI (`aop`) | invoking user | repo + `runs/` ledger | loopback inference only (`policy.assert_local_url`) | user env, never forwarded |
| Agent worker (Pi subprocess) | per-run `worker_id` | isolated `workspace/` + `home/` only | none (env scrubbed, `PI_OFFLINE=1`) | none; proxies/keys/SSH socket stripped |
| Trusted verifier | `verifier` observer | frozen snapshot copy + sealed `hidden-tests/` | none | none |
| Publisher | not implemented in M0/M1 | — | — | no signing keys exist yet |

## Boundaries enforced in code

1. **Worker isolation** (`src/aop/sandbox.py`, `src/aop/policy.py`): per-run
   workspace/home/cache/session dirs; `scrub_worker_env` removes API keys,
   `*_PROXY`, `SSH_AUTH_SOCK`; `check_no_proxy_leak` asserts before spawn.
   Hidden verifier material lives outside the workspace root and is never
   bind-mounted, copied, or embedded into task prompts (`TaskSpec.assert_no_hidden_content`).
2. **Local-only inference** (`src/aop/policy.py`, `src/aop/inference.py`):
   every endpoint URL must parse to a loopback host; redirects are not
   followed (`urllib` opener without redirect handler); non-2xx and
   off-host targets fail closed. No cloud fallback exists in the code.
3. **Independent verification** (`src/aop/verification.py`): verdict comes only
   from `EvaluationResult` produced by the verifier worker on a frozen
   snapshot. `agent_claim` is stored separately and never coerced to a verdict.
4. **Trajectory integrity** (`src/aop/trajectories.py`): per-source monotonic
   sequences, at-least-once dedup by `event_id`, completeness gaps mark the run
   `telemetry_incomplete`, which is promotion-ineligible.
5. **Auxiliary model calls**: the only model path is the registered local
   endpoint. Compaction/title/embeddings/judge do not exist in M1 and therefore
   cannot leak; adding any requires routing through `inference.LocalEndpointClient`.

## Out of scope / deferred (explicit, not approved)

- Container/VM isolation: M1 uses process + filesystem + env isolation only.
  Untrusted-repo execution still requires external containment (M5+ hardening).
- PostgreSQL transactional outbox, OTel projection, signed releases,
  publisher broker, multi-tenant RBAC: not implemented; see BACKLOG M2–M10.
- `prime` CLI on PATH is the cloud Prime Intellect CLI, not the Prime Agent
  harness; DeepSeek `dsh` is a developer preview. Both recorded as
  discovered/blocked, never auto-qualified.

## Adversarial fixtures (tests/security/)

- Hidden-test access attempt from workspace: denied (no path exists).
- Forged success claim ("I fixed it", exit 0) with failing hidden tests: verdict FAIL.
- Proxy env injection into worker: scrubbed + assertion-tested.
- Tampered artifact (flipped byte): hash verification fails.
- Observer gap injection: run marked `telemetry_incomplete`.
