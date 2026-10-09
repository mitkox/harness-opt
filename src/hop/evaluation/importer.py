"""Import a LOCAL git snapshot into deterministic case packages (AOP-020 half A).

No network is used: every fact comes from ``git`` commands against the supplied
path, with a pinned environment (``LC_ALL=C``, ``TZ=UTC``, ``GIT_CONFIG_NOSYSTEM``)
so output does not depend on the operator's git configuration. Identifiers are
content hashes and written files contain no wall-clock values, so re-importing
the same snapshot yields byte-identical packages.

``hidden_ref`` is emitted as an opaque, deterministic, *unassigned* token. This
module never creates or resolves sealed storage; that is AOP-020 half B.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from hop.contracts.evaluation import (
    CaseGroup,
    CasePackage,
    EnvironmentLock,
    RepositoryEntry,
    RepositoryManifest,
    SourceProvenance,
)

from . import cases
from .grouping import DEFAULT_WINDOW_MINUTES, CommitFact, group_commits, issue_reference
from .provenance import changed_files, patch_digest

GENERATOR = "hop.evaluation/0.1"

_TEST_PATH = re.compile(r"(^|/)(tests?)/|(^|/)test_[^/]*\.py$|(^|/)[^/]*_test\.py$")
_FIXTURE_TOP = {"fixtures", "public-fixtures"}


class SnapshotImportError(ValueError):
    """The local snapshot cannot be imported (not a repo, bad revision, ...)."""


@dataclass(frozen=True)
class ImportResult:
    """Deterministic result of one snapshot import."""

    out_root: str
    groups: list[CaseGroup]
    packages: list[CasePackage]


def _git_env() -> dict[str, str]:
    return {
        **os.environ,
        "LC_ALL": "C",
        "TZ": "UTC",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_OPTIONAL_LOCKS": "0",
    }


def _git(repo: str | Path, *args: str, required: bool = True) -> str:
    proc = subprocess.run(
        ["git", "-c", "core.quotepath=false", "-C", str(repo), *args],
        env=_git_env(),
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        if not required:
            return ""
        detail = proc.stderr.decode("utf-8", errors="replace").strip()[:400]
        raise SnapshotImportError(f"git {' '.join(args)} failed in {repo}: {detail}")
    return proc.stdout.decode("utf-8", errors="replace")


def _git_bytes(repo: str | Path, *args: str) -> bytes:
    proc = subprocess.run(
        ["git", "-c", "core.quotepath=false", "-C", str(repo), *args],
        env=_git_env(),
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise SnapshotImportError(f"git {' '.join(args)} failed in {repo}")
    return proc.stdout


def _require_repository(repo: str | Path) -> None:
    if not Path(repo).is_dir():
        raise SnapshotImportError(f"snapshot path is not a directory: {repo}")
    inside = _git(repo, "rev-parse", "--is-inside-work-tree", required=False).strip()
    if inside != "true":
        raise SnapshotImportError(f"not a local git work tree: {repo}")


def repository_identity(repo: str | Path) -> str:
    """Content identity of a repository: root commits plus the recorded remote URL."""
    roots = sorted(_git(repo, "rev-list", "--max-parents=0", "--all").split())
    if not roots:
        raise SnapshotImportError(f"repository has no commits: {repo}")
    remote = _git(repo, "config", "--get", "remote.origin.url", required=False).strip()
    payload = "\0".join(roots) + "\0" + remote
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()


def _commit_shas(repo: str | Path, revisions: list[str] | None) -> list[str]:
    if revisions:
        shas: list[str] = []
        for revision in revisions:
            resolved = _git(
                repo, "rev-parse", "--verify", f"{revision}^{{commit}}", required=False
            ).strip()
            if not resolved:
                raise SnapshotImportError(f"unknown revision in local snapshot: {revision}")
            if not re.fullmatch(r"[0-9a-f]{40}([0-9a-f]{24})?", resolved):
                raise SnapshotImportError(f"revision did not resolve to a commit: {revision}")
            shas.append(resolved)
        return shas
    listing = _git(repo, "log", "--all", "--format=%H %ct")
    timed: list[tuple[int, str]] = []
    for line in listing.splitlines():
        parts = line.split()
        if len(parts) == 2:
            timed.append((int(parts[1]), parts[0]))
    return [sha for _ts, sha in sorted(timed)]


def _commit_meta(repo: str | Path, sha: str) -> tuple[str, str, str]:
    raw = _git(repo, "show", "-s", "--format=%B%x00%aI%x00%cI", sha)
    message, authored_at, committed_at = (raw.split("\0") + ["", "", ""])[:3]
    return message.strip("\n"), authored_at.strip(), committed_at.strip()


def _patch(repo: str | Path, sha: str) -> str:
    return _git(repo, "diff-tree", "--root", "--no-commit-id", "-r", "-p", "--no-color", sha)


def _ancestry(repo: str | Path, sha: str) -> list[str]:
    return _git(repo, "rev-list", "--first-parent", sha).split()


def _branch_topic(repo: str | Path, sha: str) -> str:
    return _git(repo, "name-rev", "--name-only", "--no-undefined", sha, required=False).strip()


def _tree_entries(repo: str | Path, sha: str, *, limit: int = 20000) -> list[RepositoryEntry]:
    listing = _git(repo, "ls-tree", "-r", "-l", "--full-tree", sha)
    entries: list[RepositoryEntry] = []
    for line in listing.splitlines():
        meta, _, path = line.partition("\t")
        fields = meta.split()
        if len(fields) < 4 or fields[1] != "blob":
            continue
        blob = fields[2]
        size = int(fields[3]) if fields[3].isdigit() else 0
        content = _git_bytes(repo, "cat-file", "blob", blob)
        entries.append(
            RepositoryEntry(
                path=path,
                blob_sha=blob,
                size_bytes=size,
                sha256="sha256:" + hashlib.sha256(content).hexdigest(),
            )
        )
        if len(entries) > limit:
            raise SnapshotImportError(f"snapshot exceeds the {limit}-file import limit")
    entries.sort(key=lambda entry: entry.path)
    return entries


def _tree_digest(entries: list[RepositoryEntry]) -> str:
    digest = hashlib.sha256()
    for entry in entries:
        digest.update(entry.path.encode())
        digest.update(b"\0")
        digest.update(entry.blob_sha.encode())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _case_id_for(group_id: str) -> str:
    return "case-" + group_id.removeprefix("group-")


def _select(tree: dict[str, str], predicate) -> dict[str, str]:
    return {path: content for path, content in sorted(tree.items()) if predicate(path)}


def _read_tree(repo: str | Path, sha: str) -> dict[str, str]:
    tree: dict[str, str] = {}
    for entry in _tree_entries(repo, sha):
        raw = _git_bytes(repo, "cat-file", "blob", entry.blob_sha)
        tree[entry.path] = raw.decode("utf-8", errors="replace")
    return tree


def _prompt_for(repo: str | Path, members: list[CommitFact]) -> str:
    ordered = sorted(members, key=lambda fact: (fact.committed_at, fact.commit_sha))
    blocks = []
    for fact in ordered:
        message, _authored, _committed = _commit_meta(repo, fact.commit_sha)
        blocks.append(f"## {fact.commit_sha}\n\n{message}\n")
    return "# Imported snapshot\n\n" + "\n".join(blocks)


def _environment_lock() -> EnvironmentLock:
    return EnvironmentLock(
        python=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        platform=f"{platform.system()} {platform.machine()}",
        dependencies=[],
        note="Local snapshot import; no installs and no network access during import.",
    )


def import_snapshot(
    repo: str | Path,
    out_root: str | Path,
    *,
    revisions: list[str] | None = None,
    explicit_groups: list[list[str]] | None = None,
    window_minutes: int = DEFAULT_WINDOW_MINUTES,
) -> ImportResult:
    """Import a local git snapshot into deterministic case packages."""
    _require_repository(repo)
    repository = repository_identity(repo)
    shas = _commit_shas(repo, revisions)
    if not shas:
        raise SnapshotImportError(f"no commits to import from {repo}")

    facts: list[CommitFact] = []
    for sha in shas:
        message, _authored, committed_at = _commit_meta(repo, sha)
        patch = _patch(repo, sha)
        facts.append(
            CommitFact(
                repository=repository,
                commit_sha=sha,
                committed_at=committed_at,
                patch=patch,
                issue_ref=issue_reference(message, _branch_topic(repo, sha)),
            )
        )

    groups = group_commits(facts, window_minutes=window_minutes, explicit_groups=explicit_groups)
    by_sha = {fact.commit_sha: fact for fact in facts}
    out = Path(out_root)
    out.mkdir(parents=True, exist_ok=True)

    packages: list[CasePackage] = []
    for group in groups:
        members = [by_sha[sha] for sha in group.commit_shas]
        primary = min(members, key=lambda fact: (fact.committed_at, fact.commit_sha))
        case_id = _case_id_for(group.group_id)
        patch = primary.patch
        tree = _read_tree(repo, primary.commit_sha)
        visible = _select(tree, lambda path: bool(_TEST_PATH.search(path)))
        fixtures = _select(
            tree, lambda path: path.split("/", 1)[0] in _FIXTURE_TOP if "/" in path else False
        )
        message, authored_at, committed_at = _commit_meta(repo, primary.commit_sha)
        provenance = SourceProvenance(
            case_id=case_id,
            origin="local-git",
            repository=repository,
            commit_sha=primary.commit_sha,
            ancestry=_ancestry(repo, primary.commit_sha),
            patch_digest=patch_digest(patch),
            changed_files=changed_files(patch),
            authored_at=authored_at,
            committed_at=committed_at,
            baseline=message.splitlines()[0] if message else "",
            generator=GENERATOR,
        )
        entries = _tree_entries(repo, primary.commit_sha)
        manifest = RepositoryManifest(
            repository=repository,
            commit_sha=primary.commit_sha,
            entries=entries,
            file_count=len(entries),
            digest=_tree_digest(entries),
        )
        hidden_ref = f"sealed-unassigned:{case_id}"
        task = {
            "task_id": f"{case_id}-t1",
            "case_id": case_id,
            "group_id": group.group_id,
            "hidden_ref": hidden_ref,
            "created_at": group.created_at,
            "workflow": "imported",
            "split": "development",
            "time_budget_s": 600,
            "verifier_ref": f"verifier:{case_id}:v1",
            "verifier_id": f"verifier:{case_id}",
            "repo_subdir": "repo",
            "allowed_paths": changed_files(patch),
            "protected_paths": [],
            "acceptance": ["Re-import of the same snapshot is byte-identical."],
        }
        packages.append(
            cases.write_case_package(
                out / case_id,
                case_id=case_id,
                group_id=group.group_id,
                task=task,
                prompt=_prompt_for(repo, members),
                repository_manifest=manifest,
                environment_lock=_environment_lock(),
                provenance=provenance,
                hidden_ref=hidden_ref,
                visible_tests=visible,
                public_fixtures=fixtures,
                created_at=group.created_at,
            )
        )

    (out / "groups.json").write_text(
        json.dumps([group.model_dump(mode="json") for group in groups], indent=2, sort_keys=True)
        + "\n"
    )
    return ImportResult(out_root=str(out), groups=groups, packages=packages)
