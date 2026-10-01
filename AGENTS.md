# Build-agent operating contract

## Objective

Implement the single-user local coding tool specified in `BUILD_PLAN.md` and ADR-011. No tenants, accounts, enterprise workflows, provider write-back, signing service, fleet deployment, or role administration. Preserve worker/verifier isolation, local-only inference, and evidence integrity.

M0-M3 are delivered in scoped form. HOP-R01-R05 foundation requalification remains blocked; read its acceptance matrix before M4. Preserve existing repository work and historical evidence. Optional integrations do not gate the single-user core unless enabled.

## Hard constraints

All task inference, routing, judge calls, reflection, embeddings, compaction, and synthetic-data generation use registered local model deployments. No hosted API fallback. No external telemetry, session sharing, package downloads, or auto-updates during evaluation. Controlled artifact import is a separate explicit owner action.

Do not infer model capabilities from names. Exact checkpoints, quantizations, serving builds, chat templates, tool parsers, and supported generation parameters require pinned identities and test evidence. Never copy guessed reasoning settings into production configuration. Unsupported or unavailable combinations remain explicit, not silently substituted.

Do not replace the native harness agent loop. Wrap its supported headless/RPC/plugin interface. Isolate configuration, home, workspace, memory, credentials, and caches per run. A worker's Python REPL is not its security boundary; untrusted code requires external isolation.

Do not modify mandatory safety/policy/permission constraints to make an evaluation pass. Protect verifier code, hidden tests, sealed holdout cases, and host credentials from agent and optimizer workers. Hidden tests must not be readable inside an agent environment, even on a read-only mount.

Do not publish comments, push branches, create external tickets, change real CI, scan public targets, merge code, or deploy artifacts as a side effect of tests. Use controlled fixtures and disabled side-effect brokers. Real writes require explicit scoped authorization.

## Implementation discipline

Use versioned typed contracts and test-first vertical changes. Pin dependencies and record their source versions. Public documentation describes capabilities, but the installed/pinned build determines the adapter contract. Run local help/schema/protocol probes and maintain fixture transcripts for integrations.

Keep the initial application a modular monolith with separate trust-boundary worker processes. Do not add Kafka, a distributed workflow engine, a vector database, a custom model server, or another dashboard platform unless an accepted architecture decision establishes the need.

Never implement release compilation by asking an LLM to rewrite inputs at deployment time. Candidate generation may be stochastic; approved bundle compilation must be deterministic. Generated code and scripts require tests, sandboxing, provenance, and independent review.

Handle failures explicitly: missing models, unsupported parameters, token-accounting gaps, malformed streams, duplicate/out-of-order events, cancellation, disk-full, worker crash, artifact tampering, verifier failure, and unavailable mirrors. Do not transform infrastructure errors into model successes or remove them silently from reports.

Use source sequence numbers, durable acknowledgements, deduplication, and completeness reports for trajectory ingestion. OpenTelemetry is a projection; it is not the sole evaluation evidence store. Store large or sensitive payloads as access-controlled artifacts, not metric labels or unrestricted span attributes.

The final task verdict comes from the trusted verifier. Agent text claiming success, a harness acknowledging the prompt, or an empty error log cannot substitute for verification. Record all attempts and intervention events.

## Work-item completion procedure

For each assigned task:

1. Read its dependencies, relevant `BUILD_PLAN.md` sections, and upstream source references.
2. Confirm the task is unblocked and the required local tools/models actually exist.
3. Add focused tests, including at least one failure/adversarial case for boundary code.
4. Implement the smallest coherent change; preserve unrelated behavior.
5. Run unit, contract, and available integration tests. Record exact commands and outputs.
6. Review the diff and any generated artifacts for secrets, scope drift, and unpinned dependencies.
7. Update documentation, schema version/migration notes, and the work-item evidence.
8. Leave a reviewable commit or patch. Do not claim unavailable tests passed.

Separate implementation and independent verification for security boundaries, dataset partitions, evaluation graders, and local activation logic. A local model judge is advisory when deterministic evidence or owner confirmation is required.

## Parallel work

Use separate Git worktrees. The lead owns contracts and integration; module agents own narrow paths. Do not edit shared schemas concurrently. Stage dependent work after interface tests exist. Parallel jobs must respect local inference memory and CPU/GPU admission limits.

Do not keep multiple heavyweight actor/judge/optimizer deployments resident merely to make the project look concurrent. Local capacity and measured latency determine scheduling.

## Quality gates

Required gates include schema validation; formatter/linter/type checks; unit tests; adapter contract tests; sandbox and network tests; offline operation checks; verifier-integrity tests; event-recovery and redaction tests; bundle reproducibility and tamper tests; promotion/rollback tests; and end-to-end workflow validation.

A skipped integration is acceptable only with an explicit reason and a blocked capability entry. It is not approval. An underpowered or mixed evaluation must be reported as inconclusive rather than converted into a positive claim.

## Final work report format

Report the work-item IDs, files changed, implementation decisions, validations run, observed results, unexecuted tests with reasons, compatibility limitations, and next dependency-ready item. Distinguish implemented behavior from design stubs and sample configurations.

## Current Assignment

Close the foundation acceptance gaps before implementing AOP-020 and the core local coding packs. Keep AOP-024 and AOP-042 out of scope under ADR-011. Do not begin optimization or activation before their dependency evidence exists. A scope reduction is not a passed safety gate.
