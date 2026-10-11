"""AOP-020 half A: local snapshot import, grouping, and determinism."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import jsonschema
import pytest

from hop.evaluation import cases
from hop.evaluation.grouping import CommitFact, deterministic_group_id, group_commits
from hop.evaluation.importer import import_snapshot
from hop.evaluation.provenance import (
    near_duplicate,
    patch_digest,
    patch_similarity,
)

WHEN_1 = "2026-01-01T00:00:00+00:00"
WHEN_2 = "2026-01-01T00:30:00+00:00"
WHEN_3 = "2026-02-01T00:00:00+00:00"

BASE_MEDIAN = "def median(x):\n    return x[len(x) // 2]\n"


def _run(repo: Path, *args: str, env: dict | None = None) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
    )
    return proc.stdout


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _run(path, "init", "-q", "-b", "main", ".")
    _run(path, "config", "user.email", "fixture@example.com")
    _run(path, "config", "user.name", "Fixture")
    return path


def _write_files(repo: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)


def _commit(repo: Path, files: dict[str, str], message: str, when: str) -> str:
    _write_files(repo, files)
    _run(repo, "add", "-A")
    env = {
        "GIT_AUTHOR_DATE": when,
        "GIT_COMMITTER_DATE": when,
        "GIT_AUTHOR_NAME": "Fixture",
        "GIT_AUTHOR_EMAIL": "fixture@example.com",
        "GIT_COMMITTER_NAME": "Fixture",
        "GIT_COMMITTER_EMAIL": "fixture@example.com",
    }
    _run(repo, "commit", "-q", "-m", message, env=env)
    return _run(repo, "rev-parse", "HEAD").strip()


def _related_repo(tmp_path: Path) -> Path:
    repo = _init_repo(tmp_path / "repo")
    _commit(
        repo,
        {"median.py": BASE_MEDIAN, "tests/test_median.py": "def test_median():\n    pass\n"},
        "fix median for even input\n\nCase: issue-1",
        WHEN_1,
    )
    _commit(
        repo,
        {
            "median.py": (
                "def median(x):\n    values = sorted(x)\n    return values[len(values) // 2]\n"
            )
        },
        "handle even length\n\nCase: issue-1",
        WHEN_2,
    )
    _commit(repo, {"other.py": "VALUE = 1\n"}, "unrelated change\n\nCase: issue-2", WHEN_3)
    return repo


def _tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_related_commits_form_one_group_and_unrelated_commits_do_not(tmp_path):
    result = import_snapshot(_related_repo(tmp_path), tmp_path / "out")
    sizes = sorted(len(group.commit_shas) for group in result.groups)
    assert sizes == [1, 2], [(g.group_id, g.commit_shas) for g in result.groups]
    grouped = max(result.groups, key=lambda group: len(group.commit_shas))
    assert grouped.repository.startswith("sha256:")
    assert len(grouped.patch_digests) == 2
    assert all(len(sha) == 40 for sha in grouped.commit_shas)
    # Two packages, one per group, with deterministic names.
    assert len(result.packages) == 2
    assert {package.group_id for package in result.packages} == {
        group.group_id for group in result.groups
    }


def test_near_duplicate_patches_group_without_issue_reference(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    base = _commit(repo, {"median.py": BASE_MEDIAN}, "base", WHEN_1)
    # Two re-applications of the same change that differ in exactly one line:
    # 8 shared added lines over a 10-line union => similarity 0.8.
    common = "".join(f"    step_{index} = {index}\n" for index in range(1, 9))
    _run(repo, "checkout", "-q", "-b", "topic-a", base)
    _commit(
        repo,
        {"median.py": BASE_MEDIAN + common + "    marker = 'a'\n"},
        "apply candidate fix",
        WHEN_2,
    )
    _run(repo, "checkout", "-q", "-b", "topic-b", base)
    _commit(
        repo,
        {"median.py": BASE_MEDIAN + common + "    marker = 'b'\n"},
        "reapply candidate fix",
        WHEN_2,
    )
    result = import_snapshot(repo, tmp_path / "out")
    related = [group for group in result.groups if len(group.commit_shas) == 2]
    assert len(related) == 1, [(g.group_id, g.commit_shas) for g in result.groups]


def test_reimport_is_byte_identical_and_ids_are_stable(tmp_path):
    repo = _related_repo(tmp_path)
    first = import_snapshot(repo, tmp_path / "out-1")
    again = import_snapshot(repo, tmp_path / "out-1")
    second = import_snapshot(repo, tmp_path / "out-2")

    assert [g.group_id for g in first.groups] == [g.group_id for g in again.groups]
    assert [p.case_id for p in first.packages] == [p.case_id for p in again.packages]
    assert [p.case_id for p in first.packages] == [p.case_id for p in second.packages]
    assert _tree(tmp_path / "out-1") == _tree(tmp_path / "out-2")
    # Every package round-trips through the validating reader.
    for package in first.packages:
        loaded = cases.load_case_package(
            tmp_path / "out-1" / package.case_id, expected_digest=package.content_digest
        )
        assert loaded.case_id == package.case_id
        assert loaded.hidden_ref == package.hidden_ref


def test_case_package_layout_and_opaque_hidden_ref(tmp_path):
    result = import_snapshot(_related_repo(tmp_path), tmp_path / "out")
    package = result.packages[0]
    root = tmp_path / "out" / package.case_id
    for name in (
        "task.yaml",
        "prompt.md",
        "repository.manifest.json",
        "environment.lock.json",
        "source-provenance.json",
    ):
        assert (root / name).is_file(), name
    for directory in ("public-fixtures", "visible-tests"):
        assert (root / directory).is_dir(), directory
    # hidden_ref is an opaque token: it is not a resolvable local path.
    assert package.hidden_ref
    assert "/" not in package.hidden_ref and not package.hidden_ref.startswith(".")
    assert package.content_digest == cases.package_content_digest(root)
    loaded = cases.load_case_package(root)
    assert loaded.public_tests == package.public_tests
    assert "tests/test_median.py" in loaded.public_tests


def test_group_id_is_content_deterministic():
    repo = "sha256:" + "ab" * 32
    shas = ["b" * 40, "a" * 40]
    digests = ["sha256:" + "cd" * 32, "sha256:" + "ef" * 32]
    assert deterministic_group_id(repo, shas, digests) == deterministic_group_id(
        repo, list(reversed(shas)), list(reversed(digests))
    )
    assert deterministic_group_id(repo, shas, digests) != deterministic_group_id(
        repo, shas[:1], digests[:1]
    )


def test_grouping_rules_are_explicit():
    same_repo = "sha256:" + "11" * 32
    duplicate = CommitFact(same_repo, "a" * 40, WHEN_1, "--- a\n+++ b\n@@\n+x\n")
    replayed = CommitFact(same_repo, "b" * 40, WHEN_2, duplicate.patch)
    other = CommitFact(same_repo, "c" * 40, WHEN_3, "--- a\n+++ c\n@@\n+z\n")
    groups = group_commits([duplicate, replayed, other])
    assert sorted(len(group.commit_shas) for group in groups) == [1, 2]
    assert len(groups[0].commit_shas) + len(groups[1].commit_shas) == 3


def test_near_duplicate_similarity_threshold():
    left = "diff --git a/x.py b/x.py\n@@ -1 +1,5 @@\n+alpha\n+beta\n+gamma\n+delta\n+epsilon\n"
    right = (
        "diff --git a/x.py b/x.py\n@@ -1 +1,5 @@\n+alpha\n+beta\n+gamma\n+delta\n+epsilon\n+zeta\n"
    )
    assert patch_digest(left) != patch_digest(right)
    assert 0.0 < patch_similarity(left, right) < 1.0
    assert near_duplicate(left, right) is True
    assert near_duplicate(left, "diff --git a/y.py b/y.py\n@@\n+unrelated\n") is False


def test_generated_records_validate_against_published_schemas(tmp_path):
    specs = Path(__file__).resolve().parents[1] / "specs"
    group_schema = json.loads((specs / "case-group.schema.json").read_text())
    package_schema = json.loads((specs / "case-package.schema.json").read_text())
    provenance_schema = json.loads((specs / "source-provenance.schema.json").read_text())
    checker = jsonschema.FormatChecker()

    result = import_snapshot(_related_repo(tmp_path), tmp_path / "out")
    assert result.groups and result.packages
    for group in result.groups:
        jsonschema.validate(group.model_dump(mode="json"), group_schema, format_checker=checker)
    for package in result.packages:
        jsonschema.validate(package.model_dump(mode="json"), package_schema, format_checker=checker)
        raw = (tmp_path / "out" / package.case_id / "source-provenance.json").read_text()
        jsonschema.validate(json.loads(raw), provenance_schema, format_checker=checker)


def test_cli_evaluation_import_is_deterministic_json(tmp_path, capsys):
    from hop.cli import main

    repo = _related_repo(tmp_path)
    out = tmp_path / "cli-out"
    assert main(["evaluation", "import", str(repo), "--out", str(out)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["out_root"] == str(out)
    assert len(payload["packages"]) == 2
    assert sorted(len(group["commit_shas"]) for group in payload["groups"]) == [1, 2]

    with pytest.raises(SystemExit) as exc:
        main(["evaluation", "--help"])
    assert exc.value.code == 0


def test_cli_explicit_groups_and_invalid_spec(tmp_path, capsys):
    from hop.cli import main

    repo = _related_repo(tmp_path)
    shas = _run(repo, "rev-list", "--all").split()
    spec = tmp_path / "groups.json"
    spec.write_text(json.dumps({"groups": [{"commit_shas": shas}]}))
    out = tmp_path / "cli-out-2"
    assert main(["evaluation", "import", str(repo), "--out", str(out), "--groups", str(spec)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["groups"]) == 1
    assert payload["groups"][0]["group_id"].startswith("group-")

    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert main(["evaluation", "import", str(repo), "--out", str(out), "--groups", str(bad)]) == 2
    assert "invalid_group_spec" in capsys.readouterr().err
