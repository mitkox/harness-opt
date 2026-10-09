"""Durable local file publication and constrained store identifiers."""

from __future__ import annotations

import fcntl
import json
import os
import re
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path

OWNER_DIR_MODE = 0o700
OWNER_FILE_MODE = 0o600


class InsecurePermissions(RuntimeError):
    """A pre-existing path is not owner-only. Fail closed and never chmod it."""

    def __init__(self, path: str | Path, observed_mode: int, *, kind: str = "path"):
        self.path = str(path)
        self.observed_mode = observed_mode
        super().__init__(
            f"insecure {kind} permissions {observed_mode:04o} on {self.path}; "
            "refusing to continue (a pre-existing path is never chmod-ed silently)"
        )


def _owner_uid() -> int:
    if hasattr(os, "geteuid"):
        return os.geteuid()
    return os.getuid()


def owner_only_mode(mode: int) -> bool:
    """True when no group/other permission bit is set."""
    return mode & 0o077 == 0


def assert_owner_only(path: str | Path, *, kind: str = "path") -> int:
    """Return the observed mode or raise InsecurePermissions. Never modifies."""
    target = Path(path)
    info = target.stat()
    mode = stat.S_IMODE(info.st_mode)
    if info.st_uid != _owner_uid():
        raise InsecurePermissions(target, mode, kind=f"{kind} (foreign owner)")
    if not owner_only_mode(mode):
        raise InsecurePermissions(target, mode, kind=kind)
    return mode


def _make_dirs_owner_only(directory: Path) -> None:
    """Create missing ancestors with 0700. Existing directories are untouched."""
    missing: list[Path] = []
    current = directory
    while not current.exists() and current != current.parent:
        missing.append(current)
        current = current.parent
    for target in reversed(missing):
        target.mkdir(mode=OWNER_DIR_MODE)
        os.chmod(target, OWNER_DIR_MODE)


def ensure_owner_dir(path: str | Path) -> str:
    """Create a 0700 directory, or verify an existing directory is owner-only."""
    target = Path(path)
    if target.exists():
        if not target.is_dir():
            raise ValueError(f"not a directory: {target}")
        assert_owner_only(target, kind="directory")
        return str(target)
    _make_dirs_owner_only(target)
    os.chmod(target, OWNER_DIR_MODE)
    assert_owner_only(target, kind="directory")
    return str(target)


def ensure_owner_file(path: str | Path) -> str:
    """Create a 0600 file, or verify an existing file is owner-only."""
    target = Path(path)
    if target.exists():
        if not target.is_file():
            raise ValueError(f"not a file: {target}")
        assert_owner_only(target, kind="file")
        return str(target)
    _make_dirs_owner_only(target.parent)
    fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, OWNER_FILE_MODE)
    os.close(fd)
    os.chmod(target, OWNER_FILE_MODE)
    return str(target)


def identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value):
        raise ValueError(f"invalid store identifier: {value!r}")
    return value


def digest_hex(value: str) -> str:
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise ValueError("invalid sha256 digest")
    return value[7:]


def contained_path(root: str, *parts: str) -> str:
    base = Path(root).resolve()
    target = base.joinpath(*parts).resolve()
    if not target.is_relative_to(base):
        raise ValueError("store path escapes its root")
    return str(target)


def atomic_write(path: str | Path, data: bytes) -> None:
    path = Path(path)
    _make_dirs_owner_only(path.parent)
    fd, staging = tempfile.mkstemp(prefix=".hop-", suffix=".partial", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staging, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(staging):
            os.unlink(staging)


def atomic_json(path: str | Path, payload: object) -> None:
    atomic_write(path, json.dumps(payload, sort_keys=True, indent=2).encode())


@contextmanager
def file_lock(path: str):
    _make_dirs_owner_only(Path(path).parent)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, OWNER_FILE_MODE)
    with os.fdopen(fd, "a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)
