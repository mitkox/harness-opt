"""Pi adapter + scripted harness tests (AOP-009). Hermetic fake `pi` binary.

Covers local-only endpoint derivation, env-injection rejection, model-identity
attestation, and claim-vs-fact separation.
"""
import json
import os
import stat
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from hop.contracts.base import new_id
from hop.contracts.model import LocalEndpoint, ModelDeployment, ModelFamily, ModelStatus
from hop.harnesses.pi_adapter import PiJsonAdapter, native_to_trajectory_kind
from hop.harnesses.scripted import ScriptedHarness
from hop.inference import ModelIdentityError, verify_served_model
from hop.policy import scrub_worker_env

FAKE_PI = """#!/bin/bash
echo '{"type":"session","version":3,"id":"s1"}'
echo '{"type":"agent_start"}'
if [ "$1" = "hang" ]; then sleep 30; fi
if [ "$1" = "fail" ]; then echo '{"type":"error","error":"boom"}'; exit 1; fi
echo '{"type":"message_end","message":{"role":"assistant","content":[{"type":"text","text":"I fixed it"}]}}'
"""


def _fake_pi(tmp_path):
    path = str(tmp_path / "pi")
    with open(path, "w") as fh:
        fh.write(FAKE_PI)
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return path


def _deployment(base_url="http://127.0.0.1:8000/v1", model_id="mitko"):
    return ModelDeployment(
        deployment_id="d1", discovery_label="Qwen", family=ModelFamily.QWEN,
        status=ModelStatus.QUALIFIED, quantization="Q4_K_M",
        context_length_configured=262144,
        serving_config={"reasoning": True, "max_tokens": 4096},
        endpoint=LocalEndpoint(alias="qwen", base_url=base_url, model_id=model_id))


def test_probe_real_pi():
    caps = PiJsonAdapter().probe()
    assert caps.supports_headless is True
    assert caps.build_version != "unknown"
    assert "0.85" in caps.build_version
    assert caps.executable_sha256
    assert caps.probe_digest.startswith("sha256:")
    # Capability evidence comes from real probes, not just --version.
    assert caps.probe_evidence["help"]["flags"]["--mode"] is True


def test_prepare_isolates_config(tmp_path):
    adapter = PiJsonAdapter(deployment=_deployment(), executable=_fake_pi(tmp_path))
    session = adapter.prepare("sha256:" + "ab" * 32, str(tmp_path / "run"), "w1")
    assert session.extra_env["PI_CODING_AGENT_DIR"].startswith(str(tmp_path))
    assert os.path.exists(os.path.join(session.extra_env["PI_CODING_AGENT_DIR"], "models.json"))


def test_prompt_acceptance_is_not_completion(tmp_path):
    adapter = PiJsonAdapter(deployment=_deployment(), executable=_fake_pi(tmp_path))
    session = adapter.prepare("sha256:" + "ab" * 32, str(tmp_path / "run"), "w1")
    ws = str(tmp_path / "ws")
    os.makedirs(ws)
    out = adapter.start("do thing", session, ws, str(tmp_path / "out.jsonl"),
                        str(tmp_path / "err.log"), 20.0, threading.Event())
    assert out.terminal_status == "exited"
    assert out.agent_claim == "I fixed it"
    assert not hasattr(out, "verdict")


def test_native_mapping_separates_claims():
    etype, auth = native_to_trajectory_kind({"type": "message_end"})
    assert auth == "agent_claim"
    etype2, auth2 = native_to_trajectory_kind({"type": "agent_start"})
    assert auth2 == "harness_observation"


def test_global_home_not_contaminated(tmp_path):
    adapter = PiJsonAdapter(deployment=_deployment(), executable=_fake_pi(tmp_path))
    session = adapter.prepare("sha256:" + "ab" * 32, str(tmp_path / "run"), "w1")
    assert os.path.expanduser("~/.pi") not in session.extra_env["PI_CODING_AGENT_DIR"]


def test_env_endpoint_injection_is_scrubbed_and_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("AOP_PI_BASE_URL", "http://evil.example.com/v1")
    assert "AOP_PI_BASE_URL" not in scrub_worker_env(dict(os.environ))
    adapter = PiJsonAdapter(deployment=_deployment(), executable=_fake_pi(tmp_path))
    session = adapter.prepare("sha256:" + "ab" * 32, str(tmp_path / "run"), "w1")
    cfg = json.load(open(os.path.join(session.extra_env["PI_CODING_AGENT_DIR"], "models.json")))
    base = cfg["providers"]["aop_local"]["baseUrl"]
    assert base == "http://127.0.0.1:8000/v1"
    assert "evil.example" not in json.dumps(cfg)
    model = cfg["providers"]["aop_local"]["models"][0]
    assert model["contextWindow"] == 262144 and model["maxTokens"] == 4096


def test_remote_deployment_endpoint_fails_closed(tmp_path):
    with pytest.raises(ValueError):
        PiJsonAdapter(deployment=_deployment("http://10.1.2.3:8000/v1"),
                      executable=_fake_pi(tmp_path))


class _ModelsHandler(BaseHTTPRequestHandler):
    model_id = "mitko"

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/v1/models":
            body = {"data": [{"id": self.model_id}]}
        elif self.path == "/props":
            body = {"model_path": ""}
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
def stub_models():
    server = HTTPServer(("127.0.0.1", 0), _ModelsHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_served_model_identity_attested(stub_models):
    dep = _deployment(base_url=stub_models + "/v1", model_id="mitko")
    evidence = verify_served_model(dep)
    assert evidence["expected_model_id"] == "mitko"
    assert "mitko" in evidence["served_ids"]


def test_served_model_identity_mismatch_fails(stub_models):
    dep = _deployment(base_url=stub_models + "/v1", model_id="not-served")
    with pytest.raises(ModelIdentityError):
        verify_served_model(dep)


def test_scripted_arms():
    import tempfile
    for behavior, status in [("succeed", "exited"), ("fail", "exited"),
                             ("hang", "timeout"), ("claim_success", "exited"),
                             ("repair", "exited")]:
        h = ScriptedHarness(behavior, delay_s=0.1 if behavior != "hang" else 5.0)
        with tempfile.TemporaryDirectory() as td:
            out = h.start("p", h.prepare("d", td, "w"), td,
                          f"{td}/o", f"{td}/e", 0.5, threading.Event())
        assert out.terminal_status == status, behavior
    h = ScriptedHarness("hang", delay_s=30.0)
    cancel = threading.Event()
    box = {}
    with tempfile.TemporaryDirectory() as td:
        t = threading.Thread(target=lambda: box.update(
            r=h.start("p", h.prepare("d", td, "w"), td, f"{td}/o", f"{td}/e",
                      30.0, cancel)))
        t.start()
        cancel.set()
        t.join(10)
    assert box["r"].terminal_status == "cancelled"
