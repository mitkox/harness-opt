"""Inference + sandbox tests (AOP-007, AOP-008). Hermetic stub servers/processes."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from hop import sandbox
from hop.inference import (
    ChatRequest,
    LocalEndpointClient,
    LocalEndpointError,
    UnsupportedParameterError,
)
from hop import policy


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
            raw = ('data: {"choices": [{"delta": {"content": "héllo "}}]}\n\n'
                   'data: {"choices": [{"delta": {"tool_calls": [{"id": "c1"}]}}]}\n\n'
                   'data: [DONE]\n').encode("utf-8")
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
    out = client.complete(ChatRequest(messages=[{"role": "user", "content": "hi"}],
                                      params={"temperature": 0.3}))
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
    chunks = client.stream(ChatRequest(messages=[{"role": "user", "content": "hi"}]),
                           cancel=cancel)
    assert chunks == []


def test_sandbox_isolation_and_env(tmp_path):
    layout = sandbox.prepare_layout(str(tmp_path / "run1"), snapshot_src="")
    assert layout.workspace.startswith(str(tmp_path))
    spec = sandbox.SandboxSpec(cpu_time_s=30, memory_bytes=2**30)
    res = sandbox.spawn_isolated(
        ["bash", "-c", "echo $OPENAI_API_KEY/$HTTP_PROXY/$HOME; pwd"],
        layout, spec,
        extra_env={"OPENAI_API_KEY": "should-be-ignored", "HTTP_PROXY": "x"})
    out = open(res.stdout_path).read()
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
            r=sandbox.spawn_isolated(["sleep", "30"], layout2, spec, cancel=cancel)))
    thread.start()
    time.sleep(0.5)
    cancel.set()
    thread.join(timeout=15)
    assert result["r"].cancelled is True


def test_workspace_rejects_hidden_markers_and_symlinks(tmp_path):
    ws = str(tmp_path / "ws")
    import os
    os.makedirs(ws)
    open(os.path.join(ws, "code.py"), "w").write("x = 1\n")
    os.symlink("/etc/hostname", os.path.join(ws, "evil-link"))
    with pytest.raises(ValueError):
        sandbox.assert_no_hidden_material(ws, ["hidden-tests"])
    os.unlink(os.path.join(ws, "evil-link"))
    sandbox.assert_no_hidden_material(ws, ["hidden-tests"])
    open(os.path.join(ws, "hidden-tests"), "w").write("secret")
    with pytest.raises(ValueError):
        sandbox.assert_no_hidden_material(ws, ["hidden-tests"])
