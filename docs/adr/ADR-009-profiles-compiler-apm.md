# ADR-009: M3 profiles, immutable component registry, and APM boundary

Date: 2026-09-12.

Status: accepted.

## Context

M0–M2 established local-only execution, trusted verification, durable
trajectories, and immutable *execution* identity. Nothing yet described the
distributable agent-side configuration (prompts, skills, agents, policy) as a
versioned artifact with its own identity. APM is available locally as a
packaging/distribution tool, but using it as an evaluation-identity source
would couple HOP's authority to a downstream packager.

## Decision

1. **Two identities, explicitly separated.** `Profile`/`ResolvedProfile`/
   `Lockfile` live in `hop.contracts.profile` and describe agent-side
   configuration. `ExecutionBundle` keeps its M1 role and gains *optional*
   `profile_id`, `profile_digest`, `lock_digest`, `compiled_target`, and
   `compiled_target_digest` fields that participate in the bundle digest only
   when present. Model weights, GPU runtime, verifier, and benchmark identity
   never enter a profile or an APM export.
2. **Immutable, content-addressed registry.** `hop.registry` stores records
   (`name`, `version`, type, content digest, metadata, dependencies,
   compatibility, provenance, schema version) plus content. Re-registration
   with different content and the same identity is a hard `ComponentCollision`;
   reads recompute digests; the index is integrity-anchored.
3. **Deterministic resolution.** Floating references resolve to the highest
   registered satisfying version; transitive dependencies are resolved with a
   worklist; cycles, conflicts, type ambiguity, incompatible harnesses, and
   missing required tools fail closed. Variants are selector-driven and an
   ambiguous match is an error, never an arbitrary pick.
4. **Explicit classified policy merge.** `mandatory` > `workflow` >
   `overlay` > `component`; `additive` accumulates; `forbidden_override`
   cannot be declared elsewhere. Weakining a mandatory rule is a compile
   failure. No implicit YAML merge.
5. **Deterministic compiler.** `ProfileCompilerTarget` owns a fixed pipeline
   and tree-hash identity; `PiCompilerTarget` is the real target and the other
   harnesses are fail-closed extension points. Compilation never calls a model
   and never reads the clock.
6. **Lock digest as the history key.** The local store is keyed by
   `lock_digest`, so a re-resolution cannot overwrite a historical lock;
   `materialize` fails closed if a historical component is missing or mutated.
7. **APM is a distribution target, not an authority.** `hop profile export`
   emits an APM project plus HOP provenance and the HOP lock. The
   deterministic payload digest excludes the generation timestamp. HOP keeps
   profile/component/evaluation identity; APM packages and distributes.
   Real APM tooling (`apm lock`, `apm pack`) is exercised on a copy by the
   demo, keeping the HOP export immutable.

## Consequences

- M1/M2 run records and events remain readable: all new fields default empty.
- The lock, not the source profile, is the frozen identity used for
  reproduction; `profile_digest` identifies the source configuration only.
- Unsigned provenance is tamper-evident (digests) but not tamper-proof;
  production signing is deferred to the promotion milestone (M8).
- No optimizer, router, or promotion logic is introduced in M3.
