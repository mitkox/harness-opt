"""M2 commit 3: tracing correlation + spool recovery."""
import json
import os

from aop.telemetry.spool import SpoolQueue, collector_available
from aop.telemetry.tracing import RunTracer


def test_trace_span_correlation_bidirectional(tmp_path):
    tracer = RunTracer("run-1")
    root = tracer.start("workflow.run", {"aop.run_id": "run-1"})
    child = tracer.start("agent.execute", {"aop.bundle_digest": "sha256:" + "ab" * 32})
    tracer.finish(child)
    tracer.finish(root)
    payload = tracer.export(str(tmp_path / "trace-otel.json"))
    assert payload["trace_id"] == tracer.trace_id
    by_name = {s["name"]: s for s in payload["spans"]}
    assert by_name["agent.execute"]["parent_span_id"] == by_name["workflow.run"]["span_id"]
    # trajectory event can resolve its span; span resolves its trace/run
    tracer2 = RunTracer("run-2")
    tracer2.start("workflow.run")
    tracer2.start("admission.resolve")
    span = tracer2.span_for_event("run.admitted")
    assert span is not None and span.trace_id == tracer2.trace_id


def test_collector_unavailable_before_run_spools(tmp_path, monkeypatch):
    monkeypatch.setenv("AOP_COLLECTOR_DOWN", "1")
    spool = SpoolQueue(str(tmp_path / "spool"), str(tmp_path / "collector"))
    assert collector_available(str(tmp_path / "collector")) is False
    spool.enqueue("run-1", {"span": "workflow.run"})
    rep = spool.flush("run-1")
    assert rep["pending"] == 1 and rep["collector"] == "unavailable-spooled"
    assert spool.pending_count("run-1") == 1


def test_collector_midrun_outage_then_recovery(tmp_path, monkeypatch):
    spool = SpoolQueue(str(tmp_path / "spool"), str(tmp_path / "collector"))
    spool.enqueue("run-1", {"span": "a"})
    assert spool.flush("run-1")["flushed"] == 1
    monkeypatch.setenv("AOP_COLLECTOR_DOWN", "1")
    spool.enqueue("run-1", {"span": "b"})
    mid = spool.flush("run-1")
    assert mid["pending"] == 1 and mid.get("telemetry_gap") is True
    monkeypatch.delenv("AOP_COLLECTOR_DOWN")
    rec = spool.flush("run-1")
    assert rec == {"flushed": 1, "pending": 0, "collector": "recovered"}
    assert spool.pending_count("run-1") == 0
    dest = os.path.join(str(tmp_path / "collector"), "run-1.jsonl")
    assert os.path.exists(dest)


def test_spool_survives_process_restart(tmp_path):
    spool = SpoolQueue(str(tmp_path / "spool"), str(tmp_path / "collector"))
    spool.enqueue("run-9", {"span": "x"})
    spool2 = SpoolQueue(str(tmp_path / "spool"), str(tmp_path / "collector"))
    assert spool2.pending_count("run-9") == 1
    assert spool2.flush("run-9")["flushed"] == 1
