# G8 — live local-model acceptance (release gate)

Recorded by the Lead. This is the one release gate that requires a real local
model and a namespace-capable host shell; it is deliberately not inferred from
fixtures.

## Result: PASS

| Field | Value |
|---|---|
| Revision | `d1bf6ccbee0c3790fbc757a596e059ddbe7f7db5` (public branch `public/1.0.0`) |
| Command | `./hop run start --case debug-offbyone --model qwen-flash-next` |
| Run id | `run-916a66ca4487` |
| Outcome / verdict | `pass` / `pass` (trusted verifier, not agent narration) |
| Model deployment | `qwen-flash-next-IQ2_S_-_2.5_bpw` (Qwen/Qwen3.8-Flash-Next, IQ2_S 2.5 bpw, `mitko` at `http://127.0.0.1:8000/v1`) |
| Harness | `pi` 1.1.0, status `qualified` (sha256 `c44ddd4e…`) |
| Agent time / total | 44.8 s / 47.71 s |
| Bundle digest | `sha256:53bffb58b6c7f3a32d162c5f7b08d725d8c71806a8780cdaa5bfd47d34319d6a` |
| Run directory | `runs/run-916a66ca4487/` |

This is the release-bound run: it was executed at the public release revision.
Earlier attempts of the same case are retained only as superseded debugging
history (see below); they are not the release binding.

Raw command output (release-bound run):

```json
{
  "agent_seconds": 44.8,
  "bundle_digest": "sha256:53bffb58b6c7f3a32d162c5f7b08d725d8c71806a8780cdaa5bfd47d34319d6a",
  "case_id": "debug-offbyone",
  "model_deployment_id": "qwen-flash-next-IQ2_S_-_2.5_bpw",
  "outcome": "pass",
  "profile": {},
  "run_dir": "/home/mitko/dev/esf/worktrees/hop-1.0/runs/run-916a66ca4487",
  "run_id": "run-916a66ca4487",
  "total_seconds": 47.71,
  "verdict": "pass"
}
```

`agent_seconds = 44.8` is the observable proof that a real inference call
happened through the per-run broker; earlier failed attempts exited in 0.03 s
because the worker never started.

Superseded records (not the release binding): earlier attempts of the same case
are retained in the internal review record and are superseded by the
release-bound run above.

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
was fixed with a regression test and an ESF gate run. These fix commits are
development history:

| # | Defect | Fix |
|---|---|---|
| 1 | Venv interpreter chain crossed a symlinked mise `latest`; the install root was never bound, so every host-side sandbox exec failed (`bwrap: execvp … No such file or directory`) | task-40 — bind each chain component and its realpath plus symlinked ancestors |
| 2 | `_harness_ro_binds` assumed `~/.npm-global`; the mise-installed Pi bundle under `node_modules/.mise/…` was unbound | task-42 — resolve the executable's symlink chain and bind the package install root, layout-agnostically |
| 3 | The broker-relay spawn bound the interpreter chain twice; `build_bwrap_cmd` never skipped duplicate destinations, so the second mount failed | task-44 — skip already-bound destinations |
| 4 | The relay exec'd bare `pi` through PATH, hitting a stale `/home/mitko/.radicle/bin/pi` shim | task-45 — exec the qualified absolute harness path |

## Verification status

- Host probe for the worker network boundary: `probe_worker_network_isolation()`
  → `enforced=true`, `host_reachable=false`, `public_reachable=false`,
  `loopback_up=true`, `unix_ok=true`.
- Final host validation at the public release revision `d1bf6cc`:
  `scripts/dev check` → **exit 0, 0 findings** (74 source files; the fail-closed
  pin requires the approved **ruff 0.16.10** on `PATH` and correctly refuses the
  newer mise `latest` ruff 0.17.0), and `scripts/dev test --suite all` →
  **565 passed, 1 deselected, exit 0**. Earlier intermediate states were
  539 passed and 517 passed / 1 failed (that one failure was a test-side false
  positive in the adversarial network-denial test, fixed in task-43).
- Independent verification of the sandbox/isolation changes is recorded in
  the internal review index (not published) (R-008…R-022); the Cube gate proves
  the same paths wherever namespaces are available.

## Limitations

- One live case (`debug-offbyone`) on one deployment. This qualifies the
  pipeline end to end; it is not a model-quality benchmark and no optimization
  or promotion claim is made.
- This record is bound to the public release revision
  `d1bf6ccbee0c3790fbc757a596e059ddbe7f7db5`.
