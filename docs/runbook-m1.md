# M1 runbook: local end-to-end debugging evaluation

Prerequisites: llama-server on :8000 (Qwen Flash-Next) and :8001, `pi` 0.85.1
on PATH. No network access is used or required after discovery.

## 1. Discover and pin

```
PYTHONPATH=src python3 scripts/discover.py   # rewrites profiles/*/local-inventory.json
PYTHONPATH=src python3 -m aop.cli discover
PYTHONPATH=src python3 -m aop.cli validate-profile --model qwen-flash-next
```

`validate-profile` exits 2 (REFUSED) for unknown/unqualified models.

## 2. Run the debugging slice

```
PYTHONPATH=src python3 -m aop.cli run --case debug-offbyone --harness pi --timeout 900
PYTHONPATH=src python3 -m aop.cli run --case debug-wordcount --harness pi --timeout 900
# deterministic arms (no GPU):
PYTHONPATH=src python3 -m aop.cli run --case debug-offbyone --harness scripted:succeed
PYTHONPATH=src python3 -m aop.cli run --case debug-offbyone --harness scripted:hang --timeout 5
```

Exit code 0 means verifier PASS; 1 means any other outcome (see report).

## 3. Inspect a run

```
PYTHONPATH=src python3 -m aop.cli report --run-dir runs/<run-id>
# events:      runs/<run-id>/events.jsonl   (durable trajectory)
# manifest:    runs/<run-id>/manifest.json  (bundle + pins)
# harness log: runs/<run-id>/harness-stdout.log
# snapshot:    runs/<run-id>/snapshot/
# evaluation:  runs/<run-id>/evaluation.json
```

Outcome semantics: `pass`/`fail` come only from hidden tests.
`timeout`/`cancelled` skip verification (verdict `inconclusive`).
`telemetry_incomplete` means an event gap was detected; `infra_error`
covers crashes and contamination. Agent text in `agent.claim` is narration.

## 4. Tests

```
PYTHONPATH=src python3 -m pytest tests/
```

All hermetic except `test_probe_real_pi` (needs the `pi` binary) and the
`aop run --harness pi` path (needs the local model endpoints).
