# M2 acceptance matrix

Environment: Python 3.14.4, `pi` 0.85.1, `bwrap` present, llama-server on
`127.0.0.1:8000` (model id `mitko`). No new dependencies (stdlib + pinned
pydantic only; `requirements.lock` unchanged). Tempo/Grafana/Collector
images were NOT pulled during evaluation (offline constraint); the
demonstrated OTel path is per-run `trace-otel.json` plus the local
spool/collector directories. Compose provisions the stack for operators
(`docker compose config` validates).

Commands:

```
PYTHONPATH=src python3 -m pytest tests/ -q            # 121 passed
PYTHONPATH=src python3 scripts/acceptance_m1.py       # 18/18 PASS (M1 regression)
PYTHONPATH=src python3 scripts/demo_m2.py             # arms A-G
PYTHONPATH=src python3 scripts/migrate_m1_to_m2.py --runs-dir runs
```

| M2 requirement | Test/demo | Result | Run/artifact/trace | Commit |
|---|---|---|---|---|
| 1. All emitted events validate vs trajectory schema | `test_emitted_events_validate_against_published_schema`; G-arm 386/386 validate vs v0.2 | PASS | `runs/run-e3019e65a703/events.jsonl`, trace `9833e27e` | 9194fab, f76faff |
| 2. Runs reconstructable chronologically from ledger | `read_all`/`read_from_cursor` + `run trajectory` on every arm | PASS | `docs/evidence/m2/demo-report.json` arms A-G | de63f37, f76faff |
| 3. Bidirectional trace/span <-> trajectory correlation | `test_trace_span_correlation_bidirectional`; G single trace across 386 events | PASS | trace `9833e27e`, `trace-otel.json` 9 spans | 58abe54 |
| 4. Agent claims distinct from verifier facts | authority tests; G `agent_claim:1` vs `verifier_fact:12`, `authority_allows_verdict` | PASS | run-e3019e65a703 | 9cccef7, fa6d006 |
| 5. Missing required events mark incomplete | outcome policies; `telemetry.incomplete` on policy miss | PASS | `test_m2_ledger.py`, demo `completeness_policy` | 004c675 |
| 6. Collector/Tempo outage cannot erase evidence | arm E passes with full verifier evidence while down | PASS | run-af357cb426cc, spool `unavailable-spooled` | fa6d006, f76faff |
| 7. Telemetry recovers from spool after restoration | `E_recovery_flush` 6/6 `recovered`; restart test | PASS | run-af357cb426cc | 58abe54 |
| 8. Secrets redacted from traces/metrics/logs | synthetic-secret tests (prompt, tool args, key-name, truncation) | PASS | `test_m2_observe.py`, `test_m2_negatives.py` | 02f6882 |
| 9. Skill exposure/selection/load/execute separate | F-arm chain `catalog_exposed(1)->selected(1)->loaded(1)->executed(1)` | PASS | run-3d3deb535732 | f76faff |
| 10. Tool invocations normalized and correlated | G 3x `request/started/completed` with digests + spans; F 1x | PASS | run-e3019e65a703, run-3d3deb535732 | 9194fab |
| 11. pass/fail/timeout/cancelled classified durably | arms A/B/C/D outcomes + `completeness_policy.complete=true` | PASS | runs `run-3a67a3bffcc0`, `run-de4dc6385f81`, `run-4e6dd5d07d00`, `run-d1baf78111fb` | f76faff |
| 12. M1 runs readable after migration | migration: `run-c85be3744478` 325 events readable; legacy pre-UUID flagged | PASS | `scripts/migrate_m1_to_m2.py` | de63f37 |
| 13. Fresh process reopens previous trajectories | `test_fresh_process_reopens_trajectory`; CLI on G | PASS | run-e3019e65a703, `aop run show/trajectory` | de63f37 |
| 14. `show/trajectory/artifacts/completeness` work on real runs | 9 verbs smoke-tested + manual CLI on G | PASS | `test_m2_cli.py`, trace `9833e27e` | de63f37 |
| 15. Replay-check pins identities, no determinism claim | `replay-check` true on G with stochastic disclaimer | PASS | run-e3019e65a703 | de63f37 |
| 16. No cloud service required for observability | stdlib tracer, file export, local spool; compose unpulled | PASS | `trace-otel.json`, `runs/_spool`, `runs/_collector` | 58abe54 |
| 17. M1 verifier isolation/local-only regression passes | `scripts/acceptance_m1.py` 18/18 + full suite 121 passed | PASS | acceptance output §above | (this doc) |
| 18. Full M0/M1/M2 suite passes | `pytest tests/` 121 passed | PASS | (console) | (this doc) |
| 19. M1 adversarial/security probes still fail safely | `tests/security`, `test_verification.py` in full suite | PASS | 121 passed incl. boundary suite | (this doc) |
| 20. Real local-model run traced admission->verifier->terminal | G: `run.admitted->...->run.completed`, 3 tool calls, 9 verifier passes | PASS | run-e3019e65a703, trace `9833e27e1d19a6884823b293508d2e22` | f76faff |

## Real-run evidence (all under `runs/`, gitignored; summaries committed)

- A success: `run-3a67a3bffcc0` (pass/pass, trace `d86807bf`)
- B verifier failure: `run-de4dc6385f81` (fail/fail, trace `2dc0c3b7`)
- C timeout: `run-4e6dd5d07d00` (timeout/inconclusive, trace `2780da42`)
- D cancellation: `run-d1baf78111fb` (cancelled/inconclusive, trace `2c652b0e`)
- E outage: `run-af357cb426cc` (pass, 6 spans spooled then recovered)
- F skill: `run-3d3deb535732` (pass, full skill chain, trace `53f12a12`)
- G real Pi: `run-e3019e65a703` (pass/pass, 386 events, trace
  `9833e27e1d19a6884823b293508d2e22`, bundle
  `sha256:d13da30e…`, model `qwen-flash-next-IQ2_S_-_2.5_bpw`, harness
  `pi@0.85.1`, 25.94s agent / 0.17s verifier)

## Unresolved risks / explicit non-goals

- Grafana/Tempo/Collector containers not deployed (image pulls forbidden
  during evaluation); pipeline provisioned as compose config only.
- `file.read`/`file.write` events emitted only where directly observed
  (workspace snapshot/patch/diff are measured; per-read tracing deferred).
- GPU meters reported as estimated/unattributed; token counts null where Pi
  reports zeros; server prefill/decode unavailable (all explicit).
- Subagent events supported by taxonomy but no subagent-spawning harness
  observed in M2 arms (M5 work).
- Pre-UUID M1 dev runs under `runs/` predate the finalized contract and are
  flagged legacy, not migrated.

M3 should NOT begin until a reviewer confirms this M2 evidence; the next
dependency-ready item is M3 skill-spec/compiler work (AOP-016/017), which can
now build on the skill-exposure evidence recorded here.
