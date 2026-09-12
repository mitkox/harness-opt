# M1 runbook: local end-to-end debugging evaluation

Prerequisites: `llama-server` on :8000 (Qwen Flash-Next) and :8001, `pi` 0.85.1
on PATH, and `bwrap` (bubblewrap). No network access is used or required after
discovery; all inference is loopback.

## 0. Reproducible environment

```
bash scripts/build_lock_env.sh --run-tests   # offline, from requirements.lock + .vendor/wheels
```

`requirements.lock` is generated with `uv pip compile pyproject.toml --extra
test --generate-hashes`. If the wheelhouse is absent, regenerate it with
`python3 -m pip download -r requirements.lock -d .vendor/wheels --no-deps`.

## 1. Discover and pin

```
PYTHONPATH=src python3 scripts/discover.py   # rewrites profiles/*/local-inventory.json
PYTHONPATH=src python3 -m aop.cli discover
PYTHONPATH=src python3 -m aop.cli validate-profile --model qwen-flash-next
```

`validate-profile` exits 2 (REFUSED) for unknown/unqualified models.
Each deployment records all weight shards, quantization, serving config,
context length, chat-template hash, and endpoint; all of these feed the
execution-bundle digest.

## 2. Run the debugging slice

```
PYTHONPATH=src python3 -m aop.cli run --case debug-offbyone --harness pi --timeout 900
PYTHONPATH=src python3 -m aop.cli run --case debug-wordcount --harness pi --timeout 900
# deterministic arms (no GPU):
PYTHONPATH=src python3 -m aop.cli run --case debug-offbyone --harness scripted:repair
PYTHONPATH=src python3 -m aop.cli run --case debug-offbyone --harness scripted:fail
PYTHONPATH=src python3 -m aop.cli run --case debug-offbyone --harness scripted:hang --timeout 5
PYTHONPATH=src python3 -m aop.cli run --case debug-offbyone --harness scripted:scope_violation
```

Exit code 0 means verifier PASS; 1 means any other outcome (see report).
Re-running with the same `--idempotency-key` replays the recorded run instead
of executing twice.

## 3. Inspect a run

```
PYTHONPATH=src python3 -m aop.cli report --run-dir runs/<run-id>
# events:        runs/<run-id>/events.jsonl   (durable trajectory, schema-valid)
# manifest:      runs/<run-id>/manifest.json  (bundle + model/harness/verifier/input pins)
# evaluation:    runs/<run-id>/evaluation.json (machine-readable verifier evidence)
# scope:         runs/<run-id>/scope.json
# cancellation:  runs/<run-id>/cancellation.json
# infra failure: runs/<run-id>/error.json + report.json (outcome infra_error)
# verifier work: runs/<run-id>/verify/
```

Outcome semantics: `pass`/`fail` come only from hidden tests executed under the
trusted verifier (`debug-verifier-v2`). `timeout`/`cancelled` skip verification
(verdict `inconclusive`). `telemetry_incomplete` means a required event was
missing; `infra_error` covers crashes, setup failures, missing fixtures,
storage failures, sandbox unavailability, and model-identity mismatch.
`scope_violation` means a protected or out-of-scope file changed. Agent text in
`agent.claim` is narration.

## 4. Tests and acceptance

```
PYTHONPATH=src python3 -m pytest tests/ -q
PYTHONPATH=src python3 scripts/acceptance_m1.py
```

`acceptance_m1.py` reruns the M1 exit criteria probes and writes
`runs/acceptance/acceptance-report.json`; see `docs/acceptance-m1.md` for the
requirement-to-evidence matrix.

Hermetic tests need no GPU. `test_probe_real_pi` needs the `pi` binary, the
boundary/verifier suites need `bwrap`, and the `--harness pi` acceptance arm
needs the local model endpoints.
