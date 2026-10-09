"""Sealed evaluator store: owner-read only, ledgered, outside worker reach.

Layout (all under a root validated by :func:`hop.evaluation.splits.validate_sealed_root`)::

    <root>/cases/<token>/manifest.json
    <root>/cases/<token>/files/<relative path>
    <root>/ledger.jsonl

The ``token`` is the opaque ``hidden_ref`` a :class:`CasePackage` carries: packs
never resolve sealed paths themselves, they hand the token back to the store.
Directories are ``0700`` and files ``0600``; a read by any role other than
``verifier`` is refused and recorded as a denied ledger entry. Every granted
read records a content digest, so repeated holdout exposure is detectable and a
run that exposed a sealed case is ineligible for a promotion-eligible claim.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import secrets
import shutil
import tempfile
import time
from dataclasses import dataclass, field

from ..ledger import AccessLedger
from ..splits import (
    Split,
    default_sealed_root,
    validate_sealed_root,
)

TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
# Mirrors hop.contracts.evaluation.CASE_ID_PATTERN; kept local so this module
# imports in a checkout where the half-A contract is not present.
CASE_ID_RE = re.compile(r"^case-[0-9a-f]{16}$")
SEALED_READ_ROLES = frozenset({"verifier"})
DEFAULT_REPEAT_THRESHOLD = 1


class SealedAccessDenied(PermissionError):
    """A non-owner actor attempted to read sealed material."""


class SealedIntegrityError(RuntimeError):
    """Sealed material does not match its manifest (post-seal tampering)."""


class SealedCaseNotFound(KeyError):
    """The opaque token does not name a sealed case."""


@dataclass
class SealedRef:
    """Opaque handle a CasePackage may carry; it resolves nothing by itself."""

    token: str
    case_id: str
    split: str = Split.SEALED.value
    manifest_digest: str = ""
    files: int = 0
    path: str = ""

    def as_dict(self) -> dict:
        return {
            "token": self.token,
            "case_id": self.case_id,
            "split": self.split,
            "manifest_digest": self.manifest_digest,
            "files": self.files,
            "path": self.path,
        }


@dataclass
class SealedManifest:
    token: str
    case_id: str
    split: str
    created_at: float
    files: dict = field(default_factory=dict)
    manifest_digest: str = ""
    schema_version: int = 1


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _token_for(case_id: str, salt: str = "") -> str:
    return hashlib.sha256(f"sealed:{salt}:{case_id}".encode()).hexdigest()[:32]


def _manifest_core(payload: dict) -> dict:
    """Canonical manifest content covered by ``manifest_digest``."""
    return {
        "schema_version": int(payload.get("schema_version", 1)),
        "token": str(payload.get("token", "")),
        "case_id": str(payload.get("case_id", "")),
        "split": str(payload.get("split", "")),
        "created_at": float(payload.get("created_at", 0.0)),
        "files": dict(payload.get("files", {})),
    }


def _manifest_digest(core: dict) -> str:
    return _sha256(json.dumps(core, sort_keys=True, separators=(",", ":")).encode())


def with_hidden_ref(package, token: str, *, split: str = Split.SEALED.value):
    """Return a copy of a CasePackage whose opaque ``hidden_ref`` is ``token``.

    Only sealed-evaluator packages may point at the sealed store; the contract
    (``hop.contracts.evaluation``) requires the field to stay opaque, so this is
    the only supported way to attach a sealed token to a package.
    """
    if split != Split.SEALED.value:
        raise ValueError("only sealed-evaluator packages may carry a sealed hidden_ref")
    if not TOKEN_RE.fullmatch(token):
        raise ValueError(f"invalid sealed token: {token!r}")
    if isinstance(package, dict):
        return {**package, "hidden_ref": token}
    model_copy = getattr(package, "model_copy", None)
    if callable(model_copy):
        return model_copy(update={"hidden_ref": token})
    if dataclasses.is_dataclass(package) and not isinstance(package, type):
        return dataclasses.replace(package, hidden_ref=token)
    raise TypeError(f"cannot attach hidden_ref to {type(package).__name__}")


class SealedStore:
    def __init__(
        self,
        root: str | None = None,
        *,
        ledger: AccessLedger | None = None,
        repository_root: str | None = None,
        worker_root: str | None = None,
        repeat_threshold: int = DEFAULT_REPEAT_THRESHOLD,
        salt: str = "",
    ):
        self.root = validate_sealed_root(
            root or default_sealed_root(),
            repository_root=repository_root,
            worker_root=worker_root,
        )
        os.makedirs(self.root, mode=0o700, exist_ok=True)
        os.chmod(self.root, 0o700)
        self.cases_root = os.path.join(self.root, "cases")
        os.makedirs(self.cases_root, mode=0o700, exist_ok=True)
        if ledger is not None and os.path.realpath(ledger.path) != os.path.realpath(
            os.path.join(self.root, "ledger.jsonl")
        ):
            raise ValueError("ledger must live inside the sealed root")
        self.ledger = ledger or AccessLedger(
            os.path.join(self.root, "ledger.jsonl"), repeat_threshold=repeat_threshold
        )
        self.salt = salt

    # -- addressing --------------------------------------------------------
    def token_for(self, case_id: str) -> str:
        return _token_for(case_id, self.salt)

    def path_for(self, token: str) -> str:
        if not TOKEN_RE.fullmatch(token):
            raise SealedCaseNotFound(f"invalid sealed token: {token!r}")
        path = os.path.join(self.cases_root, token)
        if not os.path.realpath(path).startswith(os.path.realpath(self.cases_root) + os.sep):
            raise SealedCaseNotFound("sealed token escapes the store")
        return path

    def exists(self, token: str) -> bool:
        try:
            return os.path.isfile(os.path.join(self.path_for(token), "manifest.json"))
        except SealedCaseNotFound:
            return False

    # -- writer ------------------------------------------------------------
    def seal(
        self,
        case_id: str,
        files: dict[str, bytes],
        *,
        split: str = Split.SEALED.value,
        actor: str = "importer",
        purpose: str = "seal",
    ) -> SealedRef:
        """Seal evaluator material; returns the opaque hidden_ref token.

        The case is staged in a temporary directory and swapped in with
        ``os.replace`` so a crash can never leave a half-written case, and an
        existing case supersede is recorded as a ``seal-replaced`` event.
        """
        if split != Split.SEALED.value:
            raise ValueError("only sealed-evaluator cases may enter the sealed store")
        if not CASE_ID_RE.fullmatch(case_id):
            raise ValueError(f"invalid case_id for the sealed store: {case_id!r}")
        if not files:
            raise ValueError("refusing to seal an empty case")
        for rel in files:
            if rel.startswith("/") or ".." in rel.split("/"):
                raise ValueError(f"refusing unsafe sealed path: {rel!r}")
        token = self.token_for(case_id) if self.salt else _token_for(case_id)
        case_dir = self.path_for(token)
        replaced = os.path.isdir(case_dir)
        stage = tempfile.mkdtemp(dir=self.cases_root, prefix=".stage-")
        try:
            files_dir = os.path.join(stage, "files")
            os.makedirs(files_dir, mode=0o700)
            manifest_files: dict[str, str] = {}
            for rel, data in sorted(files.items()):
                destination = os.path.join(files_dir, rel)
                os.makedirs(os.path.dirname(destination) or files_dir, mode=0o700, exist_ok=True)
                fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                try:
                    os.write(fd, data)
                    os.fsync(fd)
                finally:
                    os.close(fd)
                manifest_files[rel] = _sha256(data)
            core = {
                "schema_version": 1,
                "token": token,
                "case_id": case_id,
                "split": split,
                "created_at": round(time.time(), 6),
                "files": manifest_files,
            }
            manifest_digest = _manifest_digest(core)
            payload = {**core, "manifest_digest": manifest_digest}
            manifest_bytes = json.dumps(payload, sort_keys=True, indent=2).encode("utf-8")
            manifest_path = os.path.join(stage, "manifest.json")
            fd = os.open(manifest_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                os.write(fd, manifest_bytes)
                os.fsync(fd)
            finally:
                os.close(fd)
            backup = ""
            if replaced:
                backup = f"{case_dir}.old-{os.getpid()}"
                if os.path.isdir(backup):
                    shutil.rmtree(backup)
                os.replace(case_dir, backup)
            try:
                os.replace(stage, case_dir)
            except OSError:
                if backup and not os.path.isdir(case_dir):
                    os.replace(backup, case_dir)
                raise
            if backup:
                shutil.rmtree(backup, ignore_errors=True)
            stage = ""
        finally:
            if stage and os.path.isdir(stage):
                shutil.rmtree(stage, ignore_errors=True)
        self.ledger.append(
            case_id=case_id,
            token=token,
            actor=actor,
            role="importer",
            purpose=purpose,
            outcome="granted",
            content_digest=manifest_digest,
            event="seal-replaced" if replaced else "seal",
        )
        return SealedRef(
            token=token,
            case_id=case_id,
            split=split,
            manifest_digest=manifest_digest,
            files=len(manifest_files),
            path=case_dir,
        )

    def seal_tree(
        self,
        case_id: str,
        source_dir: str,
        *,
        split: str = Split.SEALED.value,
        actor: str = "importer",
        purpose: str = "seal",
    ) -> SealedRef:
        files: dict[str, bytes] = {}
        for dirpath, _dirnames, filenames in os.walk(source_dir):
            for name in sorted(filenames):
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, source_dir)
                with open(full, "rb") as fh:
                    files[rel] = fh.read()
        return self.seal(case_id, files, split=split, actor=actor, purpose=purpose)

    # -- reader ------------------------------------------------------------
    def manifest(self, token: str) -> dict:
        """Read and integrity-check the manifest (A-3)."""
        path = os.path.join(self.path_for(token), "manifest.json")
        if not os.path.isfile(path):
            raise SealedCaseNotFound(token)
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
        recorded = str(payload.get("manifest_digest", ""))
        recomputed = _manifest_digest(_manifest_core(payload))
        if recorded != recomputed:
            raise SealedIntegrityError(
                f"sealed manifest for {payload.get('case_id', '')!r} is tampered"
            )
        return payload

    def verify_material(self, token: str) -> dict:
        """Compare every sealed file (and the manifest) against its digest."""
        path = self.path_for(token)
        manifest = self.manifest(token)
        files_dir = os.path.join(path, "files")
        expected = {
            str(rel): str(digest) for rel, digest in dict(manifest.get("files", {})).items()
        }
        observed: dict[str, str] = {}
        for dirpath, _dirnames, filenames in os.walk(files_dir):
            for name in filenames:
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, files_dir).replace(os.sep, "/")
                with open(full, "rb") as fh:
                    observed[rel] = _sha256(fh.read())
        problems: list[str] = []
        for rel, digest in sorted(expected.items()):
            if rel not in observed:
                problems.append(f"missing:{rel}")
            elif observed[rel] != digest:
                problems.append(f"mismatch:{rel}")
        for rel in sorted(set(observed) - set(expected)):
            problems.append(f"unexpected:{rel}")
        return {
            "case_id": str(manifest.get("case_id", "")),
            "ok": not problems,
            "problems": problems,
            "files": len(expected),
        }

    def read(
        self,
        token: str,
        *,
        actor: str,
        role: str,
        purpose: str,
        run_id: str = "",
        split: str = Split.SEALED.value,
    ) -> dict[str, bytes]:
        """Owner-only read; every attempt is ledgered before material returns."""
        path = self.path_for(token)
        manifest_path = os.path.join(path, "manifest.json")
        if not os.path.isfile(manifest_path):
            self._deny(token, actor, role, purpose, run_id, "missing_case")
            raise SealedCaseNotFound(token)
        try:
            manifest = self.manifest(token)
        except SealedIntegrityError:
            self._deny(token, actor, role, purpose, run_id, "manifest_tampered")
            raise
        case_id = str(manifest.get("case_id", ""))
        if role not in SEALED_READ_ROLES:
            self._deny(token, actor, role, purpose, run_id, "role_refused", case_id=case_id)
            raise SealedAccessDenied(f"role {role!r} may not read sealed case {case_id!r}")
        integrity = self.verify_material(token)
        if not integrity["ok"]:
            self._deny(
                token,
                actor,
                role,
                purpose,
                run_id,
                "material_tampered",
                case_id=case_id,
                detail=",".join(integrity["problems"])[:200],
            )
            raise SealedIntegrityError(
                f"sealed case {case_id!r} failed integrity verification: "
                + ",".join(integrity["problems"])
            )
        files_dir = os.path.join(path, "files")
        material: dict[str, bytes] = {}
        for rel in sorted(manifest.get("files", {})):
            full = os.path.join(files_dir, rel)
            with open(full, "rb") as fh:
                material[rel] = fh.read()
        digest = _sha256(
            b"".join(rel.encode("utf-8") + b"\0" + material[rel] for rel in sorted(material))
        )
        try:
            self.ledger.append(
                case_id=case_id,
                token=token,
                actor=actor,
                role=role,
                purpose=purpose,
                run_id=run_id,
                outcome="granted",
                content_digest=digest,
                event="read",
            )
        except OSError as exc:
            # Fail closed: material is never returned without a durable record.
            raise SealedAccessDenied(
                f"cannot record a sealed read for {case_id!r}; refusing unrecorded access"
            ) from exc
        return material

    def _deny(
        self,
        token: str,
        actor: str,
        role: str,
        purpose: str,
        run_id: str,
        reason: str,
        *,
        case_id: str = "",
        detail: str = "",
    ) -> None:
        try:
            self.ledger.append(
                case_id=case_id,
                token=token,
                actor=actor,
                role=role,
                purpose=purpose,
                run_id=run_id,
                outcome="denied",
                reason=f"{reason}:{detail}" if detail else reason,
                event="read",
            )
        except OSError:
            pass

    # -- exposure policy ---------------------------------------------------
    def exposure(self) -> dict:
        report = self.ledger.exposure()
        result = report.as_dict()
        result["root"] = self.root
        return result

    def repeated_exposure(self) -> list[str]:
        return self.ledger.exposure().repeated

    def promotion_eligible(self, run_id: str) -> bool:
        return self.ledger.promotion_eligible(run_id)

    def new_token(self) -> str:
        """Fresh opaque token (used only where determinism is not required)."""
        return secrets.token_hex(16)
