"""Deterministic, offline wheel construction for dependency-upgrade cases.

The deps pack must resolve upgrade targets from **local artifacts only**. The
approved wheelhouse does not carry a second version of any runtime dependency,
so a case ships the two versions of its fixture package as source trees and the
pack builds a wheel from them with this stdlib-only builder. The result is a
real, pip-installable wheel with a recorded sha256 and no network, no build
backend, and no build isolation.

Determinism: entries are sorted, stored uncompressed, and stamped with a fixed
timestamp, so the same source tree yields byte-identical wheels. This is what
lets the pack record an artifact hash that a later run can re-verify.
"""

from __future__ import annotations

import base64
import hashlib
import zipfile
from pathlib import Path

__all__ = ["build_wheel", "source_digest"]

_FIXED_DATE_TIME = (1980, 1, 1, 0, 0, 0)
_IGNORED = {"__pycache__", ".pytest_cache", ".git"}


def _record_hash(data: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=")
    return "sha256=" + digest.decode("ascii")


def _collect(source_dir: Path) -> list[tuple[str, bytes]]:
    files: list[tuple[str, bytes]] = []
    for path in sorted(source_dir.rglob("*")):
        if not path.is_file():
            continue
        if any(part in _IGNORED for part in path.parts):
            continue
        if path.suffix in (".pyc", ".pyo"):
            continue
        files.append((path.relative_to(source_dir).as_posix(), path.read_bytes()))
    return files


def source_digest(source_dir: str | Path) -> str:
    """Content digest of a local source tree (stable across filesystems)."""
    digest = hashlib.sha256()
    for name, data in _collect(Path(source_dir)):
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(data)
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def build_wheel(
    source_dir: str | Path,
    dist_name: str,
    version: str,
    out_dir: str | Path,
) -> Path:
    """Build a deterministic pure-python wheel from ``source_dir``.

    ``source_dir`` must contain the import package directory (normalized dist
    name) with its files; the generated ``.dist-info`` is appended here.
    """
    source = Path(source_dir)
    if not source.is_dir():
        raise FileNotFoundError(f"upgrade source tree is missing: {source}")
    normalized = dist_name.replace("-", "_")
    package_root = source / normalized
    if not package_root.is_dir():
        raise FileNotFoundError(
            f"upgrade source tree {source} has no package directory {normalized!r}"
        )

    dist_info = f"{normalized}-{version}.dist-info"
    metadata = (
        "Metadata-Version: 2.1\n"
        f"Name: {dist_name}\n"
        f"Version: {version}\n"
        "Summary: local deps-pack fixture package\n"
    ).encode()
    wheel_meta = (
        b"Wheel-Version: 1.0\n"
        b"Generator: hop-deps-pack/0.1\n"
        b"Root-Is-Purelib: true\n"
        b"Tag: py3-none-any\n"
    )

    entries: list[tuple[str, bytes]] = _collect(source)
    entries.append((f"{dist_info}/METADATA", metadata))
    entries.append((f"{dist_info}/WHEEL", wheel_meta))
    entries.sort(key=lambda item: item[0])

    record_lines = [f"{name},{_record_hash(data)},{len(data)}" for name, data in entries]
    record_lines.append(f"{dist_info}/RECORD,,")
    record = ("\n".join(record_lines) + "\n").encode("utf-8")
    entries.append((f"{dist_info}/RECORD", record))
    entries.sort(key=lambda item: item[0])

    target_dir = Path(out_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    wheel_path = target_dir / f"{normalized}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel_path, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, data in entries:
            info = zipfile.ZipInfo(name, date_time=_FIXED_DATE_TIME)
            info.external_attr = 0o644 << 16
            archive.writestr(info, data)
    return wheel_path
