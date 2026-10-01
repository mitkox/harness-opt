"""Bootstrap failures must never remove a working environment."""

import importlib.machinery
import importlib.util
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
