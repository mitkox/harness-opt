"""M3 CLI surface tests (component / skill / registry / profile)."""

import json

import pytest

from hop.cli import main

CODING = "examples/profiles/coding.yaml"
PR_REVIEW = "examples/profiles/pr-review.yaml"


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOP_HOME", str(tmp_path / "hop"))
    return str(tmp_path / "hop")


def _json(capsys):
    return json.loads(capsys.readouterr().out)


def test_registry_import_and_component_commands(home, capsys):
    assert main(["registry", "import", "components"]) == 0
    imported = _json(capsys)
    assert imported["count"] >= 15
    assert main(["component", "list", "--type", "skill"]) == 0
    skills = _json(capsys)
    names = {s["name"] for s in skills}
    assert {"debugging", "code-review", "testing"} <= names
    assert main(["component", "show", "debugging@2.0.0"]) == 0
    shown = _json(capsys)
    assert shown["record"]["component_type"] == "skill"
    assert "canonical/SKILL.md" in shown["files"]
    assert main(["skill", "inspect", "debugging"]) == 0
    assert _json(capsys)["record"]["logical_name"] == "debugging"
    assert main(["registry", "verify"]) == 0
    assert _json(capsys)["ok"] is True


def test_profile_validate_ok_and_fail(home, capsys):
    main(["registry", "import", "components"])
    capsys.readouterr()
    assert main(["profile", "validate", CODING]) == 0
    assert _json(capsys)["valid"] is True
    assert main(["profile", "validate", "examples/profiles/missing.yaml"]) == 2
    assert _json(capsys)["valid"] is False


def test_profile_lock_deps_explain(home, capsys, tmp_path):
    main(["registry", "import", "components"])
    capsys.readouterr()
    out = tmp_path / "coding.hop.lock"
    assert main(["profile", "lock", CODING, "--out", str(out)]) == 0
    locked = _json(capsys)
    assert locked["lock_digest"].startswith("sha256:")
    assert out.exists()
    assert main(["profile", "deps", CODING]) == 0
    deps = _json(capsys)
    assert deps["graph"]
    assert main(["profile", "explain", CODING]) == 0
    explain = _json(capsys)
    assert explain["variant_selections"]
    assert explain["target_transformations"]


def test_profile_diff(home, capsys):
    main(["registry", "import", "components"])
    capsys.readouterr()
    assert main(["profile", "diff", CODING, PR_REVIEW]) == 0
    result = _json(capsys)
    assert result["a"]["name"] == "coding"
    assert result["b"]["name"] == "pr-review"
    assert result["components"]["removed"]
    assert result["a"]["profile_digest"] != result["b"]["profile_digest"]


def test_profile_compile_and_export_verify(home, capsys, tmp_path):
    main(["registry", "import", "components"])
    capsys.readouterr()
    out = tmp_path / "pi-out"
    assert main(["profile", "compile", PR_REVIEW, "--out", str(out)]) == 0
    compiled = _json(capsys)
    assert compiled["artifact_digest"].startswith("sha256:")
    assert (out / "pi-profile.json").exists()
    export_dir = tmp_path / "apm"
    assert main(["profile", "export", PR_REVIEW, "--out", str(export_dir)]) == 0
    exported = _json(capsys)
    assert exported["package_digest"].startswith("sha256:")
    assert main(["profile", "verify-export", str(export_dir), "--profile", PR_REVIEW]) == 0
    assert _json(capsys)["ok"] is True


def test_profile_materialize_by_digest(home, capsys, tmp_path):
    main(["registry", "import", "components"])
    capsys.readouterr()
    main(["profile", "lock", PR_REVIEW, "--out", str(tmp_path / "review.hop.lock")])
    capsys.readouterr()
    assert main(["profile", "validate", PR_REVIEW]) == 0
    digest = _json(capsys)["profile_digest"]
    out = tmp_path / "materialized"
    assert main(["profile", "materialize", digest, "--out", str(out)]) == 0
    result = _json(capsys)
    assert result["profile_digest"] == digest
    assert (out / "pi-profile.json").exists()


def test_profile_materialize_unknown_digest_fails(home, capsys):
    main(["registry", "import", "components"])
    capsys.readouterr()
    rc = main(["profile", "materialize", "sha256:" + "ab" * 32])
    assert rc == 2
