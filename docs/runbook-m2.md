# M2 runbook: investigate, migrate, recover

## Investigate a failed run (no DB spelunking)

```
PYTHONPATH=src python3 -m hop.cli run show <run-id>         # identity, outcome, resources, trace
PYTHONPATH=src python3 -m hop.cli run trajectory <run-id>   # chronological durable events
PYTHONPATH=src python3 -m hop.cli run trajectory <run-id> --event-type tool.completed
PYTHONPATH=src python3 -m hop.cli run artifacts <run-id>    # content-addressed refs + integrity
PYTHONPATH=src python3 -m hop.cli run trace <run-id>        # OTel span projection
PYTHONPATH=src python3 -m hop.cli run verify <run-id>       # verifier evidence / scope / error
PYTHONPATH=src python3 -m hop.cli run skills <run-id>       # exposure -> selection -> load -> execute
PYTHONPATH=src python3 -m hop.cli run tools <run-id>        # normalized tool records
PYTHONPATH=src python3 -m hop.cli run completeness <run-id> # policy, gaps, integrity chain
PYTHONPATH=src python3 -m hop.cli run replay-check <run-id> # pinned identities still available?
```

`HOP_RUNS_DIR` overrides the run root (default `./runs`).

## End-to-end demo (A–G)

```
PYTHONPATH=src python3 scripts/demo_m2.py   # ~2 min with the real-Pi arm
```

Writes `docs/evidence/m2/demo-report.json` with per-arm outcome, trace,
completeness, resources, bundle identity, and artifact lists.

## M1 → M2 migration (non-rewriting)

```
PYTHONPATH=src python3 scripts/migrate_m1_to_m2.py --runs-dir runs
```

Verifies every run reads under v0.2, validates against the published
schema, evaluates the M2 outcome policy, and writes missing
`events.jsonl.sha256` sidecars. Original JSONL files are never modified.
Pre-UUID M1 dev runs are flagged `legacy:true`, not claimed readable.

- Backup: `cp -r runs runs.bak-<date>` (optional; migration adds files only).
- Rollback: `find runs -name '*.sha256' -delete` (M2 sidecars only).

## Collector outage recovery

Spans spool to `runs/_spool/<run-id>.jsonl` while `HOP_COLLECTOR_DOWN=1` or
the collector directory is down. On return, the runner flushes
automatically (`telemetry.recovered` event); pending spool can be inspected
via the `spool` section of `run show`. The ledger under
`runs/<run-id>/events.jsonl` is unaffected throughout.

## Local OTel stack (operator-provisioned, not evaluation-required)

```
docker compose -f deploy/compose/otel-local.yaml config   # validate only
docker compose -f deploy/compose/otel-local.yaml up       # operator action
```

M2 evaluation does not pull images or require SaaS; the demonstrated path
is the per-run `trace-otel.json` file plus the local spool/collector
directories.
