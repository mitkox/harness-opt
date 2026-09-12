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


class ArtifactStore:
    def __init__(self, root: str):
        self.root = root
        os.makedirs(os.path.join(root, "blobs"), exist_ok=True)
        os.makedirs(os.path.join(root, "refs"), exist_ok=True)

    def _blob_path(self, digest: str) -> str:
        hexpart = digest.split(":", 1)[1]
        return os.path.join(self.root, "blobs", hexpart[:2], hexpart)

    def put(self, data: bytes, run_id: str) -> ArtifactRef:
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
        ref_path = os.path.join(self.root, "refs", run_id, digest.replace(":", "_") + ".json")
        os.makedirs(os.path.dirname(ref_path), exist_ok=True)
        ref = ArtifactRef(digest=digest, path=ref_path, size_bytes=len(data), complete=True)
        with open(ref_path, "w") as fh:
            json.dump(ref.model_dump(), fh)
        return ref

    def get(self, digest: str, run_id: str) -> bytes:
        """Cross-run reads denied unless the requesting run owns a ref."""
        ref_path = os.path.join(self.root, "refs", run_id, digest.replace(":", "_") + ".json")
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
