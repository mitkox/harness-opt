"""Durable trajectory event ledger (AOP-006 worker leases + M1 slice of AOP-012).

Per-run JSONL file, fsync per append batch. Per-source monotonic sequences;
at-least-once dedup by event_id; cursor-based reread for observer reconnect;
gap detection -> completeness report.
"""
from __future__ import annotations

import json
import os

from .contracts.records import TrajectoryEvent


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
                prev = self._last_seq.get(evt.source_id, -1)
                self._last_seq[evt.source_id] = max(prev, evt.source_sequence)

    def append(self, event: TrajectoryEvent) -> bool:
        """Returns False if duplicate (deduped). Raises on sequence regression."""
        if event.event_id in self._seen:
            return False
        prev = self._last_seq.get(event.source_id, -1)
        if event.source_sequence <= prev:
            raise ValueError(
                f"sequence regression for {event.source_id}: {event.source_sequence} <= {prev}"
            )
        with open(self.path, "a") as fh:
            fh.write(event.model_dump_json() + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        self._seen.add(event.event_id)
        self._last_seq[event.source_id] = event.source_sequence
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

    def completeness(self) -> dict:
        """Per-source gap detection. Any gap -> not promotion-eligible."""
        by_source: dict[str, list[int]] = {}
        for evt in self.read_all():
            by_source.setdefault(evt.source_id, []).append(evt.source_sequence)
        gaps = {}
        for source, seqs in by_source.items():
            expected = set(range(min(seqs), max(seqs) + 1)) if seqs else set()
            missing = sorted(expected - set(seqs))
            if missing:
                gaps[source] = missing
        return {"complete": not gaps, "gaps": gaps, "sources": sorted(by_source)}
