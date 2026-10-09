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
import time
from dataclasses import dataclass, field

from ..ledger import AccessLedger
from ..splits import (
    Split,
    default_sealed_root,
    validate_sealed_root,
)

TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
SEALED_READ_ROLES = frozenset({"verifier"})
DEFAULT_REPEAT_THRESHOLD = 1


class SealedAccessDenied(PermissionError):
    """A non-owner actor attempted to read sealed material."""


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
    schema_version: int = 1


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _token_for(case_id: str, salt: str = "") -> str:
    return hashlib.sha256(f"sealed:{salt}:{case_id}".encode()).hexdigest()[:32]


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
        """Seal evaluator material; returns the opaque hidden_ref token."""
        if split != Split.SEALED.value:
            raise ValueError("only sealed-evaluator cases may enter the sealed store")
        if not files:
            raise ValueError("refusing to seal an empty case")
        token = self.token_for(case_id) if self.salt else _token_for(case_id)
        case_dir = self.path_for(token)
        if os.path.isdir(case_dir):
            shutil.rmtree(case_dir)
        files_dir = os.path.join(case_dir, "files")
        os.makedirs(files_dir, mode=0o700)
        manifest_files: dict[str, str] = {}
        for rel, data in sorted(files.items()):
            if rel.startswith("/") or ".." in rel.split("/"):
                raise ValueError(f"refusing unsafe sealed path: {rel!r}")
            destination = os.path.join(files_dir, rel)
            os.makedirs(os.path.dirname(destination) or files_dir, mode=0o700, exist_ok=True)
            fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                os.write(fd, data)
                os.fsync(fd)
            finally:
                os.close(fd)
            manifest_files[rel] = _sha256(data)
        manifest = SealedManifest(
            token=token,
            case_id=case_id,
            split=split,
            created_at=round(time.time(), 6),
            files=manifest_files,
        )
        payload = {
            "schema_version": manifest.schema_version,
            "token": manifest.token,
            "case_id": manifest.case_id,
            "split": manifest.split,
            "created_at": manifest.created_at,
            "files": manifest.files,
        }
        manifest_bytes = json.dumps(payload, sort_keys=True, indent=2).encode("utf-8")
        manifest_digest = _sha256(manifest_bytes)
        manifest_path = os.path.join(case_dir, "manifest.json")
        fd = os.open(manifest_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, manifest_bytes)
            os.fsync(fd)
        finally:
            os.close(fd)
        self.ledger.append(
            case_id=case_id,
            token=token,
            actor=actor,
            role="importer",
            purpose=purpose,
            outcome="granted",
            content_digest=manifest_digest,
            event="seal",
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
        path = os.path.join(self.path_for(token), "manifest.json")
        if not os.path.isfile(path):
            raise SealedCaseNotFound(token)
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

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
            self.ledger.append(
                case_id="",
                token=token,
                actor=actor,
                role=role,
                purpose=purpose,
                run_id=run_id,
                outcome="denied",
                event="read",
            )
            raise SealedCaseNotFound(token)
        manifest = self.manifest(token)
        case_id = str(manifest.get("case_id", ""))
        if role not in SEALED_READ_ROLES:
            self.ledger.append(
                case_id=case_id,
                token=token,
                actor=actor,
                role=role,
                purpose=purpose,
                run_id=run_id,
                outcome="denied",
                event="read",
            )
            raise SealedAccessDenied(f"role {role!r} may not read sealed case {case_id!r}")
        files_dir = os.path.join(path, "files")
        material: dict[str, bytes] = {}
        for rel in sorted(manifest.get("files", {})):
            full = os.path.join(files_dir, rel)
            with open(full, "rb") as fh:
                material[rel] = fh.read()
        digest = _sha256(
            b"".join(rel.encode("utf-8") + b"\0" + material[rel] for rel in sorted(material))
        )
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
        return material

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
