"""Durable trajectory event ledger (AOP-006 worker leases + M1 slice of AOP-012).

Per-run JSONL file, fsync per append batch. Per-source monotonic sequences;
at-least-once dedup by event_id; cursor-based reread for observer reconnect;
gap detection plus required-terminal-event detection -> completeness report.
Sequence contiguity alone is not sufficient: a run whose required events were
never emitted is incomplete even with no gaps.
"""
from __future__ import annotations

import json
import os

from .contracts.records import TrajectoryEvent

REQUIRED_PREFIX_EVENTS = ("run.admitted", "workspace.prepared", "harness.accepted")
TERMINAL_EVENTS = ("harness.exited", "harness.crashed", "harness.timeout",
                   "harness.cancelled")


class EventLedger:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._seen: set[str] = set()
        self._last_seq: dict[str, int] = {}
        if os.path.exists(path):
            self._replay()

    def _replay(self) -> None:
        with open(self.path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                evt = TrajectoryEvent.model_validate(json.loads(line))
                self._seen.add(evt.event_id)
                prev = self._last_seq.get(evt.source.id, -1)
                self._last_seq[evt.source.id] = max(prev, evt.source_sequence)

    def append(self, event: TrajectoryEvent) -> bool:
        """Returns False if duplicate (deduped). Raises on sequence regression."""
        if event.event_id in self._seen:
            return False
        prev = self._last_seq.get(event.source.id, -1)
        if event.source_sequence <= prev:
            raise ValueError(
                f"sequence regression for {event.source.id}: "
                f"{event.source_sequence} <= {prev}"
            )
        with open(self.path, "a") as fh:
            fh.write(event.model_dump_json() + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        self._seen.add(event.event_id)
        self._last_seq[event.source.id] = event.source_sequence
        return True

    def read_all(self) -> list[TrajectoryEvent]:
        events = []
        if not os.path.exists(self.path):
            return events
        with open(self.path) as fh:
            for line in fh:
                if line.strip():
                    events.append(TrajectoryEvent.model_validate(json.loads(line)))
        return events

    def read_from_cursor(self, cursor: int) -> tuple[list[TrajectoryEvent], int]:
        """Observer reconnect: resume after `cursor` events. Returns (events, new_cursor)."""
        events = self.read_all()
        return events[cursor:], len(events)

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
