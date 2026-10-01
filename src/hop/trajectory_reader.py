"""Read-only bounded pages with whole-file integrity validation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .contracts.records import TrajectoryEvent
from .telemetry.taxonomy import assert_known_event
from .trajectories import EventLedger


def read_page(path: str, cursor: int = 0, limit: int = 100, event_type: str = "") -> dict:
    if cursor < 0 or not 1 <= limit <= 10000:
        raise ValueError("cursor must be nonnegative; limit must be between 1 and 10000")
    source = Path(path)
    if Path(path + ".pending").exists():
        raise ValueError("incomplete trajectory write")
    before = source.stat()
    rows = []
    count = 0
    next_cursor = cursor
    chain = "0" * 64
    sequences = {}
    with source.open("rb") as stream:
        for raw in stream:
            if not raw.strip():
                continue
            chain = hashlib.sha256(chain.encode() + raw.rstrip(b"\r\n")).hexdigest()
            event = TrajectoryEvent.model_validate_json(raw)
            assert_known_event(event.event_type)
            EventLedger._validate_authority(event)
            if event.source_sequence <= sequences.get(event.source.id, -1):
                raise ValueError("stored source sequence regression")
            sequences[event.source.id] = event.source_sequence
            if count >= cursor and len(rows) < limit:
                next_cursor = count + 1
                if not event_type or event.event_type == event_type:
                    rows.append(json.loads(event.model_dump_json()))
            count += 1
    sidecar = Path(path + ".sha256")
    recorded = sidecar.read_text().strip() if sidecar.exists() else ""
    if sidecar.exists() and recorded != chain:
        raise ValueError("trajectory integrity chain mismatch")
    after = source.stat()
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise ValueError("trajectory changed during read; retry the page")
    if Path(path + ".pending").exists():
        raise ValueError("trajectory write in progress; retry the page")
    return {
        "events": rows,
        "next_cursor": next_cursor,
        "has_more": next_cursor < count,
        "total_events": count,
        "integrity": "verified" if recorded else "legacy_without_chain",
    }
