"""AOP-020 half A adversarial cases: malformed provenance and package rejection."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from hop.contracts.evaluation import EnvironmentLock, RepositoryManifest, SourceProvenance
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
            hidden_ref="opaque:token",
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
            hidden_ref="opaque:token",
        )
