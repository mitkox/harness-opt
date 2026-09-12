# M2 observability: trajectory, traces, redaction, resources

## Event envelope (v0.2)

Every event carries: `event_id`, `run_id`, `attempt_id`, `agent_id`,
`parent_agent_id`, `event_type`, `source{id, authority}`, monotonic
`source_sequence` per source, `event_timestamp`, `observed_at`, `trace_id`,
`span_id`, `parent_span_id`, `model_deployment_digest`, `harness_digest`,
`bundle_digest`, `repo_snapshot_digest`, `environment_digest`, skill and tool
identity/version, `payload_refs` (artifact digests), `data_classification`,
`schema_version`. Authority is one of `platform_observation`,
`harness_observation`, `agent_claim`, `model_output`, `verifier_fact`
(`evaluation.recorded` requires `verifier_fact`; violations raise visibly).

## Taxonomy

Run lifecycle, model (`inference.*`), context, skills
(`catalog_exposed → considered? → selected → loaded → executed/failed`),
tools (`request → started → completed/failed`), filesystem
(`workspace.snapshot`, `patch.generated`, `diff.frozen`; `file.read/write`
only where directly observable), agents/subagents, verifier
(`started → test_* → completed`), telemetry
(`gap_detected`, `recovered`, `incomplete`). Namespaced extension without
breaking stored trajectories.

## Trace correlation

One primary run trace (`RunTracer`, W3C IDs). Trajectory events carry the
trace/span IDs of their execution boundary; `trace-otel.json` holds the
span tree; `aop run trace <id>` shows it and `aop run trajectory <id>`
resolves the events. The ledger is authoritative; OTel is a projection.

## Metric provenance

- measured: wall/CPU/RSS (rusage), queue/model/harness/verifier boundaries
- derived: durations, token rates (when counts exposed)
- estimated: GPU host meter (never attributed to one concurrent task)
- unavailable: server prefill/decode, TTFT, token counts Pi reports as zero
  (explicit nulls, never fabricated)

Queue, model, harness/tool, and verification times are stored separately
(`resources.json`); no combined latency metric exists.

## Redaction model

Classifications: public, internal, confidential, secret. Pattern redaction
(API keys, passwords, bearer tokens, auth headers, private keys, `gh_*`,
`sk-*`, AWS IDs, URL credentials) plus key-name redaction for structured
args (`{"password": …}`), truncation with artifact digests for large text,
and sanitized summaries + digests for tool args/results. Raw payloads with
secrets live only as access-controlled artifacts. Synthetic-secret tests:
`tests/test_m2_schema.py`, `tests/test_m2_observe.py`,
`tests/test_m2_negatives.py`.

## Completeness and eligibility

Outcome-aware policies (`telemetry/completeness.py`); misses set
`telemetry_incomplete=true` (debuggable, promotion-ineligible).
Integrity chain (`events.jsonl.sha256`) detects silent rewrites.
