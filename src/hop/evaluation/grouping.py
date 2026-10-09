"""Deterministic grouping/dedup of related local commits (AOP-020 half A).

A group is a maximal set of commits in the same repository linked by any of:

* the same normalized patch digest (exact re-application/duplicate);
* near-duplicate patches -- same changed files and a changed-line Jaccard
  similarity at or above the provenance threshold;
* the same non-empty issue reference (commit trailer or branch topic) inside the
  configured time window.

Group identifiers are content hashes over the repository, the sorted commit
shas and the sorted patch digests, so grouping is stable and a group is never
re-split by a later import. All output collections are sorted.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime

from hop.contracts.evaluation import CaseGroup

from .provenance import near_duplicate, patch_digest

DEFAULT_WINDOW_MINUTES = 180


class GroupingError(ValueError):
    """Grouping inputs are inconsistent (e.g. an unknown explicit commit)."""


_TRAILER = re.compile(
    r"(?im)^\s*(?:case|issue|refs?|fixes|closes)\s*:\s*([A-Za-z0-9][A-Za-z0-9._/-]*)"
)
_AOP = re.compile(r"\bAOP-\d{2,4}\b", re.IGNORECASE)


@dataclass(frozen=True)
class CommitFact:
    """The subset of commit metadata grouping depends on."""

    repository: str
    commit_sha: str
    committed_at: str
    patch: str
    issue_ref: str = ""


def issue_reference(message: str, branch: str = "") -> str:
    """Issue/topic reference from a commit trailer, an AOP id, or the branch name."""
    match = _TRAILER.search(message)
    if match:
        return match.group(1).lower()
    match = _AOP.search(message)
    if match:
        return match.group(0).upper()
    topic = branch.strip().removeprefix("refs/heads/")
    if topic in ("", "HEAD") or topic.startswith("detached"):
        return ""
    return topic


def deterministic_group_id(
    repository: str, commit_shas: list[str], patch_digests: list[str]
) -> str:
    """Content-hash group id; independent of input ordering."""
    payload = json.dumps(
        {
            "repository": repository,
            "commit_shas": sorted(commit_shas),
            "patch_digests": sorted(patch_digests),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return "group-" + hashlib.sha256(payload.encode()).hexdigest()[:16]


def _parse_timestamp(value: str) -> datetime | None:
    text = value.strip().replace("Z", "+00:00")
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _within_window(left: CommitFact, right: CommitFact, minutes: int) -> bool:
    a = _parse_timestamp(left.committed_at)
    b = _parse_timestamp(right.committed_at)
    if a is None or b is None:
        return False
    return abs((a - b).total_seconds()) <= minutes * 60


def _related(left: CommitFact, right: CommitFact, window_minutes: int) -> bool:
    if left.repository != right.repository:
        return False
    if patch_digest(left.patch) == patch_digest(right.patch):
        return True
    if near_duplicate(left.patch, right.patch):
        return True
    return bool(
        left.issue_ref
        and left.issue_ref == right.issue_ref
        and _within_window(left, right, window_minutes)
    )


def group_commits(
    facts: list[CommitFact],
    *,
    window_minutes: int = DEFAULT_WINDOW_MINUTES,
    explicit_groups: list[list[str]] | None = None,
) -> list[CaseGroup]:
    """Group commit facts into deterministic :class:`CaseGroup` records.

    ``explicit_groups`` forces the listed shas into one group regardless of the
    automatic rules; an unknown sha raises :class:`GroupingError` rather than
    being silently dropped.
    """
    unique: dict[str, CommitFact] = {}
    for fact in facts:
        unique.setdefault(fact.commit_sha, fact)
    ordered = [unique[sha] for sha in sorted(unique)]

    parent = {fact.commit_sha: fact.commit_sha for fact in ordered}

    def find(sha: str) -> str:
        while parent[sha] != sha:
            parent[sha] = parent[parent[sha]]
            sha = parent[sha]
        return sha

    def union(left: str, right: str) -> None:
        a, b = find(left), find(right)
        if a != b:
            parent[max(a, b)] = min(a, b)

    for index, left in enumerate(ordered):
        for right in ordered[index + 1 :]:
            if _related(left, right, window_minutes):
                union(left.commit_sha, right.commit_sha)

    for members in explicit_groups or []:
        unknown = sorted(sha for sha in members if sha not in parent)
        if unknown:
            raise GroupingError("explicit group references unknown commits: " + ", ".join(unknown))
        present = [sha for sha in members if sha in parent]
        for sha in present[1:]:
            union(present[0], sha)

    components: dict[str, list[CommitFact]] = {}
    for fact in ordered:
        components.setdefault(find(fact.commit_sha), []).append(fact)

    groups = [_build_group(members) for members in components.values()]
    return sorted(groups, key=lambda group: (group.created_at, group.group_id))


def _build_group(members: list[CommitFact]) -> CaseGroup:
    members = sorted(members, key=lambda fact: fact.commit_sha)
    commit_shas = [fact.commit_sha for fact in members]
    digests = [patch_digest(fact.patch) for fact in members]
    timestamps = [fact.committed_at for fact in members if fact.committed_at]
    return CaseGroup(
        group_id=deterministic_group_id(members[0].repository, commit_shas, digests),
        repository=members[0].repository,
        commit_shas=commit_shas,
        patch_digests=digests,
        created_at=min(timestamps) if timestamps else "",
    )
