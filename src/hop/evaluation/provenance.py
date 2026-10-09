"""Local snapshot provenance: normalization, fingerprints, validation.

Everything here is a pure function of the input bytes: no clock, no network, no
ambient configuration. ``normalize_patch`` removes diff index noise so the same
logical change has the same digest whether or not git printed blob hashes, and
``near_duplicate`` compares changed-line multisets within the same file set.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from pydantic import ValidationError

from hop.contracts.evaluation import SourceProvenance

#: Similarity at or above this value counts as a near-duplicate patch.
NEAR_DUPLICATE_THRESHOLD = 0.8

_INDEX_LINE = re.compile(r"^index [0-9a-f]+\.\.[0-9a-f]+(?: \d+)?$")
_DIFF_GIT = re.compile(r"^diff --git a/(.+?) b/(.+)$")
_HUNK = re.compile(r"^@@ ")
_PLUS = re.compile(r"^\+\+\+ b/(.+?)(?:\t.*)?$")
_MINUS = re.compile(r"^--- a/(.+?)(?:\t.*)?$")


class ProvenanceError(ValueError):
    """Malformed or internally inconsistent provenance record."""


def normalize_patch(patch: str) -> str:
    """Strip diff index/mode noise and trailing whitespace per line."""
    lines: list[str] = []
    for line in patch.splitlines():
        if _INDEX_LINE.match(line):
            continue
        if line.startswith("index "):
            continue
        if line.startswith(("new file mode ", "deleted file mode ")):
            continue
        lines.append(line.rstrip())
    while lines and not lines[-1]:
        lines.pop()
    return "".join(line + "\n" for line in lines)


def patch_digest(patch: str) -> str:
    """Content digest of the normalized patch (``sha256:<hex>``)."""
    return "sha256:" + hashlib.sha256(normalize_patch(patch).encode()).hexdigest()


def changed_files(patch: str) -> list[str]:
    """Sorted unique post-image paths touched by the patch.

    Handles ``git diff`` output (``diff --git a/... b/...``) and plain unified
    diffs (``--- a/...`` / ``+++ b/...``), which the pack candidate patches use.
    """
    paths = {
        match.group(2).strip()
        for line in patch.splitlines()
        if (match := _DIFF_GIT.match(line)) is not None
    }
    if not paths:
        for line in patch.splitlines():
            match = _PLUS.match(line)
            if match and match.group(1) != "/dev/null":
                paths.add(match.group(1).strip())
    if not paths:
        for line in patch.splitlines():
            match = _MINUS.match(line)
            if match and match.group(1) != "/dev/null":
                paths.add(match.group(1).strip())
    return sorted(paths)


def changed_line_multiset(patch: str) -> Counter[str]:
    """Multiset of added/removed content lines (headers and hunk markers excluded)."""
    multiset: Counter[str] = Counter()
    in_hunk = False
    for line in patch.splitlines():
        if _HUNK.match(line):
            in_hunk = True
            continue
        if line.startswith("diff --git "):
            in_hunk = False
            continue
        if not in_hunk or not line:
            continue
        if line[0] in "+-" and not line.startswith(("+++", "---")):
            multiset[line[1:].rstrip()] += 1
    return multiset


def patch_similarity(left: str, right: str) -> float:
    """Jaccard similarity of changed-line multisets; 0.0 when file sets differ."""
    if changed_files(left) != changed_files(right):
        return 0.0
    a = changed_line_multiset(left)
    b = changed_line_multiset(right)
    union = a | b
    if not union:
        return 0.0
    return sum((a & b).values()) / sum(union.values())


def near_duplicate(left: str, right: str, *, threshold: float = NEAR_DUPLICATE_THRESHOLD) -> bool:
    """True when two patches touch the same files with near-identical lines."""
    if patch_digest(left) == patch_digest(right):
        return True
    return patch_similarity(left, right) >= threshold


def dump_source_provenance(record: SourceProvenance) -> str:
    """Deterministic JSON for a provenance record."""
    return json.dumps(record.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"


def load_source_provenance(path: str | Path) -> SourceProvenance:
    """Validate a ``source-provenance.json``; raise :class:`ProvenanceError`.

    Malformed JSON, unknown/missing fields, bad digest shapes, or an ancestry
    that does not contain the primary commit are all rejected explicitly -- a
    case package is never accepted on a partially valid provenance file.
    """
    source = Path(path)
    try:
        raw = source.read_text()
    except OSError as exc:
        raise ProvenanceError(f"provenance unreadable: {source}: {exc}") from exc
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ProvenanceError(f"provenance is not valid JSON: {source}: {exc}") from exc
    if not isinstance(data, dict):
        raise ProvenanceError(f"provenance must be a JSON object: {source}")
    try:
        record = SourceProvenance.model_validate(data)
    except ValidationError as exc:
        raise ProvenanceError(f"provenance failed contract validation: {exc}") from exc
    if record.ancestry and record.commit_sha not in record.ancestry:
        raise ProvenanceError(
            f"provenance ancestry does not contain the primary commit {record.commit_sha}"
        )
    return record
