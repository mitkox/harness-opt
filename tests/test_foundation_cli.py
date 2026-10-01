import json

import pytest

from hop.cli import main
from hop.config import Paths


@pytest.mark.parametrize("group", ["run", "profile", "component", "registry", "deployment"])
def test_nested_help(group, capsys):
    with pytest.raises(SystemExit) as exc:
        main([group, "--help"])
    assert exc.value.code == 0
    assert f"hop {group}" in capsys.readouterr().out


def test_init_never_overwrites_and_list_is_read_only(tmp_path, capsys):
    assert main(["--workspace", str(tmp_path), "run", "list"]) == 0
    assert json.loads(capsys.readouterr().out) == []
    assert list(tmp_path.iterdir()) == []
    assert main(["init", "--workspace", str(tmp_path)]) == 0
    capsys.readouterr()
    before = (tmp_path / "hop.toml").read_bytes()
    assert main(["init", "--workspace", str(tmp_path)]) == 2
    assert (tmp_path / "hop.toml").read_bytes() == before


def test_path_precedence(tmp_path, monkeypatch):
    (tmp_path / "hop.toml").write_text('version = 1\n[paths]\nruns = "configured"\n')
    assert Paths.resolve(workspace=tmp_path).runs == tmp_path / "configured"
    monkeypatch.setenv("HOP_RUNS_DIR", "environment")
    assert Paths.resolve(workspace=tmp_path).runs == tmp_path / "environment"
    assert Paths.resolve(workspace=tmp_path, runs="explicit").runs == tmp_path / "explicit"


def test_new_run_requires_model(tmp_path, capsys):
    assert main(["run", "start", "--workspace", str(tmp_path), "--case", "missing"]) == 2
    assert "model_required" in capsys.readouterr().err


def test_text_format_does_not_change_next_call(tmp_path, capsys):
    assert main(["run", "list", "--workspace", str(tmp_path), "--format", "text"]) == 0
    assert "No records" in capsys.readouterr().out
    assert main(["run", "list", "--workspace", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out) == []


def test_discovery_uses_workspace_from_another_directory(tmp_path, monkeypatch, capsys):
    workspace = tmp_path / "workspace"
    inventory = workspace / "profiles/models/local-inventory.json"
    inventory.parent.mkdir(parents=True)
    inventory.write_text(
        json.dumps({"deployments": [{"status": "qualified", "deployment_id": "fixture"}]})
    )
    harnesses = workspace / "profiles/harnesses/local-inventory.json"
    harnesses.parent.mkdir(parents=True)
    harnesses.write_text(json.dumps({"harnesses": [{"harness": "pi", "version": "fixture"}]}))
    monkeypatch.chdir(tmp_path)
    assert main(["discover", "--workspace", str(workspace), "--format", "text"]) == 0
    assert "local_pair:" in capsys.readouterr().out
