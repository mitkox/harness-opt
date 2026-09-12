# ADR-007: Local-only OTel projection with durable spool

Date: 2026-09-12.

Status: accepted.

## Context

BUILD_PLAN §3/§11 prescribes a self-hosted Collector → Tempo/Prometheus/
Grafana pipeline with the trajectory/evidence ledger as the authoritative
record. M2 must correlate traces with trajectory events, survive observer
outages, and never require SaaS. No new third-party packages may be pulled
during evaluation (pinned offline lock).

## Decision

1. Tracing is stdlib-only (`telemetry/tracing.py`): W3C trace/span IDs, the
   prescribed span hierarchy (only real execution boundaries get spans), and
   redacted attributes under `aop.*` plus a pinned GenAI-convention revision
   marker. Export is a per-run `trace-otel.json` file plus best-effort
   forwarding to a local Collector directory.
2. The durable ledger is written first, always. Span forwarding goes through
   `SpoolQueue` (`runs/_spool/<run-id>.jsonl`, fsync'd); `flush()` delivers
   to `runs/_collector/` when available and reports
   `unavailable-spooled`/`recovered` otherwise. Spool files survive process
   restart by construction (plain JSONL replay).
3. Eligibility distinguishes backend outage (spool pending, ledger complete)
   from authoritative incompleteness (ledger gaps/policy misses).
4. `deploy/compose/otel-local.yaml` provisions Collector/Tempo/Prometheus/
   Grafana for operators (config validates via `docker compose config`).
   Images are NOT pulled during evaluation; the file pipeline above is the
   demonstrated path. Tempo/Grafana were not deployed in M2.
5. Timing provenance is explicit (`telemetry/observe.py`): client span
   boundaries are measured, durations derived, GPU host meters estimated
   (never attributed to one run), server prefill/decode and TTFT unavailable
   unless the server exposes them (client first-chunk noted separately).

## Consequences

- Collector/Tempo loss cannot erase evaluation evidence (demonstrated: arm E
  passes with full verifier evidence while the collector is down).
- Secrets never enter spans/metrics/labels (`telemetry/redaction.py`;
  key-name and pattern redaction, truncation with artifact digests).

## Rename note (AOP → HOP, M2→M3 boundary)

The `aop.*` span/attribute namespace named above is frozen as the stable
v1 wire identifier for compatibility with persisted M1/M2 trajectories;
see ADR-008. No `hop.*` duplicate was introduced.
