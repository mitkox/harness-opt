"""Content-addressed artifact storage (AOP-005).

Layout: <root>/blobs/<sha256[0:2]>/<sha256> + <root>/refs/<run_id>/<sha256>.json
Writes stage to .partial then atomically rename; interrupted writes can never
appear complete. Tampered blobs fail verification on read.
"""

from __future__ import annotations

import json
import os
import tempfile

from .contracts.base import sha256_hex
from .contracts.records import ArtifactRef
from .files import atomic_json, contained_path, digest_hex, identifier


class ArtifactStore:
    def __init__(self, root: str, *, create: bool = True):
        self.root = root
        if create:
            os.makedirs(os.path.join(root, "blobs"), exist_ok=True)
            os.makedirs(os.path.join(root, "refs"), exist_ok=True)

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
        os.makedirs(os.path.dirname(dest), exist_ok=True)
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
        if not self.verify(digest):
            raise ValueError(f"artifact {digest} is tampered")
        ref_path = self._ref_path(digest, run_id)
        os.makedirs(os.path.dirname(ref_path), exist_ok=True)
        ref = ArtifactRef(digest=digest, path=ref_path, size_bytes=len(data), complete=True)
        payload = ref.model_dump()
        payload["classification"] = classification
        atomic_json(ref_path, payload)
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
