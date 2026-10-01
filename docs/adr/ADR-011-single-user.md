# ADR-011: Single-User Local Coding

Status: accepted scope decision, 2026-10-01.
Supersedes enterprise scope in the original build plan and backlog, including
ADR-001's future PostgreSQL path, ADR-007's service stack, and ADR-010's optional
enterprise integrations. Historical acceptance evidence is not rewritten.

## Decision

HOP serves one local developer, their machine, and explicitly selected coding
repositories. Keep a CLI-first modular monolith, SQLite, local artifacts,
JSONL trajectories, and file-based observability. No enterprise mode or feature
toggle is needed because no multi-user service is being built.

Remove accounts, tenants, teams, RBAC, corporate workflow requirements,
PostgreSQL/S3/Kubernetes deployment, collector/dashboard services, fleet channels,
signing infrastructure, approval chains, hosted-provider write brokers, and
webhooks from the active product. Remote publishing is an explicit owner action
outside the evaluation system, including publishing HOP's own source.

Core workflows are coding/refactoring, debugging, local diff review, and
test/dependency maintenance. Local build fixtures, extra harnesses/models, APM
export, and GEPA are optional. Standalone security-scanner workflow AOP-024 and
provider broker AOP-042 are out of scope. Security regression tests remain
mandatory; removing a scanner product feature does not remove boundary tests.

M8 means held-out confirmation, owner confirmation, local atomic activation,
and rollback. M10 means local file protection, retention, backup/restore,
offline setup, and resource limits. It does not mean enterprise administration.
Out-of-scope and disabled optional capabilities must not block the core roadmap.

## What Remains Mandatory

Local-only inference, worker isolation, hidden-test protection, trusted
verification, immutable evidence, explicit failures, secret protection,
resource limits, cancellation, and safe rollback still protect a single user.
Independent review of changed security boundaries remains an engineering gate,
not an in-product approval workflow. Existing foundation blockers are not waived.

## Compatibility

Active example profiles use the local-safety component. The old enterprise-coding
component and example lock are retained byte-for-byte only under
tests/fixtures/legacy, outside default registry import. Historic evidence remains
under docs/evidence. The org_policy schema value and compiled filenames remain
wire-format compatibility identifiers, not tenant or organization settings.
Do not rewrite old locks or silently change their digests.

## Validation

Regression checks cover the active component catalog, mandatory local policy,
the unchanged legacy lock digest, backlog dependency closure, and the absence
of enterprise settings. Scope edits do not qualify new packs or live models.
