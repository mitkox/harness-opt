"""Bootstrap failures must never remove a working environment."""

import hashlib
import importlib.machinery
import importlib.util
import json
import platform
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

EVIDENCED_ENVIRONMENT = (
    sys.version_info[:2] == (3, 14)
    and platform.system() == "Linux"
    and platform.machine() == "x86_64"
    and platform.python_implementation() == "CPython"
)

# The approved offline wheelhouse (pydantic_core, pyyaml, ...) and the pinned
# tool environment are evidenced for CPython 3.14 / Linux x86-64 only. On any
# other interpreter these bootstrap tests must skip with a precise reason
# instead of failing for an environmental cause.
requires_evidenced_environment = pytest.mark.skipif(
    not EVIDENCED_ENVIRONMENT,
    reason=(
        "offline wheelhouse is evidenced for CPython 3.14/Linux x86-64 only; running "
        f"{platform.python_implementation()} {platform.python_version()} on "
        f"{platform.system()} {platform.machine()}"
    ),
)


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


@requires_evidenced_environment
def test_existing_environment_directory_is_preserved(dev):
    existing = dev.ROOT / ".venv"
    existing.mkdir()
    (existing / "marker").write_text("working")
    with pytest.raises(ValueError, match="Preserving"):
        dev.bootstrap(SimpleNamespace(runtime=False, run_tests=False))
    assert (existing / "marker").read_text() == "working"


@requires_evidenced_environment
def test_missing_wheels_preserve_managed_link(dev):
    old = dev.ROOT / ".venvs/previous"
    old.mkdir(parents=True)
    (old / "pyvenv.cfg").write_text("existing")
    link = dev.ROOT / ".venv"
    link.symlink_to(old)
    with pytest.raises(ValueError, match="wheelhouse"):
        dev.bootstrap(SimpleNamespace(runtime=False, run_tests=False))
    assert link.resolve() == old


@requires_evidenced_environment
def test_external_destination_is_refused(dev, monkeypatch):
    monkeypatch.setenv("HOP_LOCK_VENV", str(dev.ROOT.parent))
    with pytest.raises(ValueError, match="workspace"):
        dev.bootstrap(SimpleNamespace(runtime=False, run_tests=False))


@requires_evidenced_environment
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


def _local_environment_pin(dev, name="mypy"):
    interpreter = dev.ROOT / "approved-python"
    interpreter.write_bytes(b"#!/bin/sh\n")
    interpreter.chmod(0o755)
    library = dev.ROOT / "approved-library/module.py"
    library.parent.mkdir(parents=True, exist_ok=True)
    library.write_bytes(b"x = 1\n")
    wheelhouse = dev.ROOT / ".vendor/wheels"
    wheelhouse.mkdir(parents=True, exist_ok=True)
    manifest = wheelhouse / f"{name}-library-manifest.sha256"
    manifest.write_text(f"{hashlib.sha256(library.read_bytes()).hexdigest()}  {library}\n")
    pin = {
        "name": name,
        "kind": "local_environment",
        "module": name,
        "version": "1.0",
        "interpreter": str(interpreter),
        "interpreter_sha256": hashlib.sha256(interpreter.read_bytes()).hexdigest(),
        "manifest": manifest.name,
        "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "status": "pinned_local_environment",
    }
    return pin, interpreter, manifest, library


def _ruff_pin(dev):
    ruff = dev.ROOT / "approved-ruff"
    ruff.write_bytes(b"#!/bin/sh\n")
    ruff.chmod(0o755)
    return {
        "name": "ruff",
        "kind": "local_binary",
        "version": "0.0.0",
        "sha256": hashlib.sha256(ruff.read_bytes()).hexdigest(),
        "status": "pinned_local_binary",
    }, ruff


def test_check_blocks_when_type_artifact_is_not_approved(dev, monkeypatch, capsys):
    _write_tools_lock(
        dev,
        {
            "mypy": {
                "name": "mypy",
                "kind": "local_environment",
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
    pin, _interpreter, manifest, _library = _local_environment_pin(dev)
    manifest.unlink()
    _write_tools_lock(dev, {"mypy": pin})
    looked_up = []
    monkeypatch.setattr(dev.shutil, "which", lambda name: looked_up.append(name) or "/usr/bin/mypy")
    monkeypatch.setattr(dev, "run", lambda *args, **kwargs: pytest.fail("ran an unapproved tool"))
    assert _run_check(dev, monkeypatch) == 3
    assert "manifest is missing" in capsys.readouterr().err
    assert "mypy" not in looked_up


def test_check_rejects_a_tampered_manifest_file(dev, monkeypatch, capsys):
    pin, _interpreter, _manifest, library = _local_environment_pin(dev)
    library.write_bytes(b"tampered\n")
    _write_tools_lock(dev, {"mypy": pin})
    assert _run_check(dev, monkeypatch) == 3
    assert "manifest integrity check failed" in capsys.readouterr().err


def test_check_rejects_a_manifest_pin_mismatch(dev, monkeypatch, capsys):
    pin, _interpreter, manifest, _library = _local_environment_pin(dev)
    manifest.write_text(f"{'0' * 64}  /nonexistent\n")
    _write_tools_lock(dev, {"mypy": pin})
    assert _run_check(dev, monkeypatch) == 3
    assert "manifest does not match the approved pin" in capsys.readouterr().err


def test_check_rejects_a_pinned_interpreter_mismatch(dev, monkeypatch, capsys):
    pin, interpreter, _manifest, _library = _local_environment_pin(dev)
    interpreter.write_bytes(b"#!/bin/sh\necho replaced\n")
    _write_tools_lock(dev, {"mypy": pin})
    assert _run_check(dev, monkeypatch) == 3
    assert "interpreter does not match the approved pin" in capsys.readouterr().err


def test_check_rejects_a_tampered_wheel_artifact(dev):
    wheelhouse = dev.ROOT / ".vendor/wheels"
    wheelhouse.mkdir(parents=True, exist_ok=True)
    pin = _wheel_pin(wheelhouse, "mypy-1.0-py3-none-any.whl", "mypy", "1.0")
    pin["sha256"] = "0" * 64
    with pytest.raises(dev.ToolUnavailable, match="integrity check failed"):
        dev.resolve_approved_tool({"mypy": pin}, "mypy", "wheel_artifact")


def test_check_rejects_a_missing_transitive_wheel(dev):
    wheelhouse = dev.ROOT / ".vendor/wheels"
    wheelhouse.mkdir(parents=True, exist_ok=True)
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
    with pytest.raises(dev.ToolUnavailable, match="mypy_extensions"):
        dev.resolve_approved_tool({"mypy": pin}, "mypy", "wheel_artifact")


def test_check_runs_the_type_checker_from_the_approved_environment(dev, monkeypatch):
    pin, interpreter, _manifest, _library = _local_environment_pin(dev)
    ruff_pin, ruff = _ruff_pin(dev)
    _write_tools_lock(dev, {"ruff": ruff_pin, "mypy": pin})
    monkeypatch.setattr(dev.shutil, "which", lambda name: str(ruff) if name == "ruff" else None)
    calls = []
    monkeypatch.setattr(dev, "run", lambda command, **kwargs: calls.append(command) or 0)
    assert _run_check(dev, monkeypatch) == 0
    assert calls[0] == [str(ruff), "check", "src", "tests", "scripts/dev"]
    assert calls[-1] == [str(interpreter), "-m", "mypy", "--python-version", "3.14", "src/hop"]
    assert all("which" not in command[0] for command in calls)


def test_bootstrap_tools_refuses_without_approved_artifacts(dev, monkeypatch, capsys):
    _write_tools_lock(dev, {})
    monkeypatch.setattr(sys, "argv", ["scripts/dev", "bootstrap-tools"])
    assert dev.main() == 3
    assert "BLOCKED" in capsys.readouterr().err
    assert not (dev.ROOT / ".venvs").exists()


# --- documented toolchain overrides (1.0): hash-verified relocation ----------


def test_mypy_override_with_matching_hash_is_accepted(dev, monkeypatch):
    pin, interpreter, _manifest, _library = _local_environment_pin(dev)
    relocated = dev.ROOT / "relocated-python"
    relocated.write_bytes(interpreter.read_bytes())  # the same approved build
    relocated.chmod(0o755)
    monkeypatch.setenv("HOP_MYPY_PYTHON", str(relocated))
    _write_tools_lock(dev, {"mypy": pin})
    assert dev.resolve_approved_tool(dev.load_tools(), "mypy", "local_environment") == [
        str(relocated.resolve()),
        "-m",
        "mypy",
    ]


def test_mypy_override_with_wrong_hash_is_refused(dev, monkeypatch):
    pin, _interpreter, _manifest, _library = _local_environment_pin(dev)
    impostor = dev.ROOT / "impostor-python"
    impostor.write_bytes(b"#!/bin/sh\necho not the approved mypy\n")
    impostor.chmod(0o755)
    monkeypatch.setenv("HOP_MYPY_PYTHON", str(impostor))
    _write_tools_lock(dev, {"mypy": pin})
    with pytest.raises(dev.ToolUnavailable, match="does not match the approved pin"):
        dev.resolve_approved_tool(dev.load_tools(), "mypy", "local_environment")


def test_missing_pinned_interpreter_names_the_documented_override(dev, monkeypatch):
    pin, interpreter, _manifest, _library = _local_environment_pin(dev)
    interpreter.unlink()
    monkeypatch.delenv("HOP_MYPY_PYTHON", raising=False)
    _write_tools_lock(dev, {"mypy": pin})
    with pytest.raises(dev.ToolUnavailable, match="HOP_MYPY_PYTHON"):
        dev.resolve_approved_tool(dev.load_tools(), "mypy", "local_environment")


def test_mypy_never_accepts_a_path_binary(dev, monkeypatch):
    pin, interpreter, _manifest, _library = _local_environment_pin(dev)
    interpreter.unlink()
    monkeypatch.delenv("HOP_MYPY_PYTHON", raising=False)
    monkeypatch.setattr(dev.shutil, "which", lambda name: "/usr/local/bin/mypy")
    _write_tools_lock(dev, {"mypy": pin})
    with pytest.raises(dev.ToolUnavailable, match="HOP_MYPY_PYTHON"):
        dev.resolve_approved_tool(dev.load_tools(), "mypy", "local_environment")


def test_bootstrap_tools_without_wheel_pins_reports_local_pins(dev, capsys):
    pin, _interpreter, _manifest, _library = _local_environment_pin(dev)
    _write_tools_lock(dev, {"mypy": pin})
    assert dev.bootstrap_tools(SimpleNamespace()) == 0
    out = capsys.readouterr().out
    assert "local pins resolve in place" in out
    assert "HOP_MYPY_PYTHON" in out


def test_wheelhouse_override_ignores_the_retired_aop_variable(dev, monkeypatch):
    legacy = dev.ROOT / "legacy-wheelhouse"
    legacy.mkdir()
    (dev.ROOT / ".vendor/wheels").mkdir(parents=True, exist_ok=True)
    monkeypatch.delenv("HOP_WHEELHOUSE", raising=False)
    monkeypatch.setenv("AOP_WHEELHOUSE", str(legacy))
    assert dev.approved_wheelhouse() == (dev.ROOT / ".vendor/wheels").resolve()


def test_local_environment_executes_the_configured_venv_path(dev, tmp_path, monkeypatch):
    """A venv interpreter symlinks to a base python: execute the venv path.

    RED before the fix: the resolver returned ``Path(interpreter).resolve()``, so
    the base interpreter ran and could not see the venv's site-packages.
    """
    import subprocess
    import venv as venv_module

    env_dir = tmp_path / "tool-venv"
    venv_module.EnvBuilder(with_pip=False, symlinks=True).create(env_dir)
    configured = env_dir / "bin" / "python"
    assert configured.is_symlink()
    resolved = configured.resolve()
    assert resolved != configured
    site = next((env_dir / "lib").glob("python3*/site-packages"))
    (site / "hop_override_probe.py").write_text("VALUE = 'venv-only'\n")

    wheelhouse = dev.ROOT / ".vendor/wheels"
    wheelhouse.mkdir(parents=True, exist_ok=True)
    library = dev.ROOT / "approved-library/module.py"
    library.parent.mkdir(parents=True, exist_ok=True)
    library.write_bytes(b"x = 1\n")
    manifest = wheelhouse / "mypy-library-manifest.sha256"
    manifest.write_text(f"{hashlib.sha256(library.read_bytes()).hexdigest()}  {library}\n")

    pin = {
        "name": "mypy",
        "kind": "local_environment",
        "module": "mypy",
        "version": "1.0",
        "interpreter": str(configured),
        "interpreter_sha256": hashlib.sha256(resolved.read_bytes()).hexdigest(),
        "manifest": manifest.name,
        "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "status": "pinned_local_environment",
    }
    monkeypatch.delenv("HOP_MYPY_PYTHON", raising=False)
    _write_tools_lock(dev, {"mypy": pin})

    argv = dev.resolve_approved_tool(dev.load_tools(), "mypy", "local_environment")
    assert argv == [str(configured), "-m", "mypy"]
    venv_probe = subprocess.run(
        [str(configured), "-c", "import hop_override_probe"], capture_output=True, check=False
    )
    base_probe = subprocess.run(
        [str(resolved), "-c", "import hop_override_probe"], capture_output=True, check=False
    )
    assert venv_probe.returncode == 0, venv_probe.stderr
    assert base_probe.returncode != 0  # the resolved base cannot see the venv


def test_home_expansion_in_the_pinned_interpreter(dev, monkeypatch):
    pin, interpreter, _manifest, _library = _local_environment_pin(dev)
    monkeypatch.setenv("HOME", str(dev.ROOT))
    monkeypatch.delenv("HOP_MYPY_PYTHON", raising=False)
    pin["interpreter"] = "${HOME}/" + interpreter.relative_to(dev.ROOT).as_posix()
    _write_tools_lock(dev, {"mypy": pin})
    assert dev.resolve_approved_tool(dev.load_tools(), "mypy", "local_environment")[0] == str(
        interpreter
    )
