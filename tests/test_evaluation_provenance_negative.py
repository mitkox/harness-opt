"""AOP-020 half A adversarial cases: malformed provenance and package rejection."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from hop.contracts.evaluation import (
    CaseGroup,
    CasePackage,
    EnvironmentLock,
    RepositoryManifest,
    SourceProvenance,
)
from hop.evaluation import cases
from hop.evaluation.cases import CaseImportError, write_case_package
from hop.evaluation.importer import SnapshotImportError, import_snapshot
from hop.evaluation.provenance import ProvenanceError, load_source_provenance

WHEN = "2026-01-01T00:00:00+00:00"


def _run(repo: Path, *args: str, env: dict | None = None) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
    ).stdout


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(repo, "init", "-q", "-b", "main", ".")
    _run(repo, "config", "user.email", "fixture@example.com")
    _run(repo, "config", "user.name", "Fixture")
    (repo / "median.py").write_text("def median(x):\n    return x[0]\n")
    _run(repo, "add", "-A")
    env = {
        "GIT_AUTHOR_DATE": WHEN,
        "GIT_COMMITTER_DATE": WHEN,
        "GIT_AUTHOR_NAME": "Fixture",
        "GIT_AUTHOR_EMAIL": "fixture@example.com",
        "GIT_COMMITTER_NAME": "Fixture",
        "GIT_COMMITTER_EMAIL": "fixture@example.com",
    }
    _run(repo, "commit", "-q", "-m", "fix median\n\nCase: issue-1", env=env)
    return repo


def test_import_rejects_missing_path_and_non_repository(tmp_path):
    with pytest.raises(SnapshotImportError, match="not a directory"):
        import_snapshot(tmp_path / "missing", tmp_path / "out")
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(SnapshotImportError, match="not a local git work tree"):
        import_snapshot(plain, tmp_path / "out")


def test_import_rejects_unknown_revision(tmp_path):
    repo = _repo(tmp_path)
    with pytest.raises(SnapshotImportError, match="unknown revision"):
        import_snapshot(repo, tmp_path / "out", revisions=["deadbeef"])


@pytest.mark.parametrize(
    "mutation",
    ["invalid-json", "missing-patch-digest", "bad-digest", "ancestry-missing-commit"],
)
def test_malformed_provenance_is_rejected(tmp_path, mutation):
    out = tmp_path / "out"
    result = import_snapshot(_repo(tmp_path), out)
    package_root = out / result.packages[0].case_id
    path = package_root / "source-provenance.json"
    if mutation == "invalid-json":
        path.write_text("{not json")
    else:
        data = json.loads(path.read_text())
        if mutation == "missing-patch-digest":
            del data["patch_digest"]
        elif mutation == "bad-digest":
            data["patch_digest"] = "sha256:nothex"
        elif mutation == "ancestry-missing-commit":
            data["ancestry"] = ["f" * 40]
        path.write_text(json.dumps(data))

    with pytest.raises(ProvenanceError):
        load_source_provenance(path)
    with pytest.raises(ProvenanceError):
        cases.load_case_package(package_root)


def test_package_missing_required_file_is_rejected(tmp_path):
    out = tmp_path / "out"
    result = import_snapshot(_repo(tmp_path), out)
    package_root = out / result.packages[0].case_id
    (package_root / "task.yaml").unlink()
    with pytest.raises(CaseImportError, match="missing required file: task.yaml"):
        cases.load_case_package(package_root)


def test_tampered_content_fails_expected_digest(tmp_path):
    out = tmp_path / "out"
    result = import_snapshot(_repo(tmp_path), out)
    package_root = out / result.packages[0].case_id
    loaded = cases.load_case_package(package_root)
    prompt = package_root / "prompt.md"
    prompt.write_text(prompt.read_text() + "tampered\n")
    with pytest.raises(CaseImportError, match="content digest mismatch"):
        cases.load_case_package(package_root, expected_digest=loaded.content_digest)


def _models(case_id: str) -> tuple[SourceProvenance, RepositoryManifest, EnvironmentLock]:
    provenance = SourceProvenance(
        case_id=case_id,
        origin="local-git",
        repository="sha256:" + "11" * 32,
        commit_sha="a" * 40,
        ancestry=["a" * 40],
        patch_digest="sha256:" + "22" * 32,
    )
    manifest = RepositoryManifest(
        repository=provenance.repository,
        commit_sha=provenance.commit_sha,
        entries=[],
        file_count=0,
        digest="sha256:" + "33" * 32,
    )
    return provenance, manifest, EnvironmentLock(python="3.14.0")


def test_writer_rejects_inconsistent_provenance_and_empty_hidden_ref(tmp_path):
    provenance, manifest, lock = _models("case-" + "ab" * 8)
    with pytest.raises(CaseImportError, match="does not match package"):
        write_case_package(
            tmp_path / "one",
            case_id="case-" + "cd" * 8,
            group_id="group-" + "ef" * 8,
            task={},
            prompt="x",
            repository_manifest=manifest,
            environment_lock=lock,
            provenance=provenance,
            hidden_ref=f"sealed-unassigned:{provenance.case_id}",
        )
    with pytest.raises(CaseImportError, match="hidden_ref"):
        write_case_package(
            tmp_path / "two",
            case_id=provenance.case_id,
            group_id="group-" + "ef" * 8,
            task={},
            prompt="x",
            repository_manifest=manifest,
            environment_lock=lock,
            provenance=provenance,
            hidden_ref="",
        )


def test_writer_rejects_manifest_commit_mismatch(tmp_path):
    provenance, manifest, lock = _models("case-" + "ab" * 8)
    manifest = manifest.model_copy(update={"commit_sha": "b" * 40})
    with pytest.raises(CaseImportError, match="disagree on the commit"):
        write_case_package(
            tmp_path / "three",
            case_id=provenance.case_id,
            group_id="group-" + "ef" * 8,
            task={},
            prompt="x",
            repository_manifest=manifest,
            environment_lock=lock,
            provenance=provenance,
            hidden_ref=f"sealed-unassigned:{provenance.case_id}",
        )


# -- WP-2.1f integrity tightening (A-5..A-9) --------------------------------


def test_manifest_digest_covers_entry_sha256(tmp_path):
    """A-6: mutating entries[].sha256 must invalidate the manifest digest."""
    out = tmp_path / "out"
    result = import_snapshot(_repo(tmp_path), out)
    root = out / result.packages[0].case_id
    path = root / "repository.manifest.json"
    data = json.loads(path.read_text())
    assert data["entries"], "fixture must contain at least one entry"
    data["entries"][0]["sha256"] = "sha256:" + "00" * 32
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    with pytest.raises(CaseImportError, match="manifest digest mismatch"):
        cases.load_case_package(root)


def test_verify_case_package_detects_tamper(tmp_path):
    """A-5: a trusted recorded digest detects an edited package."""
    out = tmp_path / "out"
    result = import_snapshot(_repo(tmp_path), out)
    root = out / result.packages[0].case_id
    recorded = cases.load_case_package(root).content_digest
    assert cases.verify_case_package(root, recorded) == recorded
    prompt = root / "prompt.md"
    prompt.write_text(prompt.read_text() + "tampered\n")
    with pytest.raises(CaseImportError, match="content digest mismatch"):
        cases.verify_case_package(root, recorded)


def test_sealed_token_never_appears_in_package_files(tmp_path):
    """A-7: a sealed token is attached in memory, never persisted."""
    from hop.evaluation.sealed.store import with_hidden_ref

    out = tmp_path / "out"
    result = import_snapshot(_repo(tmp_path), out)
    root = out / result.packages[0].case_id
    token = "ab" * 16
    record = cases.load_case_package(root)
    attached = with_hidden_ref(record, token)
    assert attached.hidden_ref == token
    for path in root.rglob("*"):
        if path.is_file():
            assert token not in path.read_text(errors="ignore"), path

    task = root / "task.yaml"
    data = yaml.safe_load(task.read_text())
    data["hidden_ref"] = token
    task.write_text(yaml.safe_dump(data, sort_keys=True))
    with pytest.raises(CaseImportError, match="sealed tokens are never persisted"):
        cases.load_case_package(root)


@pytest.mark.parametrize(
    "payload",
    [
        {
            "group_id": "group-" + "0" * 16,
            "repository": "sha256:" + "11" * 32,
            "commit_shas": ["not-a-sha"],
            "patch_digests": ["sha256:" + "22" * 32],
        },
        {
            "group_id": "group-" + "0" * 16,
            "repository": "sha256:" + "11" * 32,
            "commit_shas": ["a" * 40],
            "patch_digests": ["not-a-digest"],
        },
    ],
)
def test_case_group_items_are_patterned(payload):
    """A-8: per-item patterns reject non-sha/non-digest members."""
    with pytest.raises(ValidationError):
        CaseGroup.model_validate(payload)


def test_provenance_ancestry_items_are_patterned():
    with pytest.raises(ValidationError):
        SourceProvenance(
            case_id="case-" + "ab" * 8,
            origin="local-git",
            repository="sha256:" + "11" * 32,
            commit_sha="a" * 40,
            ancestry=["not-a-sha"],
            patch_digest="sha256:" + "22" * 32,
        )


@pytest.mark.parametrize("value", ["../../etc/passwd", "not a token", "case-" + "ab" * 8])
def test_hidden_ref_pattern_rejects_unknown_forms(value):
    with pytest.raises(ValidationError):
        CasePackage(
            case_id="case-" + "ab" * 8,
            group_id="group-" + "0" * 16,
            root="pkg",
            task_path="task.yaml",
            hidden_ref=value,
        )


def test_explicit_group_unknown_sha_is_a_typed_error(tmp_path):
    """A-9: a typo in an explicit group fails instead of silently dropping."""
    from hop.evaluation.grouping import CommitFact, GroupingError, group_commits

    repo = _repo(tmp_path)
    with pytest.raises(SnapshotImportError, match="unknown commits"):
        import_snapshot(repo, tmp_path / "out", explicit_groups=[["f" * 40]])

    fact = CommitFact(
        repository="sha256:" + "11" * 32,
        commit_sha="a" * 40,
        committed_at=WHEN,
        patch="--- a/x\n+++ b/x\n@@\n+x\n",
    )
    with pytest.raises(GroupingError, match="unknown commits"):
        group_commits([fact], explicit_groups=[["f" * 40]])
