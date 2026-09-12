# Contributing to HOP

## Local-only design principle

Every contribution must preserve the local-only invariant: task inference,
judges, reflection, embeddings, and optimization all run against registered
local model deployments. No hosted API fallback, no external telemetry, no
package downloads or auto-updates during evaluation. A contribution that adds
a cloud dependency needs explicit architectural approval (an ADR) before it
is accepted.

## Trust boundaries are not negotiable

Do not weaken verifier or security boundaries to make an evaluation pass:

- The final verdict comes from the trusted verifier, never from agent text,
  harness exit status, or an empty error log.
- Hidden tests must not become readable inside an agent environment.
- Policy, sandboxing, authorization, and evidence requirements are outside
  model-generated text; optimizers cannot change them.
- Keep control plane, agent worker, verifier worker, and (future) publisher
  identities, filesystems, networks, and credentials separate.

## Test requirements

- Add focused tests with every change, including at least one
  failure/adversarial case for boundary code.
- Run the full suite before asking for review:
  `scripts/build_lock_env.sh --run-tests`, or with an existing lock env
  `PYTHONPATH=src .venv-m1/bin/python -m pytest tests/ -q`.
- Record the exact commands and observed outputs in the work report.
- A skipped integration is acceptable only with an explicit reason and a
  blocked capability entry. It is not approval.

## Milestone discipline

Work is organized by `BACKLOG.yaml` milestones. Read the relevant
`BUILD_PLAN.md` sections and the work item's acceptance criteria before
changing code, confirm the task is unblocked, and keep changes minimal and
scoped. Do not start the next milestone's implementation inside the current
one, and do not silently expand scope.

## Commits and reviews

- Small logical commits with the work-item ID in the message where one
  applies (for example `m2: ...`, `AOP-014 ...`).
- Leave a reviewable diff; distinguish implemented behavior from design
  stubs and sample configurations in the description.
- Check the diff for secrets, scope drift, and unpinned dependencies.

## Adding adapters and eval packs later

- Harness adapters implement the lifecycle in `src/hop/harnesses/base.py`
  (probe, prepare, start, stream, snapshot, cancel, finalize) behind a
  pinned harness build. New adapters need contract tests, isolation tests,
  and a supported/blocked capability report; unsupported combinations stay
  explicit, never silently substituted.
- Evaluation packs follow the `benchmarks/development/<case>/` layout with
  `task.yaml`, `prompt.md`, `repository.manifest.json`,
  `environment.lock.json`, public fixtures, and visible tests. Hidden
  grader content lives in the evaluator-owned store (`.hidden/` is the
  interim M1 location with namespace enforcement, see
  `docs/adr/ADR-003-hidden-in-repo.md`); keep development, validation, and
  sealed promotion splits separated by repository, ancestry, and time.
