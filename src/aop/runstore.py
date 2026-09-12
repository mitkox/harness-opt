"""Durable run state + worker leases with fencing (AOP-006). SQLite WAL backend."""
from __future__ import annotations

import json
import os
import sqlite3
import threading

from .contracts.records import AttemptRecord, RunOutcome, RunRecord, RunStatus


class RunStore:
    def __init__(self, path: str):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, record TEXT NOT NULL, "
            "idempotency_key TEXT UNIQUE)"
        )
        self.conn.commit()

    def admit(self, record: RunRecord) -> tuple[RunRecord, bool]:
        """Idempotent admission: same idempotency_key returns the existing run."""
        with self._lock:
            if record.idempotency_key:
                row = self.conn.execute(
                    "SELECT record FROM runs WHERE idempotency_key=?",
                    (record.idempotency_key,),
                ).fetchone()
                if row:
                    return RunRecord.model_validate(json.loads(row[0])), False
            try:
                self.conn.execute(
                    "INSERT INTO runs (run_id, record, idempotency_key) VALUES (?,?,?)",
                    (record.run_id, record.model_dump_json(),
                     record.idempotency_key or None),
                )
                self.conn.commit()
            except sqlite3.IntegrityError:
                row = self.conn.execute(
                    "SELECT record FROM runs WHERE run_id=?", (record.run_id,)).fetchone()
                return RunRecord.model_validate(json.loads(row[0])), False
            return record, True

    def get(self, run_id: str) -> RunRecord:
        with self._lock:
            row = self.conn.execute(
                "SELECT record FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return RunRecord.model_validate(json.loads(row[0]))

    def _save(self, record: RunRecord) -> None:
        with self._lock:
            self.conn.execute(
                "UPDATE runs SET record=? WHERE run_id=?",
                (record.model_dump_json(), record.run_id))
            self.conn.commit()

    def set_status(self, run_id: str, status: RunStatus) -> RunRecord:
        record = self.get(run_id)
        record.status = status
        self._save(record)
        return record

    def new_attempt(self, run_id: str, attempt: AttemptRecord) -> AttemptRecord:
        record = self.get(run_id)
        attempt.fencing_token = max([a.fencing_token for a in record.attempts] + [0]) + 1
        record.attempts.append(attempt)
        self._save(record)
        return attempt

    def finalize_attempt(self, run_id: str, attempt_id: str, fencing_token: int,
                         outcome: RunOutcome, agent_claim: str = "") -> AttemptRecord:
        """A stale worker (old fencing token) cannot finalize a newer attempt."""
        record = self.get(run_id)
        current_max = max(a.fencing_token for a in record.attempts)
        if fencing_token < current_max:
            raise PermissionError(
                f"stale worker (token {fencing_token} < {current_max}) cannot finalize")
        for attempt in record.attempts:
            if attempt.attempt_id == attempt_id:
                if attempt.fencing_token != fencing_token:
                    raise PermissionError("fencing token mismatch")
                attempt.outcome = outcome
                attempt.status = RunStatus.COMPLETED
                attempt.agent_claim = agent_claim
                break
        else:
            raise KeyError(attempt_id)
        if all(a.status == RunStatus.COMPLETED for a in record.attempts):
            record.status = RunStatus.COMPLETED
        self._save(record)
        return attempt
