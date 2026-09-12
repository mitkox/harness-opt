# M1 acceptance matrix

Generated from the M1 hardening run. Evidence artifacts are committed under
`docs/evidence/m1/`; the full local run directories remain under `runs/`
(gitignored). Implementation commits are listed per row.

Environment: Python 3.14.4, `pi` 0.85.1, `bwrap` (bubblewrap) present,
llama-server on `127.0.0.1:8000` (model id `mitko`).

Commands:

```
PYTHONPATH=src python3 -m pytest tests/ -q                 # 84 passed
bash scripts/build_lock_env.sh                             # fresh offline env
PYTHONPATH=src .venv-m1/bin/python -m pytest tests/ -q     # 84 passed (lock env)
python3 scripts/acceptance_m1.py --real-pi                 # 19/19 PASS
PYTHONPATH=src python3 -m aop.cli run --case debug-offbyone --harness pi
```

| Requirement | Test/probe | Result | Evidence artifact | Commit |
|---|---|---|---|---|
| 1. Original buggy baseline fails trusted verification | `test_baseline_fails_and_repair_passes`; acceptance #1 | PASS | `docs/evidence/m1/acceptance-report.json` | 399197d |
| 2. Legitimate repair passes | `test_baseline_fails_and_repair_passes`; acceptance #2 | PASS | `docs/evidence/m1/acceptance-report.json` | 399197d |
| 3. `os._exit(0)` cannot create a false PASS | `test_os_exit_zero_is_not_pass`; acceptance #3 | PASS | `acceptance-report.json` (`verification_no_result`) | 399197d, f6657ca |
| 4. `sitecustomize.py` cannot create a false PASS | `test_sitecustomize_does_not_execute`; acceptance #4 | PASS | `acceptance-report.json` (`executed=False`) | 399197d, f6657ca |
| 5. Malicious `conftest.py` cannot create a false PASS | `test_candidate_conftest_cannot_redefine_collection`; acceptance #5 | PASS | `acceptance-report.json` | 399197d, f6657ca |
| 6. skip-all/zero-test/deselection cannot PASS | `test_report_all_skipped_rejected`, `test_report_zero_collected_rejected`, `test_report_deselected_rejected`, `test_report_collect_only_rejected`; acceptance #6 | PASS | `acceptance-report.json` | 399197d, f6657ca |
| 7. Agent cannot read/write hidden verifier material | `test_agent_cannot_read_hidden_verifier_material`, `test_agent_cannot_write_hidden_verifier_material`; acceptance #7 | PASS | `acceptance-report.json` | 399197d, f6657ca |
| 8. Agent cannot modify trusted verifier code | `test_agent_cannot_modify_verifier_code`; acceptance #7 | PASS | `acceptance-report.json` | 399197d, f6657ca |
| 9. Pi cannot be redirected to a non-approved endpoint | `test_remote_deployment_endpoint_fails_closed`, `test_env_endpoint_injection_is_scrubbed_and_ignored`; acceptance #9 | PASS | `acceptance-report.json` | 9151fb4, f6657ca |
| 10. Executed model identity matches recorded deployment | `test_served_model_identity_attested`; acceptance #10 | PASS | `real-pi-run/evaluation-summary.json`, `acceptance-report.json` | 9151fb4 |
| 11. Changing any weight shard changes bundle identity | `test_changing_any_shard_changes_bundle_identity`; acceptance #11 | PASS | `acceptance-report.json` | 972c6f9, f6657ca |
| 12. Duplicate admission is idempotent | `test_duplicate_admission_is_idempotent`; acceptance #12 | PASS | `acceptance-report.json` | 5e8e8af, f6657ca |
| 13. Infra failures end as durable `infra_error` runs | `test_missing_verifier_fixture_is_infra_error`, `test_invalid_verifier_fixture_is_infra_error`, `test_storage_failure_is_durable_infra_error`, `test_adapter_prepare_failure_is_infra_error`; acceptance #13 | PASS | `acceptance-report.json` | 5e8e8af, f6657ca |
| 14. Emitted trajectory records validate against the schema | `test_emitted_events_validate_against_published_schema`; acceptance #14 | PASS | `real-pi-run/events.jsonl`, `acceptance-report.json` | 972c6f9, f6657ca |
| 15. Missing required terminal data marks the run incomplete | `test_ledger_missing_terminal_event_is_incomplete`; acceptance #15 | PASS | `acceptance-report.json` | 972c6f9, f6657ca |
| 16. pass, fail, timeout, cancellation all have durable artifacts | `test_timeout_arm`, `test_cancellation_has_durable_artifact`; acceptance #16 | PASS | `acceptance-report.json`, `real-pi-run/report.json` | 5e8e8af, f6657ca |
| 17. Every run identifies repository/environment/model/harness inputs | `test_every_run_pins_input_identity`; acceptance #17 | PASS | `real-pi-run/manifest.json`, `acceptance-report.json` | 5e8e8af, f6657ca |
| 18. Fresh environment reproducible from the lock | `scripts/build_lock_env.sh`, `test_dependency_lock.py`; acceptance #18 | PASS | `docs/evidence/m1/pytest-lockenv.log`, `lock-env-build.log` | 0473937 |

## Real-model run

`PYTHONPATH=src python3 -m aop.cli run --case debug-offbyone --harness pi
--idempotency-key m1-accept-real-pi` -> `runs/run-c85be3744478`:
`outcome=pass`, `verdict=pass`, `model_deployment_id=qwen-flash-next-IQ2_S_-_2.5_bpw`,
`bundle_digest=sha256:d13da30e...`, `agent_seconds=23.14`, `total_seconds=24.78`.
The pass came from the trusted verifier's machine-readable report, not Pi's
exit status. Copied evidence: `docs/evidence/m1/real-pi-run/`.

The buggy baseline fails under the same verifier (acceptance #1) and a
no-repair harness fails (`test_fail_arm_baseline_unfixed`).

## Unrun / deferred

- No container/VM or separate-UNIX-identity verifier isolation; M1 uses
  `bwrap` namespaces as an unprivileged user (documented in ADR-005 and the
  threat model). VM-class isolation is M5+/M10.
- Host-level egress enforcement beyond loopback inference is deferred (M10);
  the agent namespace shares the host network so a real model call can reach
  `127.0.0.1` only per endpoint policy.
- Sealed evaluator-owned hidden storage with an access ledger is M4; M1 keeps
  `.hidden/` but the namespace boundary is what seals it.
- No M2 work (OTel beyond the existing client-side projection, spooling,
  redaction pipeline, Grafana) was implemented.

M1 exit criteria 1–18 are demonstrably satisfied. M2 should not start until a
separate review confirms the verifier/sandbox boundary on the target host.
