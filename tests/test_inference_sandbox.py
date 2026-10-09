"""Inference + sandbox tests (AOP-007, AOP-008). Hermetic stub servers/processes."""

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from hop import runner as hop_runner
from hop import sandbox
from hop.inference import (
    ChatRequest,
    LocalEndpointClient,
    LocalEndpointError,
    UnsupportedParameterError,
)


class StubHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        if payload.get("model") != "stub-model":
            self.send_response(404)
            self.end_headers()
            return
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "http://example.com/v1/chat/completions")
            self.end_headers()
            return
        stream = payload.get("stream")
        if stream:
            raw = (
                'data: {"choices": [{"delta": {"content": "héllo "}}]}\n\n'
                'data: {"choices": [{"delta": {"tool_calls": [{"id": "c1"}]}}]}\n\n'
                "data: [DONE]\n"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        else:
            body = json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)


@pytest.fixture()
def stub():
    server = HTTPServer(("127.0.0.1", 0), StubHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_complete_and_stream_unicode_toolcall(stub):
    client = LocalEndpointClient(stub, "stub-model", timeout_s=10)
    out = client.complete(
        ChatRequest(messages=[{"role": "user", "content": "hi"}], params={"temperature": 0.3})
    )
    assert out["choices"][0]["message"]["content"] == "ok"
    chunks = client.stream(ChatRequest(messages=[{"role": "user", "content": "hi"}]))
    assert len(chunks) == 2
    assert "héllo" in json.dumps(chunks[0], ensure_ascii=False)
    assert chunks[1]["choices"][0]["delta"]["tool_calls"][0]["id"] == "c1"


def test_unsupported_params_explicit(stub):
    client = LocalEndpointClient(stub, "stub-model", timeout_s=10)
    with pytest.raises(UnsupportedParameterError):
        client.complete(ChatRequest(messages=[], params={"reasoning_effort": "high"}))


def test_cloud_and_redirect_fail_closed(stub):
    with pytest.raises(ValueError):
        LocalEndpointClient("https://api.openai.com", "x")
    client = LocalEndpointClient(stub, "stub-model", timeout_s=10)
    with pytest.raises(LocalEndpointError):
        client._post("/redirect", {"model": "stub-model"}, 10)


def test_stream_cancellation(stub):
    client = LocalEndpointClient(stub, "stub-model", timeout_s=10)
    cancel = threading.Event()
    cancel.set()
    chunks = client.stream(ChatRequest(messages=[{"role": "user", "content": "hi"}]), cancel=cancel)
    assert chunks == []


def test_sandbox_isolation_and_env(tmp_path):
    layout = sandbox.prepare_layout(str(tmp_path / "run1"), snapshot_src="")
    assert layout.workspace.startswith(str(tmp_path))
    spec = sandbox.SandboxSpec(cpu_time_s=30, memory_bytes=2**30)
    res = sandbox.spawn_isolated(
        ["bash", "-c", "echo $OPENAI_API_KEY/$HTTP_PROXY/$HOME; pwd"],
        layout,
        spec,
        extra_env={"OPENAI_API_KEY": "should-be-ignored", "HTTP_PROXY": "x"},
    )
    out = Path(res.stdout_path).read_text()
    assert res.exit_code == 0
    assert "should-be-ignored" not in out and "/x" not in out
    assert layout.home in out and str(layout.workspace) in out


def test_sandbox_timeout_and_cancel(tmp_path):
    layout = sandbox.prepare_layout(str(tmp_path / "run2"))
    spec = sandbox.SandboxSpec(cpu_time_s=60, memory_bytes=2**30)
    res = sandbox.spawn_isolated(["sleep", "30"], layout, spec, timeout_s=1.0)
    assert res.timed_out is True
    cancel = threading.Event()
    layout2 = sandbox.prepare_layout(str(tmp_path / "run3"))
    result = {}
    thread = threading.Thread(
        target=lambda: result.update(
            r=sandbox.spawn_isolated(["sleep", "30"], layout2, spec, cancel=cancel)
        )
    )
    thread.start()
    time.sleep(0.5)
    cancel.set()
    thread.join(timeout=15)
    assert result["r"].cancelled is True


def test_workspace_rejects_hidden_markers_and_symlinks(tmp_path):
    ws = str(tmp_path / "ws")
    import os

    os.makedirs(ws)
    Path(os.path.join(ws, "code.py")).write_text("x = 1\n")
    os.symlink("/etc/hostname", os.path.join(ws, "evil-link"))
    with pytest.raises(ValueError):
        sandbox.assert_no_hidden_material(ws, ["hidden-tests"])
    os.unlink(os.path.join(ws, "evil-link"))
    sandbox.assert_no_hidden_material(ws, ["hidden-tests"])
    Path(os.path.join(ws, "hidden-tests")).write_text("secret")
    with pytest.raises(ValueError):
        sandbox.assert_no_hidden_material(ws, ["hidden-tests"])


# --- WP-1.6: symlinked interpreter installs (task-40, G8 blocker) -----------


def _fake_symlinked_interpreter(tmp_path, monkeypatch):
    """A venv whose interpreter chain crosses a symlinked install root.

    Mirrors the host layout ``venv/bin/python -> python3 ->
    <installs>/python/latest/bin/python3`` where ``latest`` is itself a symlink.
    """
    import os
    import sys as _sys

    real_base = Path(_sys.base_prefix).resolve()
    installs = tmp_path / "installs" / "python"
    installs.mkdir(parents=True)
    (installs / "3.14.8").symlink_to(real_base, target_is_directory=True)
    (installs / "latest").symlink_to("3.14.8", target_is_directory=True)
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "lib" / "python3.14" / "site-packages").mkdir(parents=True)
    (venv / "bin" / "python").symlink_to("python3")
    (venv / "bin" / "python3").symlink_to(installs / "latest" / "bin" / "python3")
    (venv / "pyvenv.cfg").write_text(
        f"home = {real_base / 'bin'}\ninclude-system-site-packages = false\n"
        f"version = {_sys.version.split()[0]}\n"
    )
    monkeypatch.setattr(_sys, "executable", str(venv / "bin" / "python"))
    monkeypatch.setattr(_sys, "prefix", str(venv))
    monkeypatch.setattr(_sys, "exec_prefix", str(venv))
    monkeypatch.setattr(_sys, "base_prefix", str(real_base))
    monkeypatch.setattr(_sys, "base_exec_prefix", str(real_base))
    assert os.path.realpath(venv / "bin" / "python") == os.path.realpath(
        real_base / "bin" / "python3"
    )
    return venv, installs


def test_interpreter_binds_cover_a_symlinked_install_root(tmp_path, monkeypatch):
    venv, installs = _fake_symlinked_interpreter(tmp_path, monkeypatch)
    binds = dict(sandbox.interpreter_ro_binds())
    assert str(venv) in binds, "the venv prefix must stay bound for site-packages"
    assert str(installs / "latest") in binds, (
        "the symlinked install root must be bound at its own path, or the venv "
        "interpreter symlink dangles inside the namespace"
    )
    covered_paths = set(binds) | set(sandbox.SYSTEM_RO_BINDS)
    real_install = os.path.realpath(installs / "latest")
    assert any(
        real_install == base or real_install.startswith(base + os.sep) for base in covered_paths
    ), "the real install root must stay reachable"
    for link in (venv / "bin" / "python", venv / "bin" / "python3", installs / "latest"):
        target = os.path.realpath(link)
        covered = any(target == base or target.startswith(base + os.sep) for base in covered_paths)
        assert covered, f"{link} -> {target} is not reachable in the namespace"


def test_worker_execs_through_a_symlinked_interpreter_chain(tmp_path, monkeypatch):
    venv, installs = _fake_symlinked_interpreter(tmp_path, monkeypatch)
    runner = tmp_path / "run.sh"
    runner.write_text(
        f'#!/bin/sh\nexec "{venv}/bin/python" -c "import sys; print(\'OK\', sys.prefix)"\n'
    )
    os.chmod(runner, 0o700)
    layout = sandbox.prepare_layout(str(tmp_path / "worker"))
    probe = sandbox.probe_worker_network_isolation(str(tmp_path / "probe"))
    if not probe["enforced"]:
        # Explicit blocked evidence: no namespace here, so no isolation claim.
        assert probe["reason"], probe
        return
    spec = sandbox.SandboxSpec(cpu_time_s=30, memory_bytes=2**30)
    env = sandbox.scrub_worker_env(dict(os.environ))
    base = [(p, p) for p in sandbox.SYSTEM_RO_BINDS] + [(str(runner), str(runner))]
    binds = sandbox.interpreter_ro_binds()
    fixed = sandbox.spawn_confined(
        [str(runner)],
        cwd=str(layout.workspace),
        env=env,
        ro_binds=base + binds,
        rw_binds=[(layout.root, layout.root)],
        spec=spec,
        timeout_s=60.0,
    )
    assert fixed.exit_code == 0, Path(fixed.stderr_path).read_text()[-500:]
    assert "OK" in Path(fixed.stdout_path).read_text()
    # RED: without the symlinked install root bind the same command cannot exec.
    old = [(src, dst) for src, dst in binds if str(installs / "latest") not in src]
    broken = sandbox.spawn_confined(
        [str(runner)],
        cwd=str(layout.workspace),
        env=env,
        ro_binds=base + old,
        rw_binds=[(layout.root, layout.root)],
        spec=spec,
        timeout_s=60.0,
    )
    assert broken.exit_code != 0, "the symlinked install root bind must be required"


def test_spawn_isolated_binds_the_symlinked_install_root(tmp_path, monkeypatch):
    """The assembled worker binds must include the symlinked install root."""
    from hop import sandbox as sandbox_mod

    venv, installs = _fake_symlinked_interpreter(tmp_path, monkeypatch)
    layout = sandbox_mod.prepare_layout(str(tmp_path / "worker"))
    captured: dict = {}

    def fake_spawn(cmd, *, cwd, env, ro_binds, rw_binds, spec, **kwargs):
        captured["ro"] = list(ro_binds)
        return sandbox_mod.RunResult(
            exit_code=0, timed_out=False, cancelled=False, stdout_path="", stderr_path=""
        )

    monkeypatch.setattr(sandbox_mod, "spawn_confined", fake_spawn)
    sandbox_mod.spawn_isolated(
        [str(venv / "bin" / "python"), "-c", "pass"],
        layout,
        sandbox_mod.SandboxSpec(share_network=False),
        timeout_s=5.0,
    )
    destinations = {dst for _src, dst in captured["ro"]}
    assert str(installs / "latest") in destinations
    assert str(venv) in destinations


# --- WP-1.7: harness package install binds (task-42, G8 blocker #2) ----------


def _fake_harness_install(tmp_path):
    """A mise-style Pi layout: .bin/pi -> .mise package -> bundle/cli.js."""
    install = tmp_path / "install"
    version = install / "1.1.0" / "node_modules"
    package = (
        version
        / ".mise"
        / "@scope+pkg@1.1.0"
        / "node_modules"
        / "@scope"
        / "pkg"
        / "dist"
        / "bundle"
    )
    package.mkdir(parents=True)
    bundle = package / "cli.js"
    bundle.write_text("console.log('1.1.0')\n")
    bin_dir = version / ".bin"
    bin_dir.mkdir()
    pi = bin_dir / "pi"
    pi.symlink_to("../.mise/@scope+pkg@1.1.0/node_modules/@scope/pkg/dist/bundle/cli.js")
    (install / "latest").symlink_to("1.1.0", target_is_directory=True)
    return pi, install / "latest" / "node_modules" / ".bin" / "pi", bundle


def test_harness_binds_resolve_the_package_install_root(tmp_path):
    _pi, invoked, bundle = _fake_harness_install(tmp_path)
    binds = dict(hop_runner._harness_ro_binds(str(invoked)))
    roots = [base for base in binds if base.endswith("node_modules")]
    assert roots, f"no node_modules install root bound: {sorted(binds)}"
    covered = [
        base for base in binds if str(bundle) == base or str(bundle).startswith(base + os.sep)
    ]
    assert covered, "the harness bundle must be reachable through the binds"
    assert any(base.endswith("1.1.0/node_modules") for base in binds)
    assert any(base.endswith("latest/node_modules") for base in binds)


def test_worker_reaches_the_harness_bundle_through_the_binds(tmp_path):
    _pi, invoked, bundle = _fake_harness_install(tmp_path)
    layout = sandbox.prepare_layout(str(tmp_path / "worker"))
    probe = sandbox.probe_worker_network_isolation(str(tmp_path / "probe"))
    if not probe["enforced"]:
        assert probe["reason"], probe
        return
    spec = sandbox.SandboxSpec(
        share_network=False, ro_binds=hop_runner._harness_ro_binds(str(invoked))
    )
    found = sandbox.spawn_isolated(
        ["bash", "-c", f"test -f {str(bundle)!r} && echo FOUND || echo MISSING"],
        layout,
        spec,
        timeout_s=30.0,
    )
    assert "FOUND" in Path(found.stdout_path).read_text(), Path(found.stderr_path).read_text()[
        -300:
    ]
    # RED: without the install-root binds the same probe must miss the bundle.
    old = [(src, dst) for src, dst in spec.ro_binds if "node_modules" not in src]
    missing = sandbox.spawn_isolated(
        ["bash", "-c", f"test -f {str(bundle)!r} && echo FOUND || echo MISSING"],
        layout,
        sandbox.SandboxSpec(share_network=False, ro_binds=old),
        timeout_s=30.0,
    )
    assert "MISSING" in Path(missing.stdout_path).read_text()
