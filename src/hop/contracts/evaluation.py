"""M4 evaluation contracts: local provenance, case packages, and case groups.

Frozen interface for AOP-020 half A (WP-2.1a). Packs consume these records and
**must treat** ``CasePackage.hidden_ref`` as an opaque token: never resolve sealed
paths, open a sealed store, or infer a holdout location from it. Sealed storage,
the holdout access ledger, and split assignment are owned by AOP-020 half B
(WP-2.1b) and are deliberately absent from this module.

Determinism: every identifier here is derived from content (repository identity,
commit shas, patch digests) so re-importing the same local snapshot yields
byte-identical records. No wall-clock value is embedded in a written package.

Tightening (WP-2.1f, A-5..A-9): list items are patterned rather than free
strings, ``hidden_ref`` is restricted to its three legitimate forms, and the
repository-manifest digest covers every entry field. These are stricter
validations only; existing valid records remain readable.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .base import AopBase

# Canonical shapes referenced by specs/case-package.schema.json and
# specs/source-provenance.schema.json.
SHA256_PATTERN = r"^sha256:[0-9a-f]{64}$"
GIT_SHA_PATTERN = r"^[0-9a-f]{40}([0-9a-f]{24})?$"
GROUP_ID_PATTERN = r"^group-[0-9a-f]{16}$"
CASE_ID_PATTERN = r"^case-[0-9a-f]{16}$"
#: hidden_ref is opaque, but only three forms are legitimate: the unassigned
#: in-memory placeholder, half A's deterministic non-secret token, and half B's
#: sealed-store token. Anything else is a contract violation.
HIDDEN_REF_PATTERN = r"^(unassigned|sealed-unassigned:case-[0-9a-f]{16}|[0-9a-f]{32})$"

Sha256Item = Annotated[str, StringConstraints(pattern=SHA256_PATTERN)]
GitShaItem = Annotated[str, StringConstraints(pattern=GIT_SHA_PATTERN)]

#: Required file names in every case package written by the importer.
CASE_PACKAGE_FILES = (
    "task.yaml",
    "prompt.md",
    "repository.manifest.json",
    "environment.lock.json",
    "source-provenance.json",
)
CASE_PACKAGE_DIRS = ("public-fixtures", "visible-tests")


class SourceProvenance(AopBase):
    """Where a case package came from, subject to validation on import.

    ``patch_digest`` is the sha256 of the normalized patch for the primary
    commit; ``ancestry`` is the primary commit followed by its first-parent
    history (nearest first). Timestamps are the commit's own timestamps in UTC.
    """

    kind: str = Field(default="SourceProvenance", frozen=True)
    case_id: str = Field(pattern=CASE_ID_PATTERN)
    origin: str = Field(min_length=1, description='e.g. "local-git"')
    repository: str = Field(pattern=SHA256_PATTERN)
    commit_sha: str = Field(pattern=GIT_SHA_PATTERN)
    ancestry: list[GitShaItem] = Field(default_factory=list)
    patch_digest: str = Field(pattern=SHA256_PATTERN)
    changed_files: list[str] = Field(default_factory=list)
    authored_at: str = Field(default="", description="commit author time, UTC ISO-8601")
    committed_at: str = Field(default="", description="commit time, UTC ISO-8601")
    baseline: str = Field(default="", description="free-text description of the defect")
    generator: str = Field(default="hop.evaluation/0.1")


class RepositoryEntry(BaseModel):
    """One tracked file at the imported commit."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    blob_sha: str = Field(pattern=GIT_SHA_PATTERN)
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=SHA256_PATTERN)


class RepositoryManifest(AopBase):
    """Content-addressed inventory of the imported snapshot."""

    kind: str = Field(default="RepositoryManifest", frozen=True)
    repository: str = Field(pattern=SHA256_PATTERN)
    commit_sha: str = Field(pattern=GIT_SHA_PATTERN)
    entries: list[RepositoryEntry] = Field(default_factory=list)
    file_count: int = Field(default=0, ge=0)
    digest: str = Field(pattern=SHA256_PATTERN)


class EnvironmentLock(AopBase):
    """Recorded interpreter/platform envelope; no installs happen during import."""

    kind: str = Field(default="EnvironmentLock", frozen=True)
    python: str = Field(min_length=1)
    platform: str = Field(default="")
    dependencies: list[str] = Field(default_factory=list)
    note: str = Field(default="")


class CaseGroup(AopBase):
    """One data group. Related commits stay together and are never re-split."""

    kind: str = Field(default="CaseGroup", frozen=True)
    group_id: str = Field(pattern=GROUP_ID_PATTERN)
    repository: str = Field(pattern=SHA256_PATTERN)
    commit_shas: list[GitShaItem] = Field(min_length=1)
    patch_digests: list[Sha256Item] = Field(min_length=1)
    created_at: str = Field(default="", description="earliest commit time, UTC ISO-8601")


class CasePackage(AopBase):
    """A materialized case package on disk.

    ``hidden_ref`` is an opaque token. Half A records a deterministic
    unassigned token; half B's sealed store replaces it with the token that
    resolves the sealed holdout. Packs must never dereference it.
    """

    kind: str = Field(default="CasePackage", frozen=True)
    case_id: str = Field(pattern=CASE_ID_PATTERN)
    group_id: str = Field(pattern=GROUP_ID_PATTERN)
    root: str = Field(min_length=1)
    task_path: str = Field(min_length=1)
    public_tests: list[str] = Field(default_factory=list)
    hidden_ref: str = Field(pattern=HIDDEN_REF_PATTERN)
    visible_tests: list[str] = Field(default_factory=list)
    provenance_path: str = Field(default="")
    repository_manifest_path: str = Field(default="")
    environment_lock_path: str = Field(default="")
    content_digest: str = Field(default="", pattern=SHA256_PATTERN)
    created_at: str = Field(default="")

    @model_validator(mode="after")
    def _require_digest_for_assigned_packages(self) -> CasePackage:
        """A package with an assigned hidden_ref must carry its content digest.

        ``hidden_ref == "unassigned"`` is the explicitly incomplete in-memory
        placeholder used before a package is imported or sealed; every real
        package (importer output or a loaded on-disk package) must carry a
        patterned ``content_digest`` so tamper checks have a trusted value.
        """
        if self.hidden_ref != "unassigned" and not self.content_digest:
            raise ValueError(
                "content_digest is required for a case package with an assigned hidden_ref"
            )
        return self
