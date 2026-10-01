# ADR-010: HOP 0.2 foundation and delivery gates

Status: implemented in part; release qualification blocked.
Date: 2026-10-01.

## Decision

Keep the Python modular monolith, SQLite, filesystem artifacts, native harness
loops, independent verifier, and local-only inference. The default experience
is CLI-first. Extra adapters, GEPA, hosted observability stacks, and enterprise
brokers remain dependency-gated optional capabilities, not default dependencies.

Resolve workspace paths once and pass them into execution. Use argparse's
actual nested subparsers. HOP 0.2 introduces `run start`, `run list`, `run compare`,
`deployment list`, `init`, `doctor`, explicit formatting, and paged trajectories.
Existing command forms retain compatibility shims. This supersedes ADR-008's
alias-removal schedule: remove `aop` and `AOP_*` in 0.3 only after M4 acceptance.
Stable wire identifiers and AOP work-item IDs do not change.

Execution passes typed state through preparation, native execution, frozen
verification, and finalization. Cancellation watches have bounded lifetimes.
RunStore serializes read/modify/write operations in SQLite transactions across
connections. Artifact references and profile files use atomic publication.
Interrupted event writes remain explicitly incomplete; readers do not invent
missing acknowledgements or silently reseal historical evidence.

Worker environment inheritance uses an allowlist. Resource limits are applied
in a fresh Python process before exec; failures stop execution. Local HTTP
clients disable proxies and redirects. New bundles use policy ID
`local-default-v2` to identify these changed controls. Existing digest algorithms,
historical bundle IDs, schemas, events, and lockfiles remain unchanged.

Offline bootstrap builds a versioned environment, validates it, and atomically
switches a managed link. Launchers select the versioned directory so a later
switch does not redirect a running process. Existing directories and previous
environments are retained. Runtime-only installation excludes development/test
dependencies. Qualification currently covers Linux x86-64 / Python 3.14.

## Gates and Limitations

The foundation cannot pass until offline development tooling, independent
boundary review, and current local-model acceptance are complete. Historical
M0-M3 acceptance remains evidence for its recorded revisions, not this revision.
M4 then starts with AOP-020; M5-M10 retain their acceptance and dependency order.

This change does not implement sealed storage, automated optimization,
promotion, release signing, or external write brokers. The known shared-network
and aggregate-resource limitations remain explicit high-priority review findings.
No signature, fixture result, or local judge can substitute for those gates.
