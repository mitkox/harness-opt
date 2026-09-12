# M3 acceptance matrix

Status vocabulary: **PASS**, **FAIL**, **PARTIAL**, **NOT DEMONSTRATED**.

| # | M3 criterion | Status | Test/demo | Evidence |
|---|---|---|---|---|
| 1 | Versioned immutable component registry exists | PASS | `tests/test_m3_registry.py` | content-addressed store, collision + tamper tests |
| 2 | Skills include instructions/resources/scripts/metadata as versioned components | PASS | `tests/test_m3_compiler.py`, `components/skills/debugging/` | canonical + 3 variants, resources, executable script |
| 3 | Profiles resolve deterministically | PASS | `tests/test_m3_resolver.py` | identical digests across registration orders |
| 4 | Dependency cycles/conflicts detected | PASS | `tests/test_m3_resolver.py` | `dependency_cycle`, `version_conflict` |
| 5 | Variant selection deterministic + explainable | PASS | `tests/test_m3_resolver.py`, `test_m3_cli.py` | `ambiguous_variant`; `hop profile explain` |
| 6 | Mandatory policy cannot be overridden | PASS | `tests/test_m3_resolver.py` | `policy_violation`, `forbidden_override` |
| 7 | Locked profiles contain no floating dependencies | PASS | `tests/test_m3_lockfile.py` | `floating_reference` rejection |
| 8 | Same inputs → same resolved profile digest | PASS | `tests/test_m3_resolver.py`, demo | `profile_digest` equal |
| 9 | Same inputs → deterministic compiled output | PASS | `tests/test_m3_compiler.py`, demo | `artifact_digest` equal twice |
| 10 | Historical locked profiles remain materializable | PASS | `tests/test_m3_negatives.py` | lock-digest-keyed store; `historical_component_unavailable` |
| 11 | Pi target compilation works on a real local run | PASS | `scripts/demo_m3.py --real` | `real_pi_run` in `docs/evidence/m3/demo-report.json` |
| 12 | Run trajectory records profile/lock/compiled identities | PASS | `tests/test_m3_run_integration.py` | report + manifest + every event |
| 13 | Existing M1/M2 runs remain readable | PASS | `tests/test_m3_run_integration.py` | empty-default fields validate |
| 14 | APM export works at the package-generation level | PASS | `tests/test_m3_apm.py`, demo `--apm` | package + `apm lock`/`apm pack` |
| 15 | Exported APM artifact verifiable against HOP source | PASS | `tests/test_m3_apm.py` | `verify_export` recompilation match |
| 16 | APM package excludes weights/secrets/hidden verifiers/evidence | PASS | `tests/test_m3_apm.py` | suffix + marker scan |
| 17 | Tampering with profile/component/export detected | PASS | `test_m3_registry/apm/lockfile/negatives` | content, index, lock, provenance, package |
| 18 | Profile diff works | PASS | `tests/test_m3_cli.py` | added/removed/changed + policy/variants |
| 19 | Profile explain works | PASS | `tests/test_m3_cli.py` | variants, policy layers, transformations |
| 20 | Replay-check accounts for profile dependencies | PASS | `tests/test_m3_run_integration.py` | `profile_store` check |
| 21 | Full M0–M3 test suite passes | PASS | `pytest -q` | see final work report |
| 22 | M1 verifier adversarial probes still fail safely | PASS | `tests/test_verification.py`, `tests/security/` | unchanged M1 suite |
| 23 | M1 local-only endpoint attacks still fail safely | PASS | `tests/security/test_boundaries.py` | unchanged M1 suite |
| 24 | M2 trajectory integrity tests still pass | PASS | `tests/test_m2_ledger.py` | unchanged M2 suite |
| 25 | M2 redaction/telemetry recovery tests still pass | PASS | `tests/test_m2_observe.py`, `test_m2_tracing_spool.py` | unchanged M2 suite |
| 26 | One real local-model + Pi execution from a locked profile | PASS | demo `--real` | real run with profile digests |
| 27 | One APM export from that exact locked profile | PASS | demo | same `profile_digest`/`lock_digest` |
| 28 | No M4+ optimizer/promotion logic introduced | PASS | code review | no optimizer/promotion modules |
