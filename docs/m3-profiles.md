# M3 — Profiles, component registry, compiler, and APM distribution

HOP M3 turns a tested agent configuration into an **immutable, reproducible
profile** that can later be evaluated, optimized, promoted, and distributed.
M3 does **not** optimize anything: no candidate generation, no GEPA, no
automatic skill rewriting, no dynamic routing, no promotion. It defines and
freezes identity.

```text
define → resolve → compile → freeze → inspect → export → reproduce
```

## Two different identities

HOP deliberately separates the distributable agent configuration from the
environment used for a specific evaluation.

| Contract | Lives in | Contains |
|---|---|---|
| `Profile` / `ResolvedProfile` | `hop.contracts.profile` | system prompt, agents, skills, tool/context/org policy, harness overlay, hooks, MCP, model reference |
| `Lockfile` | `hop.contracts.profile` | the frozen resolution: exact versions + digests, dependency graph, policy digest, target, compiler version |
| `ExecutionBundle` | `hop.contracts.records` | resolved-profile digest, model deployment digest/weights, inference runtime, harness build, sandbox, repo snapshot, environment, toolchain, verifier, evaluation identity |

`Profile → ResolvedProfile → (combine with model/runtime/environment) →
ExecutionBundle`. APM is never responsible for model weights, GPU runtime,
verifier identity, or benchmark identity.

## Component registry

Every configuration primitive is an immutable registry component:

- `system_prompt`, `skill`, `agent`, `tool_policy`, `context_policy`,
  `org_policy`, `harness_overlay`, `hook`, `mcp`, `resource`,
  `skill_script`.

`hop.registry.ComponentRegistry` stores records in a content-addressed local
store (`$HOP_HOME/registry`, gitignored). Each record carries a logical name,
semantic version, component type, content digest, metadata, dependencies,
compatibility metadata, source/provenance, creation context, and schema
version.

- Re-registering identical content is idempotent.
- Re-registering the **same identity** with different content fails closed
  (`ComponentCollision`); changing content requires a new version.
- Reads recompute the content digest from stored bytes; silent mutation is
  detected (`RegistryIntegrityError`).
- The registry index has its own integrity anchor, so an edited index is
  detected on load.

### Skill package format

```text
components/skills/debugging/
├── skill.yaml              # manifest: triggers, deps, tools, permissions,
│                           # resources, scripts, compatibility, provenance
├── canonical/SKILL.md
├── resources/
├── scripts/
└── variants/
    ├── qwen/    { variant.yaml, SKILL.md }
    ├── deepseek/{ variant.yaml, SKILL.md }
    └── glm/     { variant.yaml, SKILL.md }
```

A skill's manifest may declare `model_family_hints` / `harness_hints`, but
these are descriptive at M3. HOP does not decide that a skill (or variant) is
beneficial; that belongs to evaluation/optimization milestones.

## Resolution

`hop.resolver.resolve_profile` is pure and offline. It:

- resolves floating references (`debugging@^2.0.0`) to the highest satisfying
  registered version;
- recursively resolves transitive dependencies via a deterministic worklist;
- detects missing dependencies, dependency cycles, version conflicts,
  ambiguous component types, and wrong component types;
- selects skill variants by compatibility selector and **fails on ambiguity**
  rather than picking arbitrarily;
- fails on an incompatible harness or a required tool absent from the resolved
  tool policy;
- produces a lexicographically tie-broken topological dependency graph.

Selector dimensions: `model_family`, `model`, `harness`, `workflow`,
`capability`, `runtime`. Selection is order-independent because more than one
match is an error.

## Policy merge

Policy rules carry an explicit classification: `mandatory`, `default`,
`overridable`, `additive`, `forbidden_override`.

Precedence (lowest → highest) is `component` → `harness overlay` →
`workflow/profile`; **mandatory** rules always win last. A lower-precedence
layer that sets a different value for a mandatory key fails compilation with
`policy_violation`. `forbidden_override` keys cannot be declared by any other
layer. `additive` values accumulate in layer order. No implicit YAML merge is
used.

## Lockfile

`hop profile lock` writes a canonical JSON lock (default `<profile>.hop.lock`)
that contains only exact versions and digests. `lock_digest` binds the whole
file; `verify_lock` rejects floating references, duplicate components, missing
graph nodes, and stale digests (locked digest ≠ registry digest).

## Compiler

`hop compiler.py` defines `ProfileCompilerTarget` with the stages
`compile_profile`, `compile_system_prompt`, `compile_agents`, `compile_skills`,
`compile_hooks`, `compile_mcp`, `emit_manifest`, plus a capability gate.
`PiCompilerTarget` is the real M3 target; `prime`, `opencode_v2`, and
`deepseek_harness` are explicit extension points that fail closed as
`unsupported_target`.

Compilation is deterministic: all stage outputs are merged in a fixed order,
paths are validated (no absolute paths or `..`), duplicate paths with different
bytes fail (`duplicate_resource_collision`), and the artifact digest is a
sorted, length-prefixed tree hash of the output bytes. No model is invoked and
no clock is read.

## APM export

`hop profile export <profile> --target apm` produces an APM-consumable project
(`apm.yml`, `instructions/`, `skills/`, `agents/`, `hooks/`, `.mcp.json`) plus:

- `.hop/hop.lock` — the authoritative HOP lock, and
- `.hop/provenance.json` — HOP profile id/digest, compiler version, lock
  digest, export target, source component digests, and the per-file package
  manifest.

The generation timestamp lives in `.hop/export-meta.json`, **outside** the
hashed deterministic payload, so two exports of the same lock have the same
`package_digest`. The package never contains weights, secrets, hidden tests,
verifier internals, telemetry, or repository snapshots.

`hop profile verify-export <path>` checks provenance validity, the exact file
set (no unexpected or missing files), per-file and package digests, the
embedded lock, and — with `--profile` — deterministic recompilation.

When the `apm` CLI is present, `scripts/demo_m3.py --apm` additionally runs
`apm lock` and `apm pack` on a copy, proving the export is consumable by real
APM tooling. HOP remains the source of truth for profile/evaluation identity;
APM handles packaging and distribution.

## CLI

```bash
hop registry import components
hop registry verify
hop component list [--type skill]
hop component show debugging@2.0.0
hop skill list
hop skill inspect debugging
hop profile validate examples/profiles/coding.yaml
hop profile resolve examples/profiles/coding.yaml [--set model_family=qwen]
hop profile lock examples/profiles/coding.yaml [--out ...]
hop profile deps examples/profiles/coding.yaml
hop profile explain examples/profiles/coding.yaml
hop profile diff examples/profiles/coding.yaml examples/profiles/pr-review.yaml
hop profile compile examples/profiles/coding.yaml --out /tmp/pi-out
hop profile materialize <profile-or-digest> --out /tmp/out
hop profile export examples/profiles/coding.yaml --target apm --out /tmp/apm
hop profile verify-export /tmp/apm --profile examples/profiles/coding.yaml
hop run --case debug-offbyone --profile examples/profiles/debugging.yaml
hop run show <run-id>
hop run replay-check <run-id>
```

## Immutability and history

- The resolution store is keyed by `lock_digest`, so re-locking a changed
  source profile cannot overwrite a historical lock.
- `hop profile materialize <lock-digest>` reconstructs the exact locked config
  from the registry and fails closed if any historical component is missing or
  mutated (`historical_component_unavailable`).
- Runs record `profile_id`, `profile_digest`, `lock_digest`,
  `compiled_target`, and `compiled_target_digest` in the run record, manifest,
  report, and every trajectory event.
- M1/M2 run records and events lack these fields and remain fully readable
  (the fields default to empty).

## What M3 explicitly does not do

- No automatic optimization, GEPA, or skill rewriting.
- No dynamic or learned model routing.
- No automatic promotion, canary, or rollback.
- No full Prime/OpenCode/DeepSeek adapter implementations.
- No claim that an example profile is optimized or approved.
