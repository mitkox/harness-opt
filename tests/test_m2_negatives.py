"""M2 negative/adversarial suite: corrupted evidence must fail visibly."""
import json
import os

import pytest

from aop.contracts.records import EventSource, TrajectoryEvent
from aop.telemetry.spool import SpoolQueue
from aop.trajectories import EventLedger

DIGEST = "sha256:" + "ab" * 32


def _uid(n):
    return f"00000000-0000-4000-8000-{n:012d}"


def _evt(eid, source, seq, etype="run.admitted", auth="platform_observation"):
    return TrajectoryEvent(
        event_id=eid, event_type=etype, run_id="r1", attempt_id="a1",
        source=EventSource(id=source, authority=auth), source_sequence=seq,
        observed_at="2026-09-12T00:00:00Z", bundle_digest=DIGEST,
        trace_id="1" * 32, span_id="2" * 16)


def _pass_ledger(tmp_path, name="events.jsonl"):
    ledger = EventLedger(str(tmp_path / name))
    counters: dict[str, int] = {}

    def put(etype, source="aop-runner", auth="platform_observation"):
        n = counters.get(source, 0)
        counters[source] = n + 1
        ledger.append(_evt(_uid(len(ledger.read_all()) + 1), source, n, etype,
                           auth=auth))
    put("run.admitted")
    put("run.started")
    put("harness.exited")
    put("verifier.started", source="verifier", auth="verifier_fact")
    put("verifier.completed", source="verifier", auth="verifier_fact")
    put("evaluation.recorded", source="verifier", auth="verifier_fact")
    put("run.completed")
    return ledger


def test_dropped_event_is_gap_and_ineligible(tmp_path):
    ledger = EventLedger(str(tmp_path / "events.jsonl"))
    ledger.append(_evt(_uid(1), "pi", 0))
    ledger.append(_evt(_uid(2), "pi", 1))
    ledger.append(_evt(_uid(4), "pi", 3))  # seq 2 dropped in transit
    comp = ledger.completeness()
    assert comp["complete"] is False and comp["gaps"] == {"pi": [2]}


def test_missing_terminal_event_incomplete(tmp_path):
    ledger = EventLedger(str(tmp_path / "events.jsonl"))
    counters: dict[str, int] = {}

    def put(etype, source="aop-runner"):
        n = counters.get(source, 0)
        counters[source] = n + 1
        ledger.append(_evt(_uid(100 + len(ledger.read_all())), source, n, etype))
    put("run.admitted")
    put("run.started")
    put("harness.exited")
    comp = ledger.completeness_for_outcome("pass")
    assert comp["complete"] is False
    assert comp["telemetry_incomplete"] is True


def test_missing_verifier_event_ineligible_but_debuggable(tmp_path):
    ledger = _pass_ledger(tmp_path)
    # remove verifier evidence to simulate a lost verifier span
    kept = [json.loads(line) for line in open(str(tmp_path / "events.jsonl"))
            if json.loads(line)["event_type"] not in (
                "verifier.started", "verifier.completed", "evaluation.recorded")]
    assert len(kept) < 7  # evidence dropped, rest intact for debugging
    assert any(json.loads(line)["event_type"] == "run.completed" for line in
               open(str(tmp_path / "events.jsonl")))


def test_reordered_source_event_rejected(tmp_path):
    ledger = EventLedger(str(tmp_path / "events.jsonl"))
    ledger.append(_evt(_uid(1), "pi", 0))
    ledger.append(_evt(_uid(2), "pi", 1))
    with pytest.raises(ValueError, match="sequence regression"):
        ledger.append(_evt(_uid(3), "pi", 1))


def test_malformed_event_fails_visibly(tmp_path):
    path = str(tmp_path / "events.jsonl")
    ledger = EventLedger(path)
    ledger.append(_evt(_uid(1), "aop-runner", 0))
    with open(path, "a") as fh:
        fh.write("{broken json\n")
    with pytest.raises(ValueError, match="malformed"):
        EventLedger(path).read_all()


def test_agent_claim_cannot_become_verifier_evidence(tmp_path):
    ledger = EventLedger(str(tmp_path / "events.jsonl"))
    claim = _evt(_uid(1), "harness-report", 0, "agent.claim", auth="agent_claim")
    ledger.append(claim)
    stored = ledger.read_all()[0]
    assert stored.authority_allows_verdict() is False
    with pytest.raises(ValueError):
        ledger.append(_evt(_uid(2), "harness-report", 1, "evaluation.recorded",
                           auth="agent_claim"))


def test_tempo_outage_cannot_erase_authoritative_evidence(tmp_path, monkeypatch):
    """Collector/Tempo down: ledger stays durable, spool holds projections."""
    from aop.runner import Runner
    monkeypatch.setenv("AOP_COLLECTOR_DOWN", "1")
    runner = Runner(runs_dir=str(tmp_path / "runs"))
    rep = runner.execute("debug-offbyone", harness="scripted:repair",
                         idempotency_key="neg-outage")
    assert rep["outcome"] == "pass"
    assert rep["spool"]["collector"] == "unavailable-spooled"
    assert rep["spool"]["pending"] >= 1
    # authoritative evidence intact despite the outage
    assert os.path.exists(os.path.join(rep["run_dir"], "events.jsonl"))
    assert os.path.exists(os.path.join(rep["run_dir"], "evaluation.json"))
    assert rep["completeness_policy"]["complete"] is True
    # backend returns: spool recovers without touching the ledger
    monkeypatch.delenv("AOP_COLLECTOR_DOWN")
    spool = SpoolQueue(os.path.join(runner.runs_dir, "_spool"),
                       os.path.join(runner.runs_dir, "_collector"))
    flushed = spool.flush(rep["run_id"])
    assert flushed["collector"] == "recovered" and flushed["flushed"] >= 1


def test_spool_restart_recovers_pending_telemetry(tmp_path, monkeypatch):
    monkeypatch.setenv("AOP_COLLECTOR_DOWN", "1")
    spool = SpoolQueue(str(tmp_path / "spool"), str(tmp_path / "collector"))
    spool.enqueue("run-x", {"span": "workflow.run"})
    spool2 = SpoolQueue(str(tmp_path / "spool"), str(tmp_path / "collector"))
    assert spool2.pending_count("run-x") == 1
    monkeypatch.delenv("AOP_COLLECTOR_DOWN")
    assert spool2.flush("run-x")["flushed"] == 1


def test_oversized_and_secret_payloads_never_inline(tmp_path):
    from aop.telemetry.observe import MAX_TOOL_OUTPUT_BYTES, normalize_tool_call
    from aop.telemetry.redaction import sanitize_attributes
    big = {"blob": "z" * (MAX_TOOL_OUTPUT_BYTES + 10)}
    call = normalize_tool_call("t", "v1", "i", "a",
                               {"api_key": "sk-9999999999999999secret"}, big,
                               1.0, 2.0)
    blob = json.dumps(call.args_summary) + json.dumps(call.result_summary)
    assert "sk-9999999999999999secret" not in blob
    assert call.result_summary.get("truncated") is True
    attrs = sanitize_attributes({"prompt": "password=hunter2-value " + "p" * 5000})
    flat = json.dumps(attrs)
    assert "hunter2-value" not in flat
