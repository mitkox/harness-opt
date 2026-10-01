"""Storage / ledger / runstore tests (AOP-005, AOP-006). Hermetic."""

import pytest

from hop.contracts.records import (
    AttemptRecord,
    EventSource,
    RunOutcome,
    RunRecord,
    TrajectoryEvent,
)
from hop.runstore import RunStore
from hop.storage import ArtifactStore
from hop.trajectories import EventLedger

DIGEST = "sha256:" + "ab" * 32


def _uid(n):
    return f"00000000-0000-4000-8000-{n:012d}"


def _evt(eid, source, seq, run="r1"):
    return TrajectoryEvent(
        event_id=eid,
        event_type="test.event",
        run_id=run,
        attempt_id="a1",
        source=EventSource(id=source, authority="synthetic_fixture"),
        source_sequence=seq,
        observed_at="2026-09-12T00:00:00Z",
        bundle_digest=DIGEST,
        trace_id="1" * 32,
        span_id="2" * 16,
    )


def test_artifact_tamper_fails(tmp_path):
    store = ArtifactStore(str(tmp_path))
    ref = store.put(b"hello", "run1")
    assert store.get(ref.digest, "run1") == b"hello"
    # cross-repository read denied
    with pytest.raises(PermissionError):
        store.get(ref.digest, "run2")
    # tamper: flip a byte in the blob
    import os

    blob = os.path.join(
        str(tmp_path), "blobs", ref.digest.split(":")[1][:2], ref.digest.split(":")[1]
    )
    with open(blob, "r+b") as fh:
        fh.seek(0)
        fh.write(b"X")
    assert store.verify(ref.digest) is False
    with pytest.raises(ValueError):
        store.get(ref.digest, "run1")


def test_interrupted_write_not_complete(tmp_path):
    store = ArtifactStore(str(tmp_path))
    partials = [
        f for _, _, fs in __import__("os").walk(str(tmp_path)) for f in fs if f.endswith(".partial")
    ]
    assert partials == []
    store.put(b"data", "run1")
    assert store.verify(store.put(b"data", "run1").digest) is True


def test_ledger_dedup_and_reconnect(tmp_path):
    ledger = EventLedger(str(tmp_path / "events.jsonl"))
    assert ledger.append(_evt(_uid(1), "pi", 0)) is True
    assert ledger.append(_evt(_uid(1), "pi", 0)) is False  # at-least-once duplicate
    assert ledger.append(_evt(_uid(2), "pi", 1)) is True
    with pytest.raises(ValueError):
        ledger.append(_evt(_uid(3), "pi", 1))  # regression / duplicate seq
    # observer reconnect from cursor
    batch, cursor = ledger.read_from_cursor(0)
    assert len(batch) == 2 and cursor == 2
    ledger2 = EventLedger(str(tmp_path / "events.jsonl"))  # restart recovery
    batch2, _ = ledger2.read_from_cursor(cursor)
    assert batch2 == []
    ledger2.append(_evt(_uid(3), "pi", 2))
    batch3, cursor3 = ledger2.read_from_cursor(cursor)
    assert len(batch3) == 1 and cursor3 == 3
    assert ledger2.completeness()["gaps"] == {}


def test_ledger_missing_terminal_event_is_incomplete(tmp_path):
    """Contiguous sequences alone are not complete; required terminal events count."""
    ledger = EventLedger(str(tmp_path / "events.jsonl"))
    for i, etype in enumerate(("run.admitted", "workspace.prepared", "harness.accepted")):
        evt = _evt(_uid(10 + i), "aop-runner", i)
        evt.event_type = etype
        ledger.append(evt)
    comp = ledger.completeness()
    assert comp["gaps"] == {} and comp["complete"] is False
    assert "terminal_harness_event" in comp["missing_required"]
    terminal = _evt(_uid(20), "aop-runner", 3)
    terminal.event_type = "harness.exited"
    ledger.append(terminal)
    comp2 = ledger.completeness()
    assert comp2["complete"] is True and comp2["missing_required"] == []


def test_ledger_gap_detected(tmp_path):
    path = str(tmp_path / "events.jsonl")
    ledger = EventLedger(path)
    ledger.append(_evt(_uid(1), "pi", 0))
    ledger.append(_evt(_uid(2), "pi", 1))
    # simulate lost event: append a later sequence, leaving a gap at 2
    ledger.append(_evt(_uid(4), "pi", 3))
    comp = ledger.completeness()
    assert comp["complete"] is False
    assert comp["gaps"] == {"pi": [2]}


def test_runstore_idempotent_and_fenced(tmp_path):
    store = RunStore(str(tmp_path / "ledger.db"))
    rec = RunRecord(
        run_id="r1", task_id="t", case_id="c", bundle_digest=DIGEST, idempotency_key="k1"
    )
    _, created = store.admit(rec)
    assert created is True
    same, created2 = store.admit(rec)
    assert created2 is False and same.run_id == "r1"
    a1 = store.new_attempt("r1", AttemptRecord(attempt_id="a1", worker_id="w1"))
    a2 = store.new_attempt("r1", AttemptRecord(attempt_id="a2", worker_id="w2"))
    assert (a1.fencing_token, a2.fencing_token) == (1, 2)
    with pytest.raises(PermissionError):
        store.finalize_attempt("r1", "a1", 1, RunOutcome.PASS)  # stale worker
    store.finalize_attempt("r1", "a2", 2, RunOutcome.FAIL)
    assert store.get("r1").status.value == "admitted"  # a1 still open
    # restart preserves attempts
    store2 = RunStore(str(tmp_path / "ledger.db"))
    assert len(store2.get("r1").attempts) == 2
