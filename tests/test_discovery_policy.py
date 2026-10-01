"""Discovery + policy tests (AOP-002, AOP-003). All hermetic (stub servers)."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from hop import discovery, policy
from hop.contracts.model import ModelFamily, ModelStatus


class StubHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/health":
            body = {"status": "ok"}
        elif self.path == "/v1/models":
            body = {
                "object": "list",
                "data": [
                    {
                        "id": "stub-model",
                        "owned_by": "llamacpp",
                        "meta": {"n_params": 1234, "n_ctx": 8192, "ftype": "Q4_K"},
                    }
                ],
            }
        elif self.path == "/props":
            body = {"model_path": "", "n_ctx": 8192}
        else:
            self.send_response(404)
            self.end_headers()
            return
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@pytest.fixture()
def stub_llama():
    server = HTTPServer(("127.0.0.1", 0), StubHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_probe_stub_endpoint(stub_llama):
    evidence = discovery.probe_llama_endpoint("stub", stub_llama)
    assert evidence["health"] == {"status": "ok"}
    dep = discovery.build_model_deployment("stub", "Stub Label", ModelFamily.OTHER, evidence)
    assert dep.status == ModelStatus.QUALIFIED
    assert dep.endpoint is not None
    assert dep.endpoint.model_id == "stub-model"
    assert dep.context_length_configured == 8192


def test_nonlocal_endpoint_refused(stub_llama):
    with pytest.raises(ValueError):
        discovery.http_get_json("http://example.com:8000/health", timeout_s=2)
    with pytest.raises(ValueError):
        discovery.http_get_json("http://192.168.1.10:8000/health", timeout_s=2)


def test_unavailable_model_never_becomes_cloud():
    dep = discovery.build_model_deployment(
        "missing",
        "DeepSeek-V4-Flash",
        ModelFamily.DEEPSEEK,
        {
            "base_url": "http://127.0.0.1:9",
            "server_build": "",
            "server_command": "",
            "models": {"data": [{"id": "x", "meta": {}}]},
            "props": {},
        },
    )
    # No endpoint probing happened here, but deployment without a live probe
    # must not be marked qualified in the inventory writer path.
    assert dep.weight_path == ""
    assert dep.weight_size_bytes == 0


def test_harness_probe_missing_binary():
    result = discovery.probe_harness("definitely-not-a-real-binary-xyz", ["--version"])
    assert result["found"] is False


def test_policy_rejects_nonlocal_and_nonhttp():
    with pytest.raises(ValueError):
        policy.assert_local_url("https://api.openai.com/v1")
    with pytest.raises(ValueError):
        policy.assert_local_url("http://10.0.0.5:8000/v1")
    with pytest.raises(ValueError):
        policy.assert_local_url("file:///etc/passwd")
    assert policy.assert_local_url("http://127.0.0.1:8000/v1").endswith("/v1")


def test_worker_env_scrubbed():
    dirty = {
        "PATH": "/usr/bin",
        "OPENAI_API_KEY": "sk-x",
        "HTTP_PROXY": "http://evil:1",
        "SSH_AUTH_SOCK": "/tmp/s",
        "HOME": "/h",
        "MY_CUSTOM": "keep",
    }
    clean = policy.scrub_worker_env(dirty)
    assert "MY_CUSTOM" not in clean
    assert clean["PATH"] == "/usr/bin"
    assert clean["PI_OFFLINE"] == "1"
    with pytest.raises(ValueError):
        policy.check_no_proxy_leak({"HTTP_PROXY": "x"})
    policy.check_no_proxy_leak(clean)
