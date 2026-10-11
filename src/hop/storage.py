"""Content-addressed artifact storage (AOP-005).

Layout: <root>/blobs/<sha256[0:2]>/<sha256> + <root>/refs/<run_id>/<sha256>.json
Writes stage to .partial then atomically rename; interrupted writes can never
appear complete. Tampered blobs fail verification on read.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime

from .contracts.base import sha256_hex
from .contracts.records import ArtifactRef
from .files import (
    assert_owner_only,
    atomic_json,
    contained_path,
    digest_hex,
    ensure_owner_dir,
    ensure_owner_file,
    identifier,
)

ACTIVITY_GENESIS = "0" * 64


class ArtifactStore:
    def __init__(self, root: str, *, create: bool = True):
        self.root = root
        if create:
            ensure_owner_dir(root)
            ensure_owner_dir(os.path.join(root, "blobs"))
            ensure_owner_dir(os.path.join(root, "refs"))

    def _blob_path(self, digest: str) -> str:
        hexpart = digest_hex(digest)
        return contained_path(self.root, "blobs", hexpart[:2], hexpart)

    def _ref_path(self, digest: str, run_id: str) -> str:
        identifier(run_id)
        digest_hex(digest)
        return contained_path(self.root, "refs", run_id, digest.replace(":", "_") + ".json")

    def put(self, data: bytes, run_id: str) -> ArtifactRef:
        return self.put_classified(data, run_id, classification="internal")

    def put_classified(
        self, data: bytes, run_id: str, classification: str = "internal"
    ) -> ArtifactRef:
        identifier(run_id)
        if classification not in ("public", "internal", "confidential", "secret"):
            raise ValueError(f"unknown classification {classification!r}")
        digest = "sha256:" + sha256_hex(data)
        dest = self._blob_path(digest)
        ensure_owner_dir(os.path.dirname(dest))
        if not os.path.exists(dest):
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(dest), suffix=".partial")
            try:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(data)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.rename(tmp, dest)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        assert_owner_only(dest, kind="artifact blob")
        if not self.verify(digest):
            raise ValueError(f"artifact {digest} is tampered")
        ref_path = self._ref_path(digest, run_id)
        ensure_owner_dir(os.path.dirname(ref_path))
        ref = ArtifactRef(digest=digest, path=ref_path, size_bytes=len(data), complete=True)
        payload = ref.model_dump()
        payload["classification"] = classification
        atomic_json(ref_path, payload)
        assert_owner_only(ref_path, kind="artifact reference")
        return ref

    def list_refs(self, run_id: str) -> list[dict]:
        """Artifact references owned by a run (for investigation/replay-check)."""
        identifier(run_id)
        ref_dir = contained_path(self.root, "refs", run_id)
        if not os.path.isdir(ref_dir):
            return []
        out = []
        for name in sorted(os.listdir(ref_dir)):
            if not name.endswith(".json"):
                continue
            with open(contained_path(self.root, "refs", run_id, name)) as fh:
                out.append(json.load(fh))
        return out

    def get(self, digest: str, run_id: str) -> bytes:
        """Cross-run reads denied unless the requesting run owns a ref."""
        identifier(run_id)
        digest_hex(digest)
        ref_path = self._ref_path(digest, run_id)
        if not os.path.exists(ref_path):
            raise PermissionError(f"run {run_id} has no reference to {digest}")
        with open(self._blob_path(digest), "rb") as fh:
            data = fh.read()
        if "sha256:" + sha256_hex(data) != digest:
            raise ValueError(f"artifact {digest} failed integrity verification (tampered)")
        return data

    def verify(self, digest: str) -> bool:
        path = self._blob_path(digest)
        if not os.path.exists(path):
            return False
        with open(path, "rb") as fh:
            return "sha256:" + sha256_hex(fh.read()) == digest


class ActivityHistoryError(RuntimeError):
    """The local activity history is missing, truncated, reordered or modified."""


class ActivityHistory:
    """Append-only local record of evaluation/qualification actions.

    Each record is a JSON line bound to its predecessor by sha256. A separate
    0600 anchor file holds the expected record count and last hash, so a dropped
    tail record is detected as well as in-place modification. Retention is an
    explicit operation: it writes a ``retention`` marker naming exactly what was
    dropped, and it never truncates silently.
    """

    def __init__(self, path: str, *, create: bool = True):
        self.path = path
        self.anchor_path = path + ".anchor"
        if create:
            ensure_owner_dir(os.path.dirname(os.path.abspath(path)))
            ensure_owner_file(path)
            if not os.path.exists(self.anchor_path):
                self._write_anchor(0, ACTIVITY_GENESIS)
        if os.path.exists(path):
            assert_owner_only(path, kind="activity history")
        if os.path.exists(self.anchor_path):
            assert_owner_only(self.anchor_path, kind="activity history anchor")

    @staticmethod
    def _entry_hash(prev: str, entry: dict) -> str:
        body = json.dumps(entry, sort_keys=True, separators=(",", ":")).encode()
        return sha256_hex(prev.encode() + body)

    def _entries(self) -> list[dict]:
        if not os.path.exists(self.path):
            return []
        entries = []
        with open(self.path, encoding="utf-8") as fh:
            for number, line in enumerate(fh, 1):
                if not line.strip():
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise ActivityHistoryError(
                        f"activity history line {number} is not valid JSON"
                    ) from exc
        return entries

    def _write_anchor(self, count: int, last_hash: str) -> None:
        atomic_json(self.anchor_path, {"count": count, "last_hash": last_hash})

    def _read_anchor(self) -> dict | None:
        if not os.path.exists(self.anchor_path):
            return None
        with open(self.anchor_path, encoding="utf-8") as fh:
            text = fh.read().strip()
        if not text:
            return None
        return json.loads(text)

    def append(
        self,
        action: str,
        *,
        bundle_digest: str = "",
        verifier_version: str = "",
        outcome: str = "",
        detail: dict | None = None,
        recorded_at: str | None = None,
    ) -> dict:
        entries = self._entries()
        prev = entries[-1]["hash"] if entries else ACTIVITY_GENESIS
        entry: dict = {
            "seq": (entries[-1]["seq"] + 1) if entries else 1,
            "prev": prev,
            "recorded_at": recorded_at or datetime.now(UTC).isoformat(),
            "action": action,
            "bundle_digest": bundle_digest,
            "verifier_version": verifier_version,
            "outcome": outcome,
        }
        if detail:
            entry["detail"] = detail
        entry["hash"] = self._entry_hash(prev, entry)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        self._write_anchor(entry["seq"], entry["hash"])
        return entry

    def verify(self) -> dict:
        entries = self._entries()
        prefix_records = 0
        if entries and entries[0].get("action") == "retention":
            marker = entries[0]
            marker_body = {k: v for k, v in marker.items() if k != "hash"}
            if self._entry_hash(ACTIVITY_GENESIS, marker_body) != marker.get("hash"):
                raise ActivityHistoryError("activity history retention marker was modified")
            dropped = marker.get("detail", {})
            prefix_records = int(dropped.get("dropped_records", 0))
            prev = str(dropped.get("dropped_last_hash", ""))
            expected = int(marker.get("seq", 0)) + 1
            remaining = entries[1:]
        else:
            prev = ACTIVITY_GENESIS
            expected = 1
            remaining = entries
        for entry in remaining:
            if entry.get("seq") != expected:
                raise ActivityHistoryError(
                    f"activity history record {expected} has seq {entry.get('seq')!r} "
                    "(record dropped or reordered)"
                )
            if entry.get("prev") != prev:
                raise ActivityHistoryError(f"activity history chain broken at record {expected}")
            body = {k: v for k, v in entry.items() if k != "hash"}
            if self._entry_hash(prev, body) != entry.get("hash"):
                raise ActivityHistoryError(f"activity history record {expected} was modified")
            prev = entry["hash"]
            expected += 1
        anchor = self._read_anchor()
        if anchor is None:
            if entries:
                raise ActivityHistoryError("activity history anchor is missing")
        else:
            last = entries[-1]["hash"] if entries else ACTIVITY_GENESIS
            if anchor.get("count") != len(entries) or anchor.get("last_hash") != last:
                raise ActivityHistoryError(
                    "activity history was truncated or had records dropped after the last anchor"
                )
        return {
            "records": len(entries),
            "retained_prefix_records": prefix_records,
            "last_hash": prev,
        }

    def retain(self, max_records: int) -> int:
        """Explicit retention. Returns the number of records dropped (0 = none)."""
        if max_records < 1:
            raise ValueError("max_records must be at least 1")
        entries = self._entries()
        if len(entries) <= max_records:
            return 0
        dropped = entries[: len(entries) - max_records]
        kept = entries[len(entries) - max_records :]
        marker: dict = {
            "seq": dropped[-1]["seq"],
            "prev": ACTIVITY_GENESIS,
            "recorded_at": datetime.now(UTC).isoformat(),
            "action": "retention",
            "bundle_digest": "",
            "verifier_version": "",
            "outcome": "retained",
            "detail": {
                "dropped_records": len(dropped),
                "dropped_through_seq": dropped[-1]["seq"],
                "dropped_last_hash": dropped[-1]["hash"],
            },
        }
        marker["hash"] = self._entry_hash(ACTIVITY_GENESIS, marker)
        lines = [json.dumps(marker, sort_keys=True)]
        lines += [json.dumps(entry, sort_keys=True) for entry in kept]
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        assert_owner_only(self.path, kind="activity history")
        self._write_anchor(len(lines), kept[-1]["hash"] if kept else marker["hash"])
        return len(dropped)
