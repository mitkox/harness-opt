"""Holdout access ledger for the sealed evaluator store (AOP-020 half B).

Append-only, hash-chained record of every sealed-store access. The ledger is the
only authority for holdout-exposure reporting, so it records attempts that are
refused as well as granted reads:

* one JSON object per line, written with ``O_APPEND`` and ``fsync``;
* each entry carries ``prev`` (the previous entry digest) and ``digest`` (its
  own chain hash), so removing, reordering, or editing an entry is detectable;
* ``exposure()`` reports per-case read counts, flags repeated holdout exposure,
  and names runs that are ineligible for promotion-eligible claims.

The ledger lives next to the sealed store, outside the repository and outside
any path an agent or optimizer worker can reach.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import time
from dataclasses import dataclass, field

LEDGER_SCHEMA_VERSION = 1
GENESIS = "0" * 64
DEFAULT_REPEAT_THRESHOLD = 1


class LedgerError(RuntimeError):
    """The ledger is malformed or its hash chain is broken."""


def _canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


@dataclass
class AccessEntry:
    seq: int
    ts: float
    event: str
    case_id: str
    token: str
    actor: str
    role: str
    purpose: str
    run_id: str
    outcome: str
    content_digest: str
    prev: str
    digest: str = ""
    reason: str = ""
    schema_version: int = LEDGER_SCHEMA_VERSION

    def core(self) -> dict:
        payload = {
            "schema_version": self.schema_version,
            "seq": self.seq,
            "ts": self.ts,
            "event": self.event,
            "case_id": self.case_id,
            "token": self.token,
            "actor": self.actor,
            "role": self.role,
            "purpose": self.purpose,
            "run_id": self.run_id,
            "outcome": self.outcome,
            "content_digest": self.content_digest,
        }
        # Only present when set, so entries written before this field existed
        # keep the same digest.
        if self.reason:
            payload["reason"] = self.reason
        return payload

    def compute_digest(self) -> str:
        return hashlib.sha256(
            self.prev.encode("ascii") + b"\n" + _canonical(self.core())
        ).hexdigest()


@dataclass
class ExposureReport:
    counts: dict = field(default_factory=dict)
    repeated: list = field(default_factory=list)
    denied: int = 0
    ineligible_runs: list = field(default_factory=list)
    chain_ok: bool = True
    entries: int = 0

    def as_dict(self) -> dict:
        return {
            "counts": dict(sorted(self.counts.items())),
            "repeated": sorted(self.repeated),
            "denied": self.denied,
            "ineligible_runs": sorted(self.ineligible_runs),
            "chain_ok": self.chain_ok,
            "entries": self.entries,
        }


class AccessLedger:
    def __init__(self, path: str, *, repeat_threshold: int = DEFAULT_REPEAT_THRESHOLD):
        self.path = path
        self.lock_path = path + ".lock"
        self.repeat_threshold = repeat_threshold
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

    # -- append-only writer ------------------------------------------------
    def append(
        self,
        *,
        case_id: str,
        token: str,
        actor: str,
        role: str,
        purpose: str,
        run_id: str = "",
        outcome: str = "granted",
        content_digest: str = "",
        event: str = "read",
        reason: str = "",
    ) -> AccessEntry:
        with open(self.lock_path, "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                entries = self._read_entries(strict=False)
                previous = entries[-1].digest if entries else GENESIS
                entry = AccessEntry(
                    seq=len(entries) + 1,
                    ts=round(time.time(), 6),
                    event=event,
                    case_id=case_id,
                    token=token,
                    actor=actor,
                    role=role,
                    purpose=purpose,
                    run_id=run_id,
                    outcome=outcome,
                    content_digest=content_digest,
                    prev=previous,
                    reason=reason,
                )
                entry.digest = entry.compute_digest()
                line = json.dumps(
                    {**entry.core(), "prev": entry.prev, "digest": entry.digest},
                    sort_keys=True,
                ).encode("utf-8")
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                try:
                    os.write(fd, line + b"\n")
                    os.fsync(fd)
                finally:
                    os.close(fd)
                return entry
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    # -- reader ------------------------------------------------------------
    def entries(self) -> list[AccessEntry]:
        return self._read_entries(strict=True)

    def _read_entries(self, *, strict: bool) -> list[AccessEntry]:
        if not os.path.exists(self.path):
            return []
        out: list[AccessEntry] = []
        with open(self.path, encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    if strict:
                        raise LedgerError(f"malformed ledger entry at line {lineno}") from exc
                    break
                try:
                    entry = AccessEntry(
                        schema_version=int(row.get("schema_version", 0)),
                        seq=int(row["seq"]),
                        ts=float(row["ts"]),
                        event=str(row["event"]),
                        case_id=str(row["case_id"]),
                        token=str(row["token"]),
                        actor=str(row["actor"]),
                        role=str(row["role"]),
                        purpose=str(row["purpose"]),
                        run_id=str(row.get("run_id", "")),
                        outcome=str(row.get("outcome", "granted")),
                        content_digest=str(row.get("content_digest", "")),
                        prev=str(row["prev"]),
                        digest=str(row["digest"]),
                        reason=str(row.get("reason", "")),
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    if strict:
                        raise LedgerError(f"invalid ledger entry at line {lineno}") from exc
                    break
                out.append(entry)
        return out

    def verify(self) -> dict:
        """Detect removed, reordered, or edited entries."""
        previous = GENESIS
        entries = self.entries()
        for index, entry in enumerate(entries, start=1):
            if entry.seq != index:
                return {"ok": False, "error": f"sequence gap at {index}"}
            if entry.prev != previous:
                return {"ok": False, "error": f"broken chain at {index}"}
            if entry.digest != entry.compute_digest():
                return {"ok": False, "error": f"tampered entry at {index}"}
            previous = entry.digest
        return {"ok": True, "entries": len(entries), "head": previous}

    # -- exposure policy ---------------------------------------------------
    def exposure(self, *, repeat_threshold: int | None = None) -> ExposureReport:
        threshold = self.repeat_threshold if repeat_threshold is None else repeat_threshold
        report = ExposureReport()
        chain = self.verify()
        report.chain_ok = bool(chain.get("ok"))
        counts: dict[str, int] = {}
        ineligible: set[str] = set()
        try:
            entries = self.entries()
        except LedgerError:
            report.chain_ok = False
            entries = []
        report.entries = len(entries)
        for entry in entries:
            if entry.event != "read":
                continue
            # Never surface the raw sealed token: unknown-case denials are keyed
            # by a short hash so CLI/ledger output cannot disclose hidden_ref.
            key = entry.case_id or (
                "token:" + hashlib.sha256(entry.token.encode("utf-8")).hexdigest()[:12]
            )
            if entry.outcome != "granted":
                report.denied += 1
                if entry.run_id:
                    ineligible.add(entry.run_id)
                continue
            counts[key] = counts.get(key, 0) + 1
            if entry.role != "verifier" and entry.run_id:
                ineligible.add(entry.run_id)
        report.counts = counts
        report.repeated = [key for key, count in counts.items() if count > threshold]
        for entry in entries:
            if (
                entry.event == "read"
                and entry.run_id
                and counts.get(entry.case_id or entry.token, 0) > threshold
            ):
                ineligible.add(entry.run_id)
        report.ineligible_runs = sorted(ineligible)
        if not report.chain_ok:
            report.ineligible_runs = sorted(
                set(report.ineligible_runs) | {entry.run_id for entry in entries if entry.run_id}
            )
        return report

    def promotion_eligible(self, run_id: str) -> bool:
        report = self.exposure()
        return bool(run_id) and report.chain_ok and run_id not in report.ineligible_runs
