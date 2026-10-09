"""Backup and restore of the local evidence chain (AOP-051, task-30).

One consistent set: the SQLite run ledger (snapshotted through the SQLite
backup API, so it can never be torn), the content-addressed artifact store, the
per-run evidence directories, the component registry and profile/lock store,
and the local model inventory. Backup and restore are local-only: no network,
no model endpoint, and the restored store keeps owner-only modes.

A restore verifies the manifest and every file digest *before* writing, then
re-verifies what it wrote and reports the evidence chain: run records whose
artifacts do not resolve, and attempts whose run directory is missing (fenced,
never silently adopted).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

from .config import Paths, current_paths
from .files import ensure_owner_dir

BACKUP_SCHEMA_VERSION = 1
MANIFEST_NAME = "backup-manifest.json"
_LEDGER_DB = "ledger.db"


class BackupError(RuntimeError):
    """A backup is invalid, truncated, or the restore target is not usable."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rel_files(root: Path, *, skip_ledger: bool = False) -> list[Path]:
    if not root.exists():
        return []
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name != "__pycache__")
        for name in sorted(filenames):
            if name.endswith(".lock"):
                continue
            if skip_ledger and name.startswith(_LEDGER_DB):
                continue
            found.append(Path(dirpath) / name)
    return found


def _component_roots(paths: Paths) -> dict[str, Path]:
    return {
        "runs": Path(paths.runs),
        "home": Path(paths.home),
        "deployments": Path(paths.deployments),
    }


def _ensure_owned_dir(path: Path, root: Path) -> None:
    """Create or explicitly re-mode a directory inside the restored HOP store.

    Unlike ``ensure_owner_dir`` (which refuses to touch a pre-existing insecure
    path), a restore is an explicit owner action on the store being restored, so
    the directory and every ancestor up to the owned root are set to 0700.
    """
    os.makedirs(path, mode=0o700, exist_ok=True)
    boundary = root.resolve()
    current = path
    while True:
        os.chmod(current, 0o700)
        if current.resolve() == boundary or current.parent == current:
            break
        current = current.parent


def _snapshot_sqlite(source: Path, destination: Path) -> None:
    """Consistent SQLite snapshot (no torn pages) via the online backup API."""
    ensure_owner_dir(destination.parent)
    if destination.exists():
        destination.unlink()
    source_conn = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        dest_conn = sqlite3.connect(str(destination))
        try:
            source_conn.backup(dest_conn)
            dest_conn.commit()
        finally:
            dest_conn.close()
    finally:
        source_conn.close()
    os.chmod(destination, 0o600)


def create_backup(
    destination: str,
    *,
    paths: Paths | None = None,
    force: bool = False,
    now: float | None = None,
) -> dict:
    """Write an atomic, self-verifying backup of the evidence chain."""
    paths = paths or current_paths()
    target = Path(destination).expanduser().resolve()
    if target.exists() and any(target.iterdir()):
        if not force:
            raise BackupError("destination_not_empty", f"{target} is not empty; pass force=True")
        shutil.rmtree(target)
    staging = target.parent / f"{target.name}.partial-{os.getpid()}"
    if staging.exists():
        shutil.rmtree(staging)
    ensure_owner_dir(staging)
    entries: list[dict] = []
    databases: list[dict] = []
    try:
        for component, root in _component_roots(paths).items():
            if root.is_file():
                rel = f"{component}/{root.name}"
                dest = staging / rel
                ensure_owner_dir(dest.parent)
                shutil.copyfile(root, dest)
                os.chmod(dest, 0o600)
                entries.append(_entry(rel, dest, component))
                continue
            for source in _rel_files(root, skip_ledger=component == "runs"):
                rel = f"{component}/{source.relative_to(root)}"
                dest = staging / rel
                ensure_owner_dir(dest.parent)
                shutil.copyfile(source, dest)
                os.chmod(dest, 0o600)
                entries.append(_entry(rel, dest, component))
        ledger = Path(paths.runs) / _LEDGER_DB
        if ledger.exists():
            rel = f"runs/{_LEDGER_DB}"
            dest = staging / rel
            _snapshot_sqlite(ledger, dest)
            entry = _entry(rel, dest, "runs")
            entry["snapshot"] = "sqlite-backup-api"
            entries.append(entry)
            databases.append({"path": rel, "digest": entry["sha256"], "bytes": entry["bytes"]})
        entries.sort(key=lambda item: item["path"])
        manifest = {
            "schema_version": BACKUP_SCHEMA_VERSION,
            "created_at": time.time() if now is None else now,
            "source": {
                "runs": str(paths.runs),
                "home": str(paths.home),
                "deployments": str(paths.deployments),
            },
            "databases": databases,
            "entries": entries,
        }
        manifest["set_digest"] = _set_digest(entries)
        _write_json(staging / MANIFEST_NAME, manifest)
        os.replace(staging, target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {
        "destination": str(target),
        "files": len(entries),
        "databases": databases,
        "set_digest": manifest["set_digest"],
        "manifest": str(target / MANIFEST_NAME),
    }


def _entry(rel: str, path: Path, component: str) -> dict:
    return {
        "path": rel,
        "component": component,
        "sha256": _sha256_file(path),
        "bytes": path.stat().st_size,
        "mode": "0600",
    }


def _set_digest(entries: list[dict]) -> str:
    canonical = json.dumps(
        [
            {"path": item["path"], "sha256": item["sha256"], "bytes": item["bytes"]}
            for item in entries
        ],
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def _write_json(path: Path, payload: object) -> None:
    ensure_owner_dir(path.parent)
    data = json.dumps(payload, indent=2, sort_keys=True).encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


def verify_backup(backup: str) -> dict:
    """Check the manifest, every listed digest, and the set digest."""
    root = Path(backup).expanduser().resolve()
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file():
        raise BackupError("backup_truncated", f"no {MANIFEST_NAME} in {root}")
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, ValueError) as exc:
        raise BackupError("backup_truncated", f"unreadable manifest: {exc}") from exc
    if manifest.get("schema_version") != BACKUP_SCHEMA_VERSION:
        raise BackupError("backup_schema", f"unsupported schema {manifest.get('schema_version')!r}")
    entries = manifest.get("entries")
    if not isinstance(entries, list):
        raise BackupError("backup_truncated", "manifest has no entries")
    missing: list[str] = []
    mismatched: list[str] = []
    for item in entries:
        rel = str(item.get("path", ""))
        path = root / rel
        if not path.is_file():
            missing.append(rel)
            continue
        observed = _sha256_file(path)
        if observed != item.get("sha256") or path.stat().st_size != item.get("bytes"):
            mismatched.append(rel)
    recorded_set = str(manifest.get("set_digest", ""))
    recomputed_set = _set_digest(entries)
    return {
        "backup": str(root),
        "ok": not missing and not mismatched and recorded_set == recomputed_set,
        "files": len(entries),
        "missing": sorted(missing),
        "mismatched": sorted(mismatched),
        "set_digest_ok": recorded_set == recomputed_set,
        "set_digest": recorded_set,
        "created_at": manifest.get("created_at"),
        "source": manifest.get("source", {}),
    }


def restore_backup(
    backup: str,
    *,
    paths: Paths | None = None,
    force: bool = False,
) -> dict:
    """Verify first, then restore into a clean HOP home and re-verify it."""
    report = verify_backup(backup)
    if not report["ok"]:
        raise BackupError(
            "backup_invalid",
            f"refusing to restore: missing={report['missing']} mismatched={report['mismatched']} "
            f"set_digest_ok={report['set_digest_ok']}",
        )
    root = Path(report["backup"])
    manifest = json.loads((root / MANIFEST_NAME).read_text())
    paths = paths or current_paths()
    runs = Path(paths.runs)
    if runs.exists() and any(runs.iterdir()) and not force:
        raise BackupError("target_not_empty", f"{runs} is not empty; pass force=True")
    restored: list[str] = []
    for item in manifest["entries"]:
        rel = str(item["path"])
        component, _, tail = rel.partition("/")
        base = {
            "runs": Path(paths.runs),
            "home": Path(paths.home),
            "deployments": Path(paths.deployments),
        }.get(component)
        if base is None:
            raise BackupError("backup_invalid", f"unknown component in entry {rel!r}")
        if component == "deployments" and not base.is_dir():
            destination = base
        else:
            destination = base / tail
        if component in ("runs", "home"):
            _ensure_owned_dir(destination.parent, base)
        else:
            # A pre-existing path outside the HOP home is never chmod-ed.
            destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            destination.unlink()
        shutil.copyfile(root / rel, destination)
        os.chmod(destination, 0o600)
        if _sha256_file(destination) != item["sha256"]:
            raise BackupError("restore_mismatch", f"restored {rel} does not match its digest")
        restored.append(rel)
    chain = verify_evidence_chain(paths)
    return {
        "backup": str(root),
        "runs": str(paths.runs),
        "home": str(paths.home),
        "restored": len(restored),
        "files": restored,
        "chain": chain,
    }


def verify_evidence_chain(paths: Paths | None = None) -> dict:
    """Resolve every run's artifact references; fence lost/partial attempts."""
    paths = paths or current_paths()
    runs = Path(paths.runs)
    ledger = runs / _LEDGER_DB
    result: dict = {
        "runs": 0,
        "artifacts_resolved": 0,
        "missing": [],
        "mismatched": [],
        "fenced": [],
    }
    if not ledger.is_file():
        # An empty (or not-yet-created) store is healthy, not an error: the CLI
        # and callers read ``ok`` directly.
        result["ok"] = True
        return result
    conn = sqlite3.connect(f"file:{ledger}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT run_id, record FROM runs").fetchall()
    finally:
        conn.close()
    blobs = runs / "artifacts" / "blobs"
    for run_id, record_json in rows:
        result["runs"] += 1
        try:
            record = json.loads(record_json)
        except ValueError:
            record = {}
        run_dir = runs / run_id
        attempts = record.get("attempts", []) if isinstance(record, dict) else []
        if not run_dir.is_dir():
            result["fenced"].append({"run_id": run_id, "reason": "run_dir_missing"})
        elif not (run_dir / "report.json").is_file():
            result["fenced"].append({"run_id": run_id, "reason": "report_missing"})
        for attempt in attempts if isinstance(attempts, list) else []:
            if isinstance(attempt, dict) and not attempt.get("outcome"):
                result["fenced"].append(
                    {
                        "run_id": run_id,
                        "reason": "attempt_without_outcome",
                        "attempt_id": attempt.get("attempt_id", ""),
                        "worker_id": attempt.get("worker_id", ""),
                    }
                )
        refs = runs / "artifacts" / "refs" / run_id
        for ref_path in _rel_files(refs):
            try:
                ref = json.loads(ref_path.read_text())
            except (OSError, ValueError):
                result["missing"].append(f"{run_id}:unreadable-ref:{ref_path.name}")
                continue
            digest = str(ref.get("digest", ""))
            hexpart = digest.split(":", 1)[-1]
            if len(hexpart) != 64:
                result["missing"].append(f"{run_id}:bad-digest:{digest!r}")
                continue
            blob = blobs / hexpart[:2] / hexpart
            if not blob.is_file():
                result["missing"].append(f"{run_id}:{digest}")
                continue
            if _sha256_file(blob) != hexpart:
                result["mismatched"].append(f"{run_id}:{digest}")
                continue
            result["artifacts_resolved"] += 1
    result["missing"].sort()
    result["mismatched"].sort()
    result["ok"] = not result["missing"] and not result["mismatched"]
    return result


def _print(payload: dict, fmt: str) -> None:
    if fmt == "json" or not sys.stdout.isatty():
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for key, value in payload.items():
            print(f"{key}: {value}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hop backup", description=__doc__)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    fmt = argparse.ArgumentParser(add_help=False)
    fmt.add_argument("--format", choices=("text", "json"), default="text")
    sub = parser.add_subparsers(dest="action", required=True)

    create = sub.add_parser("create", parents=[fmt], help="back up the evidence chain")
    create.add_argument("--destination", required=True)
    create.add_argument("--home", default="")
    create.add_argument("--runs", default="")
    create.add_argument("--force", action="store_true")

    verify = sub.add_parser("verify", parents=[fmt], help="verify a backup without restoring it")
    verify.add_argument("--backup", required=True)

    restore = sub.add_parser("restore", parents=[fmt], help="restore a verified backup")
    restore.add_argument("--backup", required=True)
    restore.add_argument("--home", default="")
    restore.add_argument("--runs", default="")
    restore.add_argument("--force", action="store_true")

    chain = sub.add_parser("chain", parents=[fmt], help="report the evidence chain of a HOP home")
    chain.add_argument("--home", default="")
    chain.add_argument("--runs", default="")

    args = parser.parse_args(argv)
    try:
        if args.action == "create":
            paths = _paths(args.home, args.runs)
            result = create_backup(args.destination, paths=paths, force=args.force)
        elif args.action == "verify":
            result = verify_backup(args.backup)
            if not result["ok"]:
                _print(result, args.format)
                return 3
        elif args.action == "restore":
            paths = _paths(args.home, args.runs)
            result = restore_backup(args.backup, paths=paths, force=args.force)
            if not result["chain"]["ok"]:
                _print(result, args.format)
                return 3
        else:
            result = verify_evidence_chain(_paths(args.home, args.runs))
            if not result["ok"]:
                _print(result, args.format)
                return 3
    except BackupError as exc:
        _print({"ok": False, "code": exc.code, "error": str(exc)}, args.format)
        return 2
    _print(result, args.format)
    return 0


def _paths(home: str, runs: str) -> Paths:
    return Paths.resolve(home=home or None, runs=runs or None)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
