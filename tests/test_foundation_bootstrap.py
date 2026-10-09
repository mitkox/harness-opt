"""Bootstrap failures must never remove a working environment."""

import hashlib
import importlib.machinery
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def dev(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[1] / "scripts/dev"
    loader = importlib.machinery.SourceFileLoader("hop_dev", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    for name in ("HOP_WHEELHOUSE", "AOP_WHEELHOUSE", "HOP_LOCK_VENV", "AOP_LOCK_VENV"):
        monkeypatch.delenv(name, raising=False)
    return module


def test_existing_environment_directory_is_preserved(dev):
    existing = dev.ROOT / ".venv"
    existing.mkdir()
    (existing / "marker").write_text("working")
    with pytest.raises(ValueError, match="Preserving"):
        dev.bootstrap(SimpleNamespace(runtime=False, run_tests=False))
    assert (existing / "marker").read_text() == "working"


def test_missing_wheels_preserve_managed_link(dev):
    old = dev.ROOT / ".venvs/previous"
    old.mkdir(parents=True)
    (old / "pyvenv.cfg").write_text("existing")
    link = dev.ROOT / ".venv"
    link.symlink_to(old)
    with pytest.raises(ValueError, match="wheelhouse"):
        dev.bootstrap(SimpleNamespace(runtime=False, run_tests=False))
    assert link.resolve() == old


def test_external_destination_is_refused(dev, monkeypatch):
    monkeypatch.setenv("HOP_LOCK_VENV", str(dev.ROOT.parent))
    with pytest.raises(ValueError, match="workspace"):
        dev.bootstrap(SimpleNamespace(runtime=False, run_tests=False))


def test_failed_install_preserves_old_environment(dev, monkeypatch):
    old = dev.ROOT / ".venvs/old"
    old.mkdir(parents=True)
    (old / "pyvenv.cfg").write_text("existing")
    link = dev.ROOT / ".venv"
    link.symlink_to(old)
    (dev.ROOT / ".vendor/wheels").mkdir(parents=True)
    (dev.ROOT / "requirements.lock").write_text("# empty fixture\n")
    monkeypatch.setattr(dev.venv.EnvBuilder, "create", lambda *args: None)
    monkeypatch.setattr(dev, "run", lambda *args, **kwargs: 1)
    with pytest.raises(ValueError, match="installation failed"):
        dev.bootstrap(SimpleNamespace(runtime=False, run_tests=False))
    assert link.resolve() == old
    assert list((dev.ROOT / ".venvs").iterdir()) == [old]


# --- scripts/dev check: approved-artifact resolution is fail-closed ---------


def _write_tools_lock(dev, tools):
    (dev.ROOT / "development-tools.lock.json").write_text(
        json.dumps({"schema_version": 2, "platform": "linux-x86_64", "tools": tools})
    )


def _run_check(dev, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["scripts/dev", "check"])
    return dev.main()


def _wheel_pin(wheelhouse, filename, name, version, requirements=()):
    wheel = wheelhouse / filename
    wheel.write_bytes(f"test-wheel-bytes:{filename}".encode())
    return {
        "name": name,
        "kind": "wheel_artifact",
        "module": name,
        "version": version,
        "wheel": filename,
        "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        "requirements": list(requirements),
        "status": "pinned_wheel",
    }


def test_check_blocks_when_type_artifact_is_not_approved(dev, monkeypatch, capsys):
    _write_tools_lock(
        dev,
        {
            "mypy": {
                "name": "mypy",
                "kind": "wheel_artifact",
                "module": "mypy",
                "status": "blocked",
            }
        },
    )
    looked_up = []
    real_which = dev.shutil.which
    monkeypatch.setattr(
        dev.shutil, "which", lambda name: looked_up.append(name) or real_which(name)
    )
    assert _run_check(dev, monkeypatch) == 3
    err = capsys.readouterr().err
    assert "BLOCKED" in err and "mypy" in err
    assert "mypy" not in looked_up


def test_check_never_falls_back_to_a_path_type_checker(dev, monkeypatch, capsys):
    wheelhouse = dev.ROOT / ".vendor/wheels"
    wheelhouse.mkdir(parents=True)
    _write_tools_lock(
        dev, {"mypy": _wheel_pin(wheelhouse, "mypy-1.0-py3-none-any.whl", "mypy", "1.0")}
    )
    looked_up = []
    monkeypatch.setattr(dev.shutil, "which", lambda name: looked_up.append(name) or "/usr/bin/mypy")
    monkeypatch.setattr(dev, "run", lambda *args, **kwargs: pytest.fail("ran an unapproved tool"))
    assert _run_check(dev, monkeypatch) == 3
    assert "environment is missing" in capsys.readouterr().err
    assert "mypy" not in looked_up


def test_check_rejects_a_tampered_approved_artifact(dev, monkeypatch, capsys):
    wheelhouse = dev.ROOT / ".vendor/wheels"
    wheelhouse.mkdir(parents=True)
    pin = _wheel_pin(wheelhouse, "mypy-1.0-py3-none-any.whl", "mypy", "1.0")
    pin["sha256"] = "0" * 64
    _write_tools_lock(dev, {"mypy": pin})
    assert _run_check(dev, monkeypatch) == 3
    assert "integrity check failed" in capsys.readouterr().err


def test_check_rejects_a_missing_transitive_wheel(dev, monkeypatch, capsys):
    wheelhouse = dev.ROOT / ".vendor/wheels"
    wheelhouse.mkdir(parents=True)
    pin = _wheel_pin(
        wheelhouse,
        "mypy-1.0-py3-none-any.whl",
        "mypy",
        "1.0",
        requirements=[
            {
                "name": "mypy_extensions",
                "version": "1.0.0",
                "wheel": "mypy_extensions-1.0.0-py3-none-any.whl",
                "sha256": "1" * 64,
            }
        ],
    )
    _write_tools_lock(dev, {"mypy": pin})
    assert _run_check(dev, monkeypatch) == 3
    assert "mypy_extensions" in capsys.readouterr().err


def test_check_runs_the_type_checker_from_the_approved_environment(dev, monkeypatch):
    wheelhouse = dev.ROOT / ".vendor/wheels"
    wheelhouse.mkdir(parents=True)
    ruff = dev.ROOT / "approved-ruff"
    ruff.write_bytes(b"#!/bin/sh\n")
    ruff.chmod(0o755)
    _write_tools_lock(
        dev,
        {
            "ruff": {
                "name": "ruff",
                "kind": "local_binary",
                "version": "0.0.0",
                "sha256": hashlib.sha256(ruff.read_bytes()).hexdigest(),
                "status": "pinned_local_binary",
            },
            "mypy": _wheel_pin(wheelhouse, "mypy-1.0-py3-none-any.whl", "mypy", "1.0"),
        },
    )
    monkeypatch.setattr(dev.shutil, "which", lambda name: str(ruff) if name == "ruff" else None)
    interpreter = dev.ROOT / ".venvs/tools/bin/python"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_bytes(b"#!/bin/sh\n")
    interpreter.chmod(0o755)
    calls = []
    monkeypatch.setattr(dev, "run", lambda command, **kwargs: calls.append(command) or 0)
    assert _run_check(dev, monkeypatch) == 0
    assert calls[-1] == [str(interpreter), "-m", "mypy", "src/hop"]
    assert all("which" not in command[0] for command in calls)


def test_bootstrap_tools_refuses_without_approved_artifacts(dev, monkeypatch, capsys):
    _write_tools_lock(dev, {})
    monkeypatch.setattr(sys, "argv", ["scripts/dev", "bootstrap-tools"])
    assert dev.main() == 3
    assert "BLOCKED" in capsys.readouterr().err
    assert not (dev.ROOT / ".venvs").exists()
