"""M2 commit 2: ledger integrity + outcome-aware completeness."""

import pytest

from hop.contracts.records import EventSource, TrajectoryEvent
from hop.trajectories import EventLedger

DIGEST = "sha256:" + "ab" * 32


def _uid(n):
    return f"00000000-0000-4000-8000-{n:012d}"


def _evt(eid, source, seq, etype="run.admitted", run="r1", auth="platform_observation"):
    return TrajectoryEvent(
        event_id=eid,
        event_type=etype,
        run_id=run,
        attempt_id="a1",
        source=EventSource(id=source, authority=auth),
        source_sequence=seq,
        observed_at="2026-09-12T00:00:00Z",
        bundle_digest=DIGEST,
        trace_id="1" * 32,
        span_id="2" * 16,
    )


def _full_success_ledger(tmp_path):
    ledger = EventLedger(str(tmp_path / "events.jsonl"))
    seq = 0

    def emit(etype, source="aop-runner", auth="platform_observation"):
        nonlocal seq
        evt = _evt(_uid(100 + seq), source, seq, etype, auth=auth)
        # per-source sequences: track separately per source
        return evt

    # simpler: explicit per-source counters
    counters: dict[str, int] = {}

    def put(etype, source="aop-runner", auth="platform_observation", run="r1"):
        n = counters.get(source, 0)
        counters[source] = n + 1
        ledger.append(_evt(_uid(len(ledger.read_all()) + 1), source, n, etype, run=run, auth=auth))

    put("run.admitted")
    put("run.started")
    put("harness.exited")
    put("verifier.started", source="verifier", auth="verifier_fact")
    put("verifier.completed", source="verifier", auth="verifier_fact")
    put("evaluation.recorded", source="verifier", auth="verifier_fact")
    put("run.completed")
    return ledger


def test_success_policy_complete_and_fail_policy_missing_verifier(tmp_path):
    ledger = _full_success_ledger(tmp_path)
    comp = ledger.completeness_for_outcome("pass")
    assert comp["complete"] is True and comp["telemetry_incomplete"] is False
    # missing verifier event -> incomplete, still debuggable
    path2 = str(tmp_path / "e2.jsonl")
    ledger2 = EventLedger(path2)
    counters: dict[str, int] = {}

    def put2(etype, source="aop-runner"):
        n = counters.get(source, 0)
        counters[source] = n + 1
        ledger2.append(_evt(_uid(500 + len(ledger2.read_all())), source, n, etype))

    put2("run.admitted")
    put2("run.started")
    put2("harness.exited")
    put2("run.completed")
    comp2 = ledger2.completeness_for_outcome("pass")
    assert comp2["complete"] is False
    assert any("verifier" in m for m in comp2["missing_required"])


def test_cancel_policy_requires_request_and_terminal(tmp_path):
    ledger = EventLedger(str(tmp_path / "events.jsonl"))
    ledger.append(_evt(_uid(1), "aop-runner", 0, "run.admitted"))
    ledger.append(_evt(_uid(2), "aop-runner", 1, "run.started"))
    comp = ledger.completeness_for_outcome("cancelled")
    assert comp["complete"] is False
    ledger.append(_evt(_uid(3), "aop-runner", 2, "cancel.requested"))
    ledger.append(_evt(_uid(4), "aop-runner", 3, "harness.cancelled"))
    comp2 = ledger.completeness_for_outcome("cancelled")
    assert comp2["complete"] is True


def test_duplicate_dedup_reorder_gap_and_malformed_visible(tmp_path):
    ledger = EventLedger(str(tmp_path / "events.jsonl"))
    assert ledger.append(_evt(_uid(1), "pi", 0)) is True
    assert ledger.append(_evt(_uid(1), "pi", 0)) is False
    with pytest.raises(ValueError):
        ledger.append(_evt(_uid(2), "pi", 0))  # regression
    ledger.append(_evt(_uid(3), "pi", 2))  # gap at 1
    assert ledger.completeness()["gaps"] == {"pi": [1]}
    # malformed line surfaces visibly
    with open(str(tmp_path / "events.jsonl"), "a") as fh:
        fh.write("{not json\n")
    with pytest.raises(ValueError):
        EventLedger(str(tmp_path / "events.jsonl")).read_all()


def test_invalid_authority_transition_rejected(tmp_path):
    ledger = EventLedger(str(tmp_path / "events.jsonl"))
    with pytest.raises(ValueError):
        ledger.append(_evt(_uid(9), "pi", 0, "evaluation.recorded", auth="agent_claim"))
    with pytest.raises(ValueError):
        ledger.append(_evt(_uid(10), "pi", 0, "verifier.completed", auth="harness_observation"))


def test_tamper_detected_by_integrity_chain(tmp_path):
    ledger = EventLedger(str(tmp_path / "events.jsonl"))
    ledger.append(_evt(_uid(1), "aop-runner", 0, "run.admitted"))
    assert ledger.verify_integrity()["ok"] is True
    # silent rewrite: flip event type bytes in place
    with open(str(tmp_path / "events.jsonl"), "r+") as fh:
        content = fh.read().replace("run.admitted", "run.completed")
        fh.seek(0)
        fh.write(content)
        fh.truncate()
    # fresh replay must fail the chain check visibly (not silently accept)
    with pytest.raises(ValueError, match="tampered ledger"):
        EventLedger(str(tmp_path / "events.jsonl"))


def test_ledger_survives_restart_and_cursor_recovery(tmp_path):
    path = str(tmp_path / "events.jsonl")
    ledger = EventLedger(path)
    ledger.append(_evt(_uid(1), "aop-runner", 0, "run.admitted"))
    ledger.append(_evt(_uid(2), "aop-runner", 1, "run.started"))
    batch, cursor = ledger.read_from_cursor(1)
    assert len(batch) == 1 and cursor == 2
    ledger2 = EventLedger(path)
    batch2, _ = ledger2.read_from_cursor(cursor)
    assert batch2 == []
