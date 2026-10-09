"""Negative/adversarial tests for the worker egress broker (ADR-012 / WP-1.4d).

Host-runnable tests cover the broker policy, the relay transport, and the Pi
adapter wiring. The namespace test runs in the Cube sandbox; when bwrap cannot
create a namespace it asserts the explicit unenforced report rather than
skipping, because a green suite must never mean "the boundary was not tested".
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from hop.contracts.model import LocalEndpoint, ModelDeployment, ModelFamily, ModelStatus
from hop.harnesses.broker import Broker
from hop.harnesses.pi_adapter import PiJsonAdapter
from hop.sandbox import SandboxSpec, probe_worker_network_isolation

REPO = Path(__file__).resolve().parents[2]
RELAY = REPO / "src" / "hop" / "harnesses" / "broker_relay.py"


def _deployment(base_url: str, model_id: str = "mitko") -> ModelDeployment:
    return ModelDeployment(
        deployment_id="d1",
        discovery_label="Qwen",
        family=ModelFamily.QWEN,
        status=ModelStatus.QUALIFIED,
        quantization="Q4_K_M",
        context_length_configured=8192,
        serving_config={"max_tokens": 512},
        endpoint=LocalEndpoint(alias="qwen", base_url=base_url, model_id=model_id),
    )


class _Upstream:
    """Minimal loopback upstream that records the requests it actually saw."""

    def __init__(self, status: int = 200, payload: bytes = b'{"ok":true}', redirect: str = ""):
        self.requests: list[tuple[str, str]] = []
        self.status = status
        self.payload = payload
        self.redirect = redirect
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                return

            def _respond(self):
                outer.requests.append((self.command, self.path))
                if outer.redirect:
                    self.send_response(302)
                    self.send_header("Location", outer.redirect)
                    self.end_headers()
                    return
                self.send_response(outer.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(outer.payload)))
                self.end_headers()
                self.wfile.write(outer.payload)

            do_GET = _respond
            do_POST = _respond

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def _broker(tmp_path: Path, upstream: _Upstream, run_id: str = "run-1"):
    broker = Broker(
        _deployment(upstream.base_url), run_id, "a1", str(tmp_path / f"broker-{run_id}")
    )
    return broker


def _body(model: str = "mitko") -> bytes:
    return json.dumps({"model": model, "messages": [], "stream": False}).encode()


def test_broker_requires_per_run_token(tmp_path):
    upstream = _Upstream()
    try:
        broker = _broker(tmp_path, upstream)
        for headers in ({}, {"authorization": "Bearer wrong"}, {"authorization": "Basic x"}):
            status, _payload, reason = broker.process(
                "POST", "/v1/chat/completions", headers, _body()
            )
            assert status == 401, (headers, status)
            assert reason == "bad_token"
        assert upstream.requests == [], "unauthenticated request must never reach upstream"
    finally:
        upstream.close()


def test_broker_rejects_token_from_another_run(tmp_path):
    upstream = _Upstream()
    try:
        first = _broker(tmp_path, upstream, "run-a")
        second = _broker(tmp_path, upstream, "run-b")
        headers = {"authorization": f"Bearer {second.handle().token}"}
        status, _payload, reason = first.process("POST", "/v1/chat/completions", headers, _body())
        assert (status, reason) == (401, "bad_token")
        assert upstream.requests == []
    finally:
        upstream.close()


def test_broker_rejects_revoked_attempt_token(tmp_path):
    upstream = _Upstream()
    try:
        broker = _broker(tmp_path, upstream)
        token = broker.handle().token
        broker.revoke()
        status, _payload, reason = broker.process(
            "POST", "/v1/chat/completions", {"authorization": f"Bearer {token}"}, _body()
        )
        assert (status, reason) == (401, "bad_token")
    finally:
        upstream.close()


def test_broker_allowlist_refuses_other_requests(tmp_path):
    upstream = _Upstream()
    try:
        broker = _broker(tmp_path, upstream)
        auth = {"authorization": f"Bearer {broker.handle().token}"}
        cases = [
            ("GET", "/v1/chat/completions", "not_allowlisted"),
            ("POST", "/v1/models", "not_allowlisted"),
            ("DELETE", "/v1/models", "not_allowlisted"),
            ("CONNECT", "127.0.0.1:8000", "connect_refused"),
            ("POST", "http://169.254.169.254/v1/chat/completions", "absolute_form"),
            ("GET", "//169.254.169.254/v1/models", "absolute_form"),
        ]
        for method, target, reason in cases:
            status, _payload, got = broker.process(method, target, auth, _body())
            assert got == reason, (method, target, status, got)
        assert upstream.requests == []
    finally:
        upstream.close()


def test_broker_pins_model_id(tmp_path):
    upstream = _Upstream()
    try:
        broker = _broker(tmp_path, upstream)
        auth = {"authorization": f"Bearer {broker.handle().token}"}
        status, _payload, reason = broker.process(
            "POST", "/v1/chat/completions", auth, _body(model="gpt-4")
        )
        assert (status, reason) == (400, "model_mismatch")
        assert upstream.requests == []
        status, _payload, reason = broker.process("POST", "/v1/chat/completions", auth, _body())
        assert status == 200 and reason == ""
        assert upstream.requests == [("POST", "/v1/chat/completions")]
    finally:
        upstream.close()


def test_broker_refuses_redirect(tmp_path):
    upstream = _Upstream(redirect="https://example.invalid/steal")
    try:
        broker = _broker(tmp_path, upstream)
        auth = {"authorization": f"Bearer {broker.handle().token}"}
        status, _payload, reason = broker.process("POST", "/v1/chat/completions", auth, _body())
        assert (status, reason) == (502, "redirect_refused")
        assert upstream.requests == [("POST", "/v1/chat/completions")]
    finally:
        upstream.close()


def test_broker_refuses_non_loopback_deployment(tmp_path):
    with pytest.raises(ValueError):
        Broker(
            _deployment("http://10.1.2.3:8000/v1"),
            "run",
            "a1",
            str(tmp_path / "b"),
        )


def test_broker_writes_audit_line(tmp_path):
    upstream = _Upstream()
    try:
        broker = _broker(tmp_path, upstream)
        auth = {"authorization": f"Bearer {broker.handle().token}"}
        broker.process("POST", "/v1/chat/completions", auth, _body())
        broker.process("GET", "/v1/models", {"authorization": "Bearer nope"}, b"")
        rows = [json.loads(line) for line in Path(broker.audit_path).read_text().splitlines()]
        assert [row["status"] for row in rows] == [200, 401]
        assert rows[0]["run_id"] == "run-1" and rows[0]["model"] == "mitko"
    finally:
        upstream.close()


def test_worker_provider_config_points_only_at_broker(tmp_path):
    upstream = _Upstream()
    try:
        broker = _broker(tmp_path, upstream)
        adapter = PiJsonAdapter(
            deployment=broker.deployment,
            executable="/bin/true",
            spec=SandboxSpec(share_network=False),
            broker=broker,
        )
        config = adapter._local_models_config()
        provider = config["providers"][adapter.provider]
        handle = broker.handle()
        assert provider["baseUrl"] == handle.base_url
        assert provider["apiKey"] == handle.token
        assert "127.0.0.1" in provider["baseUrl"]
        assert upstream.base_url not in json.dumps(config)
    finally:
        upstream.close()


def test_agent_worker_rejects_shared_network_spec(tmp_path):
    from hop import sandbox

    layout = sandbox.prepare_layout(str(tmp_path / "w"))
    with pytest.raises(ValueError, match="must not share the host network"):
        sandbox.spawn_isolated(["/bin/true"], layout, SandboxSpec(share_network=True), timeout_s=5)


def test_pi_adapter_wraps_command_with_relay(tmp_path, monkeypatch):
    upstream = _Upstream()
    captured: dict = {}
    try:
        broker = _broker(tmp_path, upstream)
        adapter = PiJsonAdapter(
            deployment=broker.deployment,
            executable="/bin/true",
            spec=SandboxSpec(share_network=False),
            broker=broker,
        )
        session = adapter.prepare("sha256:" + "ab" * 32, str(tmp_path / "run"), "w1")

        def fake_spawn(cmd, layout, spec, **kwargs):
            captured["cmd"] = list(cmd)
            captured["spec"] = spec
            from hop.sandbox import RunResult

            return RunResult(
                exit_code=0, timed_out=False, cancelled=False, stdout_path="", stderr_path=""
            )

        monkeypatch.setattr("hop.harnesses.pi_adapter.spawn_isolated", fake_spawn)
        adapter.start(
            "do it",
            session,
            str(tmp_path / "ws"),
            str(tmp_path / "out"),
            str(tmp_path / "err"),
            5.0,
            threading.Event(),
        )
        cmd = captured["cmd"]
        assert cmd[2] == str(RELAY)
        assert cmd[3] == broker.handle().socket_path
        assert "--" in cmd and cmd[cmd.index("--") + 1] == "/bin/true"
        assert captured["spec"].share_network is False
    finally:
        upstream.close()


def test_relay_forwards_tcp_to_unix_socket(tmp_path):
    """The in-namespace relay is a dumb, token-free forwarder."""
    unix_path = str(tmp_path / "upstream.sock")
    received: list[bytes] = []
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(unix_path)
    server.listen(1)

    def serve():
        conn, _ = server.accept()
        received.append(conn.recv(64))
        conn.sendall(b"PONG")
        conn.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import socket, sys\n"
        "s = socket.socket()\n"
        "s.connect(('127.0.0.1', int(sys.argv[1])))\n"
        "s.sendall(b'PING')\n"
        "print(s.recv(64).decode())\n"
    )
    # Pick a free loopback port for the relay listener.
    with socket.socket() as probe_socket:
        probe_socket.bind(("127.0.0.1", 0))
        port = probe_socket.getsockname()[1]
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            str(RELAY),
            unix_path,
            str(port),
            "--",
            sys.executable,
            "-I",
            str(probe),
            str(port),
        ],
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "PONG" in result.stdout
    assert received == [b"PING"]
    server.close()


def test_probe_failure_is_infra_error_without_shared_net_fallback():
    from hop.runner import InfraError, require_worker_network_isolation

    with pytest.raises(InfraError) as exc:
        require_worker_network_isolation(
            "pi", {"enforced": False, "reason": "host_network_reachable"}
        )
    assert exc.value.error_class == "worker_network_isolation_unavailable"
    # Non-agent harnesses are not silently covered by this gate.
    report = {"enforced": False, "reason": "not_required"}
    assert require_worker_network_isolation("scripted:noop", report) is report


@pytest.mark.sandbox
def test_worker_network_isolation_enforced_or_explicitly_blocked(tmp_path):
    report = probe_worker_network_isolation(str(tmp_path / "netprobe"))
    if not report["bwrap"]:
        assert report["enforced"] is False and report["reason"] == "bwrap_missing"
        return
    if not report["enforced"]:
        # Present but unusable (or refused) is explicit, not a skip.
        assert report["reason"], report
        return
    assert report["host_reachable"] is False
    assert report["public_reachable"] is False
    assert report["loopback_up"] is True and report["unix_ok"] is True
    assert not [name for name in report["interfaces"] if name not in ("lo", "sit0")]
