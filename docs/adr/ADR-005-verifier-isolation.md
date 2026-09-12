# ADR-005: Trusted verifier isolation and positive verification

Date: 2026-09-12.

Status: accepted.

## Context

The M1 verifier originally decided PASS from `pytest`'s process exit code and
ran in the control-plane process with the candidate snapshot as the working
directory. That is unsafe in three ways:

1. a candidate can exit the interpreter with status 0 (`os._exit(0)`,
   `sitecustomize.py`, a malicious module import) and look "green";
2. candidate `conftest.py` / `pytest.ini` can change collection, deselect
   mandatory tests, or skip everything;
3. read-only mounting of hidden tests still leaks content and let the candidate
   read/modify verifier material.

## Decision

Verification is a separate confined run under `bwrap`, driven by evaluator-owned
code, and the verdict comes from a machine-readable report.

* **Namespace isolation.** The verifier process runs under `bwrap` with a fresh
  mount/PID namespace and no network. Only the frozen candidate snapshot, the
  single hidden case directory, and the verifier bootstrap/plugin are mounted
  (read-only). The repository, other hidden cases, and other runs are absent.
* **Isolated interpreter.** `python -I` disables user site, `PYTHON*`
  environment influence, and cwd/script injection, so candidate
  `sitecustomize.py` / `usercustomize.py` are never imported. The snapshot is
  added to `sys.path` by the bootstrap only after interpreter startup.
* **Verifier-owned pytest configuration.** `-c <verifier pytest.ini>` plus
  `-o addopts=` and a verifier-owned plugin make candidate `conftest.py` and
  `pytest.ini` irrelevant: hidden tests are copied to a directory that is not a
  descendant of the candidate snapshot.
* **Positive verification, not exit status.** A pytest plugin writes a
  per-test machine-readable report. The verdict requires: non-zero collected
  count, at least `minimum_test_count`, exact collection match against the
  pinned `expected_tests`, no deselections, every collected test executed, no
  all-skipped run, and every `mandatory_tests` entry passed. A missing or
  malformed report (for example `os._exit(0)`) is a rejection, never a PASS.
* **Pinned expectations.** Each hidden case carries `verifier.json` with
  `verifier_id`, `version`, `test_files`, `expected_tests`, `mandatory_tests`,
  and `minimum_test_count`. Unexpected collection changes are rejected.
* **Agent isolation.** The agent worker runs under its own `bwrap` namespace
  that binds only its run root (plus the pinned harness/Node runtime read-only).
  Hidden material and other runs are not present, so "read-only leak" cannot
  occur either.

## Consequences

- `bwrap` is required. If it is unavailable the verifier returns
  `infra_error` (`verifier_sandbox_unavailable`); it never degrades to an
  unisolated run.
- Verification evidence is stored in `evaluation.json` and in the run's
  verifier directory (`verification.json`), and its digest is recorded on the
  trajectory event.
- Adversarial coverage lives in `tests/test_verification.py` and
  `tests/security/test_boundaries.py`.
- Candidate code can still read the current case's hidden tests *while the
  verifier runs*, because those tests must be mounted to execute. This cannot
  change the already-frozen candidate and provides no feedback loop; reading
  other cases, the repository, or verifier code remains impossible. Sealed
  evaluator-owned storage and an access ledger remain M4 work.
