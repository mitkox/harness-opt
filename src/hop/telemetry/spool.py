"""M2 local telemetry spool + Collector-outage recovery (AOP-012/013).

Authoritative trajectory events are always durable in the run ledger first.
OTel span forwarding to the local Collector is best-effort: spans are spooled
to runs/_spool/<run_id>.jsonl and flushed asynchronously. Collector states:

- unavailable before run: execution proceeds (policy allows), spans spool;
- disappears mid-run: subsequent exports spool, gaps detectable on flush;
- returns: flush() retries from spool, then marks telemetry.recovered;
- process restart with pending spool: new SpoolQueue replays the same files.

Promotion/evaluation eligibility distinguishes:
- observability backend unavailable (spool pending, ledger complete), from
- authoritative trajectory incomplete (ledger gaps/missing evidence).
"""

from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager

from ..files import atomic_write, identifier


def collector_available(collector_dir: str) -> bool:
    from ..envcompat import COLLECTOR_DOWN_VARS, is_flag_set

    if is_flag_set(*COLLECTOR_DOWN_VARS):
        return False
    # Absent marker + absent dir both mean "no collector"; presence of the
    # up-marker means available. Default local dir without marker is treated
    # as available (writes succeed) unless explicitly taken down.
    return not os.path.exists(os.path.join(collector_dir, ".collector-down"))


class SpoolQueue:
    def __init__(self, spool_dir: str, collector_dir: str):
        self.spool_dir = spool_dir
        self.collector_dir = collector_dir
        os.makedirs(spool_dir, exist_ok=True)

    def _path(self, run_id: str) -> str:
        identifier(run_id)
        return os.path.join(self.spool_dir, f"{run_id}.jsonl")

    @contextmanager
    def _locked(self, run_id: str):
        with open(self._path(run_id) + ".lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def enqueue(self, run_id: str, payload: dict) -> None:
        path = self._path(run_id)
        with self._locked(run_id), open(path, "a") as fh:
            fh.write(json.dumps(payload, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def pending(self, run_id: str) -> list[dict]:
        path = self._path(run_id)
        if not os.path.exists(path):
            return []
        with open(path) as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def pending_count(self, run_id: str) -> int:
        return len(self.pending(run_id))

    def flush(self, run_id: str) -> dict:
        """Try to deliver spooled payloads to the collector. Returns report."""
        with self._locked(run_id):
            return self._flush(run_id)

    def _flush(self, run_id: str) -> dict:
        items = self.pending(run_id)
        if not items:
            return {"flushed": 0, "pending": 0, "collector": "idle-empty"}
        if not collector_available(self.collector_dir):
            return {
                "flushed": 0,
                "pending": len(items),
                "collector": "unavailable-spooled",
                "telemetry_gap": True,
            }
        os.makedirs(self.collector_dir, exist_ok=True)
        dest = os.path.join(self.collector_dir, f"{run_id}.jsonl")
        delivered = []
        if os.path.exists(dest):
            with open(dest) as fh:
                delivered = [json.loads(line) for line in fh if line.strip()]
        # The local collector is a replayable projection. Publishing the
        # deduplicated set atomically makes a crash before spool removal safe.
        lines = dict.fromkeys(json.dumps(item, sort_keys=True) for item in delivered + items)
        atomic_write(dest, ("\n".join(lines) + "\n").encode())
        os.remove(self._path(run_id))
        return {"flushed": len(items), "pending": 0, "collector": "recovered"}
