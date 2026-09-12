# ADR-003: In-repo `.hidden/` holds M1 hidden tests; sealed store deferred

Date: 2026-09-12.

BUILD_PLAN §9 requires the sealed holdout to live in a separate
evaluator-owned store, never as a readable directory in the repository.
For the M1 slice the hidden tests live in `.hidden/hidden-tests/` in this
repository because there is no evaluator service yet.

What keeps this honest for M1:

- The runner copies only `benchmarks/development/<case>/repo/` into the agent
  workspace. `.hidden/` is never copied, mounted, or embedded in prompts.
- `contamination_check` scans every frozen snapshot for hidden filenames and
  content markers before the verifier runs; a leak yields ERROR, never PASS.
- `tests/test_verification.py::test_hidden_tests_not_in_agent_source_tree`
  and the e2e workspace walk fail the build if hidden files appear under
  `benchmarks/` or any worker workspace.

M4 (AOP-020) moves sealed material to an evaluator-owned store with an access
ledger. Until then, `.hidden/` must be treated as read-only evaluation data:
do not import it from adapter/worker code, do not log its contents.
