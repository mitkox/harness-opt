"""Durable trajectory event ledger (AOP-006 worker leases + M2 AOP-012).

Per-run JSONL file, fsync per append batch. Per-source monotonic sequences;
at-least-once dedup by event_id; cursor-based reread for observer reconnect;
gap detection plus required-terminal-event detection -> completeness report.
Sequence contiguity alone is not sufficient: a run whose required events were
never emitted is incomplete even with no gaps.

M2 additions (M1 guarantees preserved):
- rolling integrity chain (tamper-evident; silent rewrites detected);
- malformed-line and invalid-authority-transition detection surfaced
  visibly instead of silently accepted;
- outcome-aware completeness policies (see telemetry/completeness.py).
"""
from __future__ import annotations

import hashlib
import json
import os

from .contracts.records import TrajectoryEvent
from .telemetry.completeness import evaluate as evaluate_policy
from .telemetry.taxonomy import assert_known_event

REQUIRED_PREFIX_EVENTS = ("run.admitted", "workspace.prepared", "harness.accepted")
TERMINAL_EVENTS = ("harness.exited", "harness.crashed", "harness.timeout",
                   "harness.cancelled")

# Authority transitions that are never valid in a stored trajectory.
# Agent/harness output must never appear as verifier_fact.
_INVALID_AUTHORITY_FOR_VERDICT_TYPES = {"agent_claim", "harness_observation",
                                        "model_output", "platform_observation",
                                        "synthetic_fixture"}


class EventLedger:
    def __init__(self, path: str):
        self.path = path
        self.chain_path = path + ".sha256"
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._seen: set[str] = set()
        self._last_seq: dict[str, int] = {}
        self._chain: str = "0" * 64
        if os.path.exists(path):
            self._replay()

    def _replay(self) -> None:
        chain = "0" * 64
        with open(self.path) as fh:
            for lineno, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    evt = TrajectoryEvent.model_validate(json.loads(line))
                except Exception as exc:
                    raise ValueError(
                        f"malformed trajectory event at line {lineno}: {exc}"
                    ) from exc
                assert_known_event(evt.event_type)
                self._seen.add(evt.event_id)
                prev = self._last_seq.get(evt.source.id, -1)
                self._last_seq[evt.source.id] = max(prev, evt.source_sequence)
                chain = hashlib.sha256((chain + line).encode()).hexdigest()
        self._chain = chain
        # Reconcile sidecar chain if present; absence (M1 runs) is fine.
        if os.path.exists(self.chain_path):
            with open(self.chain_path) as fh:
                recorded = fh.read().strip()
            if recorded and recorded != chain:
                raise ValueError("trajectory integrity chain mismatch (tampered ledger)")

    def append(self, event: TrajectoryEvent) -> bool:
        """Returns False if duplicate (deduped). Raises on sequence regression."""
        assert_known_event(event.event_type)
        self._validate_authority(event)
        if event.event_id in self._seen:
            return False
        prev = self._last_seq.get(event.source.id, -1)
        if event.source_sequence <= prev:
            raise ValueError(
                f"sequence regression for {event.source.id}: "
                f"{event.source_sequence} <= {prev}"
            )
        line = event.model_dump_json()
        with open(self.path, "a") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        self._chain = hashlib.sha256((self._chain + line).encode()).hexdigest()
        with open(self.chain_path, "w") as fh:
            fh.write(self._chain)
            fh.flush()
            os.fsync(fh.fileno())
        self._seen.add(event.event_id)
        self._last_seq[event.source.id] = event.source_sequence
        return True

    @staticmethod
    def _validate_authority(event: TrajectoryEvent) -> None:
        # Verdict-carrying evaluation events must be verifier_fact; a harness
        # or model source claiming verifier authority via attributes is rejected.
        if event.event_type == "evaluation.recorded" and (
                event.source.authority.value != "verifier_fact"):
            raise ValueError("evaluation.recorded requires verifier_fact authority")
        if (event.event_type in ("verifier.completed", "verifier.test_passed",
                                 "verifier.test_failed")
                and event.source.authority.value in
                ("agent_claim", "model_output", "harness_observation")):
            raise ValueError(
                f"{event.event_type} cannot carry {event.source.authority.value} authority")

    def read_all(self) -> list[TrajectoryEvent]:
        events = []
        if not os.path.exists(self.path):
            return events
        with open(self.path) as fh:
            for lineno, line in enumerate(fh, start=1):
                if line.strip():
                    try:
                        events.append(TrajectoryEvent.model_validate(json.loads(line)))
                    except Exception as exc:
                        raise ValueError(
                            f"malformed trajectory event at line {lineno}: {exc}"
                        ) from exc
        return events

    def read_from_cursor(self, cursor: int) -> tuple[list[TrajectoryEvent], int]:
        """Observer reconnect: resume after `cursor` events. Returns (events, new_cursor)."""
        events = self.read_all()
        return events[cursor:], len(events)

    def verify_integrity(self) -> dict:
        """Detect silent rewrites: recompute the rolling chain over raw lines."""
        if not os.path.exists(self.path):
            return {"ok": True, "events": 0}
        chain = "0" * 64
        count = 0
        with open(self.path, "rb") as fh:
            for raw in fh:
                if raw.strip():
                    count += 1
                    chain = hashlib.sha256(
                        chain.encode() + raw.strip(b"\n").strip(b"\r")).hexdigest()
        recorded = ""
        if os.path.exists(self.chain_path):
            with open(self.chain_path) as fh:
                recorded = fh.read().strip()
        # M1 runs have no sidecar: integrity is "unknown", not failure.
        if not recorded:
            return {"ok": True, "events": count, "chain": chain,
                    "note": "m1-ledger-without-chain"}
        return {"ok": recorded == chain, "events": count, "chain": chain,
                "recorded": recorded}

    def completeness(self, required_events: tuple[str, ...] = REQUIRED_PREFIX_EVENTS,
                     terminal_events: tuple[str, ...] = TERMINAL_EVENTS) -> dict:
        """Gap AND required-event completeness. Any failure -> not promotion-eligible."""
        events = self.read_all()
        by_source: dict[str, list[int]] = {}
        types: set[str] = set()
        for evt in events:
            by_source.setdefault(evt.source.id, []).append(evt.source_sequence)
            types.add(evt.event_type)
        gaps = {}
        for source, seqs in by_source.items():
            expected = set(range(min(seqs), max(seqs) + 1)) if seqs else set()
            missing = sorted(expected - set(seqs))
            if missing:
                gaps[source] = missing
        missing_required = [t for t in required_events if t not in types]
        if not any(t in types for t in terminal_events):
            missing_required.append("terminal_harness_event")
        return {"complete": not gaps and not missing_required, "gaps": gaps,
                "missing_required": missing_required,
                "sources": sorted(by_source)}

    def completeness_for_outcome(self, outcome: str) -> dict:
        """M2 required-event policy for a terminal outcome (pass/fail/timeout/...)."""
        events = self.read_all()
        types = {evt.event_type for evt in events}
        by_source: dict[str, list[int]] = {}
        for evt in events:
            by_source.setdefault(evt.source.id, []).append(evt.source_sequence)
        gaps: dict[str, list[int]] = {}
        for source, seqs in by_source.items():
            expected = set(range(min(seqs), max(seqs) + 1)) if seqs else set()
            missing = sorted(expected - set(seqs))
            if missing:
                gaps[source] = missing
        policy = evaluate_policy(types, outcome)
        complete = policy["complete"] and not gaps
        result = {"complete": complete, "telemetry_incomplete": (not complete),
                  "gaps": gaps, "missing_required": policy["missing_required"],
                  "policy": policy["policy"], "sources": sorted(by_source)}
        return result
