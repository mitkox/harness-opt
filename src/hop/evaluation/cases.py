"""Deterministic case-package writer/reader (AOP-020 half A).

Package layout (must match ``specs/case-package.schema.json``)::

    <case_id>/task.yaml
    <case_id>/prompt.md
    <case_id>/repository.manifest.json
    <case_id>/environment.lock.json
    <case_id>/source-provenance.json
    <case_id>/public-fixtures/...
    <case_id>/visible-tests/...

Writes are fully sorted and timestamp-free, so importing the same snapshot twice
produces byte-identical packages. ``hidden_ref`` is copied through verbatim as an
opaque token; nothing here resolves or creates sealed storage.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import yaml
from pydantic import ValidationError

from hop.contracts.evaluation import (
    CASE_PACKAGE_DIRS,
    CASE_PACKAGE_FILES,
    CasePackage,
    EnvironmentLock,
    RepositoryManifest,
    SourceProvenance,
)

from .provenance import dump_source_provenance, load_source_provenance


class CaseImportError(ValueError):
    """A case package is missing, incomplete, or internally inconsistent."""


#: Half B's sealed-store tokens are 32 hex characters. They are attached in
#: memory and must never be persisted into a public package file.
_SEALED_TOKEN = re.compile(r"^[0-9a-f]{32}$")


def _write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _write_json(path: Path, payload: dict) -> None:
    _write_bytes(path, (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode())


def _write_yaml(path: Path, payload: dict) -> None:
    text = yaml.safe_dump(payload, sort_keys=True, default_flow_style=False, allow_unicode=True)
    _write_bytes(path, text.encode())


def _write_text(path: Path, text: str) -> None:
    if text and not text.endswith("\n"):
        text += "\n"
    _write_bytes(path, text.encode())


def package_content_digest(root: str | Path) -> str:
    """sha256 over sorted ``relpath\\0content\\0`` pairs of a package directory."""
    base = Path(root)
    if not base.is_dir():
        raise CaseImportError(f"case package directory does not exist: {base}")
    digest = hashlib.sha256()
    for path in sorted((p for p in base.rglob("*") if p.is_file()), key=lambda p: p.as_posix()):
        rel = path.relative_to(base).as_posix()
        digest.update(rel.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def write_case_package(
    root: str | Path,
    *,
    case_id: str,
    group_id: str,
    task: dict,
    prompt: str,
    repository_manifest: RepositoryManifest,
    environment_lock: EnvironmentLock,
    provenance: SourceProvenance,
    hidden_ref: str,
    visible_tests: dict[str, str] | None = None,
    public_fixtures: dict[str, str] | None = None,
    created_at: str = "",
) -> CasePackage:
    """Materialize a case package and return its validated record."""
    if provenance.case_id != case_id:
        raise CaseImportError(
            f"provenance case_id {provenance.case_id!r} does not match package {case_id!r}"
        )
    if repository_manifest.commit_sha != provenance.commit_sha:
        raise CaseImportError("repository manifest and provenance disagree on the commit")
    if not hidden_ref:
        raise CaseImportError("hidden_ref must be a non-empty opaque token")
    base = Path(root)
    base.mkdir(parents=True, exist_ok=True)
    for directory in CASE_PACKAGE_DIRS:
        (base / directory).mkdir(exist_ok=True)

    _write_yaml(base / "task.yaml", task)
    _write_text(base / "prompt.md", prompt)
    _write_json(base / "repository.manifest.json", repository_manifest.model_dump(mode="json"))
    _write_json(base / "environment.lock.json", environment_lock.model_dump(mode="json"))
    _write_text(base / "source-provenance.json", dump_source_provenance(provenance))

    visible = dict(visible_tests or {})
    fixtures = dict(public_fixtures or {})
    for rel, content in sorted(visible.items()):
        _write_text(base / "visible-tests" / rel, content)
    for rel, content in sorted(fixtures.items()):
        _write_text(base / "public-fixtures" / rel, content)

    return CasePackage(
        case_id=case_id,
        group_id=group_id,
        root=base.name,
        task_path="task.yaml",
        public_tests=sorted(visible),
        hidden_ref=hidden_ref,
        visible_tests=sorted(visible),
        provenance_path="source-provenance.json",
        repository_manifest_path="repository.manifest.json",
        environment_lock_path="environment.lock.json",
        content_digest=package_content_digest(base),
        created_at=created_at,
    )


def verify_repository_manifest(manifest: RepositoryManifest) -> str:
    """Recompute the manifest digest from entries and compare it to ``digest``.

    Covers ``path``, ``blob_sha`` **and** ``sha256`` so an altered entry digest
    is rejected even when the recorded ``digest`` field is left untouched.
    """
    digest = hashlib.sha256()
    for entry in manifest.entries:
        digest.update(entry.path.encode())
        digest.update(b"\0")
        digest.update(entry.blob_sha.encode())
        digest.update(b"\0")
        digest.update(entry.sha256.encode())
        digest.update(b"\0")
    computed = "sha256:" + digest.hexdigest()
    if computed != manifest.digest:
        raise CaseImportError("repository manifest digest mismatch: entries were altered")
    return computed


def verify_case_package(root: str | Path, recorded_digest: str) -> str:
    """Recompute a package digest and compare it to a trusted recorded digest.

    This is the tamper check: ``load_case_package`` returns the digest of the
    tree it actually read, so callers that keep the recorded digest (a trusted
    registry, ledger, or run record) must use this helper to detect edits.
    """
    if not recorded_digest:
        raise CaseImportError("a recorded content digest is required for verification")
    digest = package_content_digest(root)
    if digest != recorded_digest:
        raise CaseImportError(
            f"case package content digest mismatch: expected {recorded_digest}, got {digest}"
        )
    return digest


def load_case_package(root: str | Path, *, expected_digest: str = "") -> CasePackage:
    """Validate an on-disk package and return its record.

    Structural problems raise :class:`CaseImportError`; provenance problems
    raise :class:`hop.evaluation.provenance.ProvenanceError`. Nothing is
    rewritten by a read.
    """
    base = Path(root)
    if not base.is_dir():
        raise CaseImportError(f"case package directory does not exist: {base}")
    for name in CASE_PACKAGE_FILES:
        candidate = base / name
        if not candidate.is_file():
            raise CaseImportError(f"case package is missing required file: {name}")
    for directory in CASE_PACKAGE_DIRS:
        if not (base / directory).is_dir():
            raise CaseImportError(f"case package is missing required directory: {directory}")

    try:
        task = yaml.safe_load((base / "task.yaml").read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise CaseImportError(f"task.yaml is unreadable: {exc}") from exc
    if not isinstance(task, dict):
        raise CaseImportError("task.yaml must describe a mapping")
    # A sealed token is attached in memory (with_hidden_ref); it is never
    # persisted into a public package file.
    task_hidden_ref = str(task.get("hidden_ref", ""))
    if not task_hidden_ref:
        raise CaseImportError("task.yaml is missing hidden_ref")
    if _SEALED_TOKEN.fullmatch(task_hidden_ref):
        raise CaseImportError(
            "task.yaml must carry only the non-secret hidden_ref placeholder; "
            "sealed tokens are never persisted in package files"
        )

    try:
        manifest = RepositoryManifest.model_validate_json(
            (base / "repository.manifest.json").read_text()
        )
        EnvironmentLock.model_validate_json((base / "environment.lock.json").read_text())
    except (OSError, ValidationError) as exc:
        raise CaseImportError(f"package manifest/environment invalid: {exc}") from exc
    verify_repository_manifest(manifest)

    provenance = load_source_provenance(base / "source-provenance.json")
    if provenance.commit_sha != manifest.commit_sha:
        raise CaseImportError("repository manifest and provenance disagree on the commit")

    visible = sorted(
        p.relative_to(base / "visible-tests").as_posix()
        for p in (base / "visible-tests").rglob("*")
        if p.is_file()
    )
    digest = package_content_digest(base)
    if expected_digest and digest != expected_digest:
        raise CaseImportError(
            f"case package content digest mismatch: expected {expected_digest}, got {digest}"
        )
    return CasePackage(
        case_id=provenance.case_id,
        group_id=str(task.get("group_id", "")),
        root=base.name,
        task_path="task.yaml",
        public_tests=visible,
        hidden_ref=task_hidden_ref,
        visible_tests=visible,
        provenance_path="source-provenance.json",
        repository_manifest_path="repository.manifest.json",
        environment_lock_path="environment.lock.json",
        content_digest=digest,
        created_at=str(task.get("created_at", "")),
    )
