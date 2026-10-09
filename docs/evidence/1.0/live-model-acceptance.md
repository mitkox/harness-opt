# G8 — live local-model acceptance (release gate)

Recorded by the Lead. This is the one release gate that requires a real local
model and a namespace-capable host shell; it is deliberately not inferred from
fixtures.

## Result: PASS

| Field | Value |
|---|---|
| Revision | `779e73f61f5112b681242e0376ed9c78c6e2d9e1` (frozen branch `esf/hop-1.0`) |
| Command | `./hop run start --case debug-offbyone --model qwen-flash-next` |
| Run id | `run-85d1c3a117ef` |
| Outcome / verdict | `pass` / `pass` (trusted verifier, not agent narration) |
| Model deployment | `qwen-flash-next-IQ2_S_-_2.5_bpw` (Qwen/Qwen3.8-Flash-Next, IQ2_S 2.5 bpw, `mitko` at `http://127.0.0.1:8000/v1`) |
| Harness | `pi` 1.1.0, status `qualified` (sha256 `c44ddd4e…`) |
| Agent time / total | 44.71 s / 47.72 s |
| Bundle digest | `sha256:53bffb58b6c7f3a32d162c5f7b08d725d8c71806a8780cdaa5bfd47d34319d6a` |
| Run directory | `runs/run-85d1c3a117ef/` |

This is the release-bound run: it was executed at the frozen revision. An
earlier run of the same case before the freeze (`run-58f5589ef776` at
`138a4ef`, `pass`/`pass`, `agent_seconds 47.71`) is recorded in the "Path to
green" section below; the frozen run supersedes it for the release.

Raw command output (frozen run):

```json
{
  "agent_seconds": 44.71,
  "bundle_digest": "sha256:53bffb58b6c7f3a32d162c5f7b08d725d8c71806a8780cdaa5bfd47d34319d6a",
  "case_id": "debug-offbyone",
  "model_deployment_id": "qwen-flash-next-IQ2_S_-_2.5_bpw",
  "outcome": "pass",
  "profile": {},
  "run_dir": "/home/mitko/dev/esf/worktrees/hop-1.0/runs/run-85d1c3a117ef",
  "run_id": "run-85d1c3a117ef",
  "total_seconds": 47.72,
  "verdict": "pass"
}
```

`agent_seconds = 44.71` is the observable proof that a real inference call
happened through the per-run broker; earlier attempts exited in 0.03 s because
the worker never started.

Superseded pre-freeze run (`run-58f5589ef776` at `138a4ef`, kept for the
debugging trail only — not the release binding): `agent_seconds 47.71`,
`total_seconds 50.66`, same bundle digest and verdicts.

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
- Host non-live suite after the bind fixes and the task-43 test correction:
  `scripts/dev test --suite all` → **539 passed, 0 failed, exit 0**, and
  `scripts/dev check` → **exit 0, 0 findings**. (Intermediate states were
  517 passed / 1 failed — the one failure was a test-side false positive in the
  adversarial network-denial test — and 12 failed + 7 errors before the bind
  fixes.) Exact commands and output are in `.factory/host-final-r4.log`.
- Independent verification of the sandbox/isolation changes is recorded in
  the internal review index (not published) (R-008…R-022); the Cube gate proves the same
  paths wherever namespaces are available.

## Limitations

- One live case (`debug-offbyone`) on one deployment. This qualifies the
  pipeline end to end; it is not a model-quality benchmark and no optimization
  or promotion claim is made.
- The freeze gate re-runs this acceptance at the frozen revision; this record is
  bound to `138a4ef`.
