"""Immutable local component registry (M3, BUILD_PLAN §7).

Layout under the (gitignored) HOP home::

    <root>/
      index.json            canonical registry index
      index.sha256          integrity anchor for the index
      content/<hex>/...     component content, keyed by content digest
      records/<hex>.json    per-record copies for digest-addressed lookup

Every component is content-addressed. Re-registering identical content is
idempotent. Re-registering the *same* (type, name, version) with different
content fails closed: changing content requires a new version/identity. Reads
recompute the content digest from stored bytes, so silent mutation of the
store is detected rather than accepted.
"""
from __future__ import annotations

import json
import os
import tempfile

from .contracts.profile import RegistryComponent, digest_payload, tree_digest


class RegistryError(RuntimeError):
    pass


class ComponentNotFound(RegistryError, KeyError):
    pass


class ComponentCollision(RegistryError):
    """Same (type, name, version), different content -- mutation attempt."""


class RegistryIntegrityError(RegistryError):
    """Stored content no longer matches its recorded digest (tamper)."""


def compute_record_digest(record: RegistryComponent) -> str:
    payload = record.model_dump(mode="json", exclude={
        "record_digest", "created_at", "source", "source_revision", "metadata",
        "files",
    })
    return digest_payload(payload)


class ComponentRegistry:
    def __init__(self, root: str):
        self.root = root
        os.makedirs(os.path.join(root, "content"), exist_ok=True)
        os.makedirs(os.path.join(root, "records"), exist_ok=True)
        self.index_path = os.path.join(root, "index.json")
        self.index_anchor = os.path.join(root, "index.sha256")
        self._index: dict[str, RegistryComponent] = {}
        self._load()

    # -- persistence -------------------------------------------------------
    def _load(self) -> None:
        if not os.path.exists(self.index_path):
            self._index = {}
            return
        with open(self.index_path, "rb") as fh:
            raw = fh.read()
        recorded = ""
        if os.path.exists(self.index_anchor):
            with open(self.index_anchor) as fh:
                recorded = fh.read().strip()
        if recorded and recorded != _sha256_hex(raw):
            raise RegistryIntegrityError(
                "registry index integrity anchor mismatch (tampered index)")
        payload = json.loads(raw.decode("utf-8"))
        components = payload.get("components", {})
        for record_digest, data in components.items():
            record = RegistryComponent.model_validate(data)
            if record.record_digest != record_digest:
                raise RegistryIntegrityError(
                    f"index record {record_digest} has mismatched digest field")
            self._index[record_digest] = record

    def _write_index(self) -> None:
        serialized: dict[str, dict] = {}
        for digest in sorted(self._index):
            serialized[digest] = self._index[digest].model_dump(mode="json")
        payload = {"schema_version": "0.3", "components": serialized}
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True).encode("utf-8")
        _atomic_write(self.index_path, raw)
        _atomic_write(self.index_anchor, _sha256_hex(raw).encode())

    # -- write -------------------------------------------------------------
    def register(self, record: RegistryComponent, files: dict[str, bytes]) -> RegistryComponent:
        """Register immutable content. Idempotent; collisions fail closed."""
        if not record.content_digest:
            record.content_digest = tree_digest(files)
        else:
            actual = tree_digest(files)
            if actual != record.content_digest:
                raise RegistryError(
                    f"content digest mismatch for {record.logical_name}: "
                    f"declared {record.content_digest}, computed {actual}")
        stored = self._stored_files(record.content_digest)
        if stored is not None and stored != files:
            raise RegistryIntegrityError(
                f"content store for {record.content_digest} already exists with "
                "different bytes")
        if not record.record_digest:
            record.record_digest = compute_record_digest(record)
        else:
            expected = compute_record_digest(record)
            if expected != record.record_digest:
                raise RegistryError("record_digest does not match record content")

        existing = self._index.get(record.record_digest)
        if existing is not None:
            self._verify_existing(existing)
            return existing

        # Collision: same identity, different content/digest.
        for other in self._index.values():
            if (other.component_type == record.component_type
                    and other.logical_name == record.logical_name
                    and other.version == record.version):
                raise ComponentCollision(
                    f"{record.component_type.value}/{record.logical_name}@"
                    f"{record.version} already registered as {other.record_digest}; "
                    "immutable components cannot be silently mutated "
                    "(bump the version)")

        self._write_content(record.content_digest, files)
        record.files = _file_list(files)
        self._index[record.record_digest] = record
        _atomic_write(os.path.join(self.root, "records",
                                   record.record_digest.split(":")[1] + ".json"),
                      json.dumps(record.model_dump(mode="json"), sort_keys=True,
                                 indent=2).encode())
        self._write_index()
        return record

    # -- read --------------------------------------------------------------
    def get(self, name: str, version: str,
            component_type=None) -> tuple[RegistryComponent, dict[str, bytes]]:
        matches = [r for r in self._index.values()
                   if r.logical_name == name and r.version == version
                   and (component_type is None or r.component_type == component_type)]
        if not matches:
            raise ComponentNotFound(f"component {name}@{version} not registered")
        if len(matches) > 1:
            raise RegistryError(
                f"ambiguous component identity {name}@{version}: "
                f"{[m.component_type.value for m in matches]}")
        record = matches[0]
        self._verify_existing(record)
        files = self._stored_files(record.content_digest)
        if files is None:
            raise RegistryIntegrityError(
                f"content missing for {name}@{version} ({record.content_digest})")
        return record, files

    def by_digest(self, record_digest: str) -> tuple[RegistryComponent, dict[str, bytes]]:
        record = self._index.get(record_digest)
        if record is None:
            raise ComponentNotFound(f"component digest {record_digest} not registered")
        self._verify_existing(record)
        files = self._stored_files(record.content_digest)
        if files is None:
            raise RegistryIntegrityError(f"content missing for {record_digest}")
        return record, files

    def versions(self, name: str, component_type=None) -> list[str]:
        from .semver import Version

        out = [r.version for r in self._index.values()
               if r.logical_name == name
               and (component_type is None or r.component_type == component_type)]
        out.sort(key=lambda v: Version.parse(v), reverse=True)
        return out

    def all_components(self) -> list[RegistryComponent]:
        return sorted(self._index.values(),
                      key=lambda r: (r.component_type.value, r.logical_name, r.version))

    def find(self, name: str | None = None, component_type=None,
             version: str | None = None) -> list[RegistryComponent]:
        out = []
        for record in self.all_components():
            if name is not None and record.logical_name != name:
                continue
            if component_type is not None and record.component_type != component_type:
                continue
            if version is not None and record.version != version:
                continue
            out.append(record)
        return out

    def materialize(self, record_digest: str, dest: str) -> None:
        _, files = self.by_digest(record_digest)
        for rel, data in files.items():
            target = os.path.join(dest, rel)
            os.makedirs(os.path.dirname(target) or dest, exist_ok=True)
            _atomic_write(target, data)

    # -- integrity ---------------------------------------------------------
    def _verify_existing(self, record: RegistryComponent) -> None:
        files = self._stored_files(record.content_digest)
        if files is None:
            raise RegistryIntegrityError(
                f"content missing for {record.logical_name}@{record.version}")
        actual = tree_digest(files)
        if actual != record.content_digest:
            raise RegistryIntegrityError(
                f"content digest mismatch for {record.logical_name}@"
                f"{record.version}: expected {record.content_digest}, got {actual}")

    def verify_all(self) -> list[dict]:
        """Recompute every record; return per-record integrity evidence."""
        results = []
        for record in self.all_components():
            entry = {"type": record.component_type.value,
                     "name": record.logical_name, "version": record.version,
                     "record_digest": record.record_digest,
                     "content_digest": record.content_digest}
            try:
                self._verify_existing(record)
                recomputed = compute_record_digest(record)
                if recomputed != record.record_digest:
                    raise RegistryIntegrityError("record digest mismatch")
                entry["ok"] = True
            except RegistryIntegrityError as exc:
                entry["ok"] = False
                entry["error"] = str(exc)
            results.append(entry)
        return results

    # -- content store -----------------------------------------------------
    def _content_dir(self, content_digest: str) -> str:
        return os.path.join(self.root, "content", content_digest.split(":")[1])

    def _stored_files(self, content_digest: str) -> dict[str, bytes] | None:
        base = self._content_dir(content_digest)
        if not os.path.isdir(base):
            return None
        files: dict[str, bytes] = {}
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(dirnames)
            for name in sorted(filenames):
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, base).replace(os.sep, "/")
                with open(full, "rb") as fh:
                    files[rel] = fh.read()
        return files

    def _write_content(self, content_digest: str, files: dict[str, bytes]) -> None:
        base = self._content_dir(content_digest)
        for rel, data in files.items():
            target = os.path.join(base, rel)
            os.makedirs(os.path.dirname(target) or base, exist_ok=True)
            _atomic_write(target, data)


def _file_list(files: dict[str, bytes]) -> list[dict]:
    return [{"path": rel, "size_bytes": len(files[rel]),
             "digest": "sha256:" + _sha256_hex(files[rel]),
             "executable": bool(files[rel].startswith(b"#!"))}
            for rel in sorted(files)]


def _sha256_hex(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def _atomic_write(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", suffix=".partial")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
