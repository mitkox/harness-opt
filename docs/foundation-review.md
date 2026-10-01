# HOP Foundation Review

Baseline: `4e6d6a243e5f46b80318fb35981251ef0392d200`.
Implementation branch: `esf/hop-foundation`.
Work items: HOP-R01 through HOP-R05. Review date: 2026-10-01.

Scope follow-up: ADR-011 removes enterprise services, signing infrastructure,
and multi-user authorization from the product. The local safety findings below
remain open; historical future-integration references are not current requirements.

This is the implementer's review and regression evidence. It does not constitute
independent security verification or renewed M0-M3 release qualification.

## Findings and Disposition

| Priority | Finding | Disposition and Evidence |
|---|---|---|
| High | Worker environment denylisting inherited arbitrary shell functions and host configuration. | Explicit allowlist; hostile-environment boundary tests pass. New policy identity v2. |
| High | Run admission and attempt fencing used separately locked reads/writes; independent SQLite connections could lose updates. | BEGIN IMMEDIATE transactions; concurrent admission and unique-token regressions. Stale failure writes are rejected. |
| High | Artifact owner/digest paths could escape their store; existing corrupt blobs could acquire new references. | Identifier/digest/containment checks, symlink escape and tampered-blob regressions, atomic reference publication. |
| High | A failed first event write could be read as legacy data without an integrity chain. | Durable pending marker and atomic chain publication; interrupted writes remain incomplete. Operator-assisted recovery still requires separate evidence. |
| High | Resource-limit setup swallowed errors and used preexec_fn in a threaded process. | Fresh limit-setting process before exec; failures stop child execution. Existing cancellation and sandbox tests pass. |
| Medium | A collector write followed by a crash before acknowledgement duplicated delivered spans. | Locked atomic deduplicated local projection; injected acknowledgement failure regression. |
| Medium | Corrupt profile indexes became empty indexes; replay could fall back from a missing exact lock. | Explicit corruption errors, strict digest paths, serialized index updates, and exact-reference replay. |
| Medium | Migration crashed on an absent runs directory and classified arbitrary corruption as legacy success. | Empty/missing history is read-only and empty; corrupt finalized history returns failure. Committed historical fixture copied per test. |
| Medium | Nested CLI help failed and execution/investigation resolved paths differently. | Real subparsers, shared Paths, formatting and configuration regressions. |
| Medium | Tests required machine-local deployment records and an installed Pi version; a CLI test rewrote a committed lockfile. | Synthetic runner identities, separate live marker, temporary lock output. Historical files remain unchanged. |
| Medium | Bootstrap deleted its destination before proving replacement viability. | Staged offline environments and managed link switch; failure-preservation tests. |
| Medium | Completed runs left cancellation polling threads alive. | Scoped cancellation watch joined on every exit. |
| High / Open | Pi workers share host networking; endpoint validation is not OS-level general egress enforcement. | Previously documented M1 limitation; no external-network security qualification claimed. Requires a reviewed host enforcement or isolated inference-broker design. |
| High / Open | RLIMITs are per-process/per-user controls, not aggregate per-run CPU/memory/disk quotas; output truncation occurs after execution. | Remains unresolved. Requires resource accounting/enforcement for descendants before hostile workload qualification. |
| High / Open | Verifier imports candidate code in the same evaluator process; ADR-005 documents current-case visibility. | Requires independent threat-model review before claiming M4 sealed evaluation protection. No sealed-store implementation added. |

## Review Coverage

Contracts and identity, native Pi interfaces, run state and cancellation, sandbox
environment/network/process boundaries, frozen verification, artifact and profile
storage, trajectory integrity, telemetry projection/redaction, deterministic
resolution/compiler/APM export, CLI, setup, tests, and milestone evidence were
inspected. Existing tests cover compiler determinism, policy merging, tampering,
redaction, verifier bypass attempts, and export exclusions. Full-file model
fingerprints, additional harness qualification, signing, and production promotion
remain assigned to their original later milestones.

The runner now uses typed RunContext/PreparedRun/ExecutedRun/VerifiedRun state and
explicit preparation, execution, verification, and finalization methods. Shared
event binding removes repeated run/attempt identity arguments. Profile preparation
uses the same locking/compilation facade as the CLI. No native harness loop was
replaced and no generic plugin framework or new runtime service was introduced.

## Remaining Dependencies

HOP-R02 needs an administrator-supplied offline type-checker artifact and dependency
lock. HOP-R04 needs closure of the open boundary findings and independent review.
Current Pi/local-model acceptance is outstanding. These block foundation acceptance
and AOP-020. M4-M10 are not implemented by this change. No optimization, promotion,
provider writes, public scans, or deployment were performed.

The final installed-interface probe observed Pi 1.0.0 and failed the prior 0.85.1
contract assertion. An earlier observation in this work session was 0.99.2; the
installation changed independently. HOP did not install or update Pi.

Changes include a mechanical Ruff normalization of runtime/tests, scoped bug fixes,
new foundation tests, runtime lock extraction, and developer commands. Review the
behavioral changes separately from formatting. Source size increases because of
new controls and commands; installation and trajectory-memory savings are measured
separately. See the acceptance matrix for commands and measurement limits.
