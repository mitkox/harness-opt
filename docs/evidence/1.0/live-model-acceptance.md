# G8 — live local-model acceptance (release gate)

Recorded by the Lead. This is the one release gate that requires a real local
model and a namespace-capable host shell; it is deliberately not inferred from
fixtures.

## Result: PASS

| Field | Value |
|---|---|
| Revision | `138a4ef9d76932000084a6341d6e80813230928a` (branch `esf/hop-1.0`) |
| Command | `./hop run start --case debug-offbyone --model qwen-flash-next` |
| Run id | `run-58f5589ef776` |
| Outcome / verdict | `pass` / `pass` (trusted verifier, not agent narration) |
| Model deployment | `qwen-flash-next-IQ2_S_-_2.5_bpw` (Qwen/Qwen3.8-Flash-Next, IQ2_S 2.5 bpw, `mitko` at `http://127.0.0.1:8000/v1`) |
| Harness | `pi` 1.1.0, status `qualified` (sha256 `c44ddd4e…`) |
| Agent time / total | 47.71 s / 50.66 s |
| Bundle digest | `sha256:53bffb58b6c7f3a32d162c5f7b08d725d8c71806a8780cdaa5bfd47d34319d6a` |
| Run directory | `runs/run-58f5589ef776/` |

Raw command output:

```json
{
  "agent_seconds": 47.71,
  "bundle_digest": "sha256:53bffb58b6c7f3a32d162c5f7b08d725d8c71806a8780cdaa5bfd47d34319d6a",
  "case_id": "debug-offbyone",
  "model_deployment_id": "qwen-flash-next-IQ2_S_-_2.5_bpw",
  "outcome": "pass",
  "profile": {},
  "run_dir": "/home/mitko/dev/esf/worktrees/hop-1.0/runs/run-58f5589ef776",
  "run_id": "run-58f5589ef776",
  "total_seconds": 50.66,
  "verdict": "pass"
}
```

`agent_seconds = 47.71` is the observable proof that a real inference call
happened through the per-run broker; earlier attempts exited in 0.03 s because
the worker never started.

## Why this gate is host-executed

HOP's worker and verifier run under `bwrap` with no network. The agent session
that performed the delivery is itself confined by AppArmor
(`bwrap//&unpriv_bwrap (enforce)`), so no process started from it can create
nested namespaces. The owner enabled unprivileged user namespaces
(`kernel.apparmor_restrict_unprivileged_userns=0`) and the run was executed with
the harness's wider-access mode, where `bwrap` works and the probe reports
`enforced=true`.

## Path to green (four defects found and fixed)

Each attempt produced a real failure, was diagnosed with a reproduction, and
was fixed with a regression test and an ESF gate run:

| # | Defect | Fix |
|---|---|---|
| 1 | Venv interpreter chain crossed a symlinked mise `latest`; the install root was never bound, so every host-side sandbox exec failed (`bwrap: execvp … No such file or directory`) | task-40 (`d920e92`) — bind each chain component and its realpath plus symlinked ancestors |
| 2 | `_harness_ro_binds` assumed `~/.npm-global`; the mise-installed Pi bundle under `node_modules/.mise/…` was unbound | task-42 (`cb114fd`) — resolve the executable's symlink chain and bind the package install root, layout-agnostically |
| 3 | The broker-relay spawn bound the interpreter chain twice; `build_bwrap_cmd` never skipped duplicate destinations, so the second mount failed | task-44 (`194babe`) — skip already-bound destinations |
| 4 | The relay exec'd bare `pi` through PATH, hitting a stale `/home/mitko/.radicle/bin/pi` shim | task-45 (`194babe`) — exec the qualified absolute harness path |

## Verification status

- Host probe at `d920e92`: `probe_worker_network_isolation()` →
  `enforced=true`, `host_reachable=false`, `public_reachable=false`,
  `loopback_up=true`, `unix_ok=true`.
- Host non-live suite: `scripts/dev test --suite all` → 517 passed / 1 failed
  before task-43 (the single failure was a test-side false positive in the
  adversarial network-denial test, fixed by task-43) — see the run log in
  `.factory/` for the exact command.
- Independent verification of the sandbox/isolation changes is recorded in
  `docs/verification/reviews.md` (R-008…R-022); the Cube gate proves the same
  paths wherever namespaces are available.

## Limitations

- One live case (`debug-offbyone`) on one deployment. This qualifies the
  pipeline end to end; it is not a model-quality benchmark and no optimization
  or promotion claim is made.
- The freeze gate re-runs this acceptance at the frozen revision; this record is
  bound to `138a4ef`.
