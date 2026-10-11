"""Per-run inference broker (ADR-012 / WP-1.4d).

The agent worker runs with ``bwrap --unshare-net`` and therefore has no route to
the host. Inference is reached only through this broker: a control-plane process
listening on a per-run ``AF_UNIX`` socket (mode ``0600``) that is bind-mounted
into the worker namespace. The worker authenticates with a per-attempt bearer
token delivered as the Pi provider ``apiKey``.

The broker:

* starts only after the control plane has attested the served model
  (``verify_served_model``) and targets exactly that loopback endpoint;
* allow-lists ``POST /v1/chat/completions`` and ``GET /v1/models``/``/props``;
* refuses absolute-form request targets, ``CONNECT``, other methods/paths,
  redirects, and a body ``model`` that is not the pinned deployment model;
* writes one audit line per request into the run directory;
* holds no upstream credential and must never be reachable from outside the
  run: the socket lives under the run root and the token is bound to
  ``(run_id, attempt_id)``.

Honest limits (ADR-012): the token is held by the run's own worker, so it
separates runs/attempts rather than protecting against the worker itself; the
boundary is the network and mount namespace plus the broker's fixed upstream.
"""

from __future__ import annotations

import http.client
import json
import os
import secrets
import socket
import socketserver
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlparse

from ..inference import assert_local_url

ALLOWED_REQUESTS = frozenset(
    {("POST", "/v1/chat/completions"), ("GET", "/v1/models"), ("GET", "/props")}
)
MAX_REQUEST_BYTES = 8 * 1024 * 1024
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_HEADER_BYTES = 64 * 1024
DEFAULT_BROKER_PORT = 18321
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


@dataclass
class BrokerHandle:
    """What the worker adapter needs to reach the broker."""

    socket_path: str
    token: str
    port: int
    base_url: str


def _constant_time_equal(left: str, right: str) -> bool:
    return secrets.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


class _UnixServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True


class _Handler(socketserver.BaseRequestHandler):
    def handle(self) -> None:  # pragma: no cover - exercised through Broker.process
        self.server.broker.serve_connection(self.request)  # type: ignore[attr-defined]


class Broker:
    """Trusted inference broker for one run/attempt."""

    def __init__(
        self,
        deployment,
        run_id: str,
        attempt_id: str,
        directory: str,
        *,
        port: int = DEFAULT_BROKER_PORT,
        timeout_s: float = 120.0,
    ):
        if deployment is None or deployment.endpoint is None:
            raise ValueError("broker requires a registered deployment endpoint")
        self.deployment = deployment
        self.run_id = run_id
        self.attempt_id = attempt_id
        self.model_id = deployment.endpoint.model_id
        upstream = assert_local_url(deployment.endpoint.base_url)
        parsed = urlparse(upstream)
        self._upstream_host = parsed.hostname or ""
        self._upstream_port = parsed.port or (443 if parsed.scheme == "https" else 80)
        self._upstream_scheme = parsed.scheme
        self._upstream_path = parsed.path.rstrip("/")
        if self._upstream_scheme not in ("http", "https"):
            raise ValueError(f"refusing non-http(s) upstream: {upstream!r}")
        self.timeout_s = timeout_s
        self.port = port
        self.token: str | None = secrets.token_urlsafe(32)
        self.directory = directory
        os.makedirs(directory, exist_ok=True)
        self.socket_path = os.path.join(directory, "inference.sock")
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)
        self._server = _UnixServer(self.socket_path, _Handler)
        self._server.broker = self  # type: ignore[attr-defined]
        os.chmod(self.socket_path, 0o600)
        self.audit_path = os.path.join(directory, "broker-audit.jsonl")
        self._thread = threading.Thread(
            target=self._server.serve_forever, name=f"hop-broker-{run_id}", daemon=True
        )
        self._started = False

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        if not self._started:
            self._thread.start()
            self._started = True

    def handle(self, base_suffix: str = "") -> BrokerHandle:
        """Handle for the worker; ``base_suffix`` mirrors the registered /v1 shape."""
        assert self.token is not None
        suffix = base_suffix or self._upstream_path
        base_url = f"http://127.0.0.1:{self.port}{suffix}"
        return BrokerHandle(
            socket_path=self.socket_path,
            token=self.token,
            port=self.port,
            base_url=base_url,
        )

    def revoke(self) -> None:
        self.token = None

    def close(self) -> None:
        self.revoke()
        try:
            self._server.shutdown()
        except OSError:  # best-effort teardown
            pass
        try:
            self._server.server_close()
        except OSError:
            pass
        try:
            if os.path.exists(self.socket_path):
                os.unlink(self.socket_path)
        except OSError:
            pass

    # -- policy ------------------------------------------------------------
    def _authorized(self, headers: dict[str, str]) -> bool:
        token = self.token
        if not token:
            return False
        header = headers.get("authorization", "")
        if not header.lower().startswith("bearer "):
            return False
        return _constant_time_equal(header[7:].strip(), token)

    def _upstream_url(self, path: str) -> str:
        return f"{self._upstream_scheme}://{self._upstream_host}:{self._upstream_port}{path}"

    def process(
        self, method: str, target: str, headers: dict[str, str], body: bytes
    ) -> tuple[int, bytes, str]:
        """Apply broker policy and audit every decision."""
        status, payload, reason = self._decide(method, target, headers, body)
        self.audit(method.upper(), urlparse(target).path, status, len(payload), reason)
        return status, payload, reason

    def _decide(
        self, method: str, target: str, headers: dict[str, str], body: bytes
    ) -> tuple[int, bytes, str]:
        """Apply broker policy; returns ``(status, body, reason)``."""
        if target.startswith(("http://", "https://", "//")):
            return 400, b"absolute-form request target refused", "absolute_form"
        parsed = urlparse(target)
        path = parsed.path
        method = method.upper()
        if method == "CONNECT":
            return 405, b"CONNECT refused", "connect_refused"
        if (method, path) not in ALLOWED_REQUESTS:
            return 403, b"request refused by broker policy", "not_allowlisted"
        if not self._authorized(headers):
            return 401, b"unauthorized", "bad_token"
        if len(body) > MAX_REQUEST_BYTES:
            return 413, b"request too large", "request_too_large"

        forward = body
        if method == "POST" and path == "/v1/chat/completions":
            try:
                payload = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return 400, b"malformed json body", "malformed_body"
            if not isinstance(payload, dict):
                return 400, b"malformed json body", "malformed_body"
            if payload.get("model") != self.model_id:
                return 400, b"model is not the pinned deployment model", "model_mismatch"
            forward = json.dumps(payload).encode("utf-8")

        try:
            status, payload_bytes = self._forward(method, path, headers, forward)
        except _UpstreamRefused as exc:
            return 502, str(exc).encode("utf-8"), "upstream_refused"
        except Exception as exc:  # noqa: BLE001 - no fallback, explicit failure
            return 502, f"upstream failure: {exc}".encode(), "upstream_error"
        if status in REDIRECT_STATUSES:
            return 502, b"redirect refused", "redirect_refused"
        return status, payload_bytes, ""

    def _forward(
        self, method: str, path: str, headers: dict[str, str], body: bytes
    ) -> tuple[int, bytes]:
        url = self._upstream_url(path)
        assert_local_url(url)
        connection: http.client.HTTPConnection
        if self._upstream_scheme == "https":
            connection = http.client.HTTPSConnection(
                self._upstream_host, self._upstream_port, timeout=self.timeout_s
            )
        else:
            connection = http.client.HTTPConnection(
                self._upstream_host, self._upstream_port, timeout=self.timeout_s
            )
        send_headers = {"Accept": headers.get("accept", "application/json")}
        if method == "POST":
            send_headers["Content-Type"] = "application/json"
        send_headers["Authorization"] = "Bearer local"
        try:
            # The upstream root is host:port; the worker path already carries the
            # API prefix, so forward the path (never an absolute-form target).
            connection.request(
                method, path, body=body if method == "POST" else None, headers=send_headers
            )
            response = connection.getresponse()
            payload = response.read(MAX_RESPONSE_BYTES + 1)
        finally:
            connection.close()
        if len(payload) > MAX_RESPONSE_BYTES:
            raise _UpstreamRefused("upstream response too large")
        return response.status, payload

    def audit(self, method: str, path: str, status: int, size: int, reason: str) -> None:
        row = {
            "ts": round(time.time(), 3),
            "run_id": self.run_id,
            "attempt_id": self.attempt_id,
            "method": method,
            "path": path,
            "model": self.model_id,
            "status": status,
            "bytes": size,
            "reason": reason,
        }
        try:
            with open(self.audit_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, sort_keys=True) + "\n")
        except OSError:
            pass

    # -- transport ---------------------------------------------------------
    def serve_connection(self, connection: socket.socket) -> None:
        try:
            connection.settimeout(self.timeout_s)
            request = self._read_request(connection)
            if request is None:
                return
            method, target, headers, body = request
            status, payload, _reason = self.process(method, target, headers, body)
            connection.sendall(_response_bytes(status, payload))
        except (OSError, ValueError):
            return
        finally:
            try:
                connection.close()
            except OSError:
                pass

    @staticmethod
    def _read_request(
        connection: socket.socket,
    ) -> tuple[str, str, dict[str, str], bytes] | None:
        buffer = b""
        while b"\r\n\r\n" not in buffer:
            chunk = connection.recv(4096)
            if not chunk:
                return None
            buffer += chunk
            if len(buffer) > MAX_HEADER_BYTES:
                return None
        head, _, rest = buffer.partition(b"\r\n\r\n")
        lines = head.decode("iso-8859-1").split("\r\n")
        if not lines or len(lines[0].split()) < 2:
            return None
        method, target = lines[0].split()[:2]
        headers: dict[str, str] = {}
        for line in lines[1:]:
            if ":" in line:
                key, value = line.split(":", 1)
                headers[key.strip().lower()] = value.strip()
        length = int(headers.get("content-length", "0") or "0")
        if length > MAX_REQUEST_BYTES:
            return method, target, headers, b""
        body = rest
        while len(body) < length:
            chunk = connection.recv(min(65536, length - len(body)))
            if not chunk:
                break
            body += chunk
        return method, target, headers, body[:length]


class _UpstreamRefused(RuntimeError):
    pass


def _response_bytes(status: int, payload: bytes) -> bytes:
    reason = {
        200: "OK",
        400: "Bad Request",
        401: "Unauthorized",
        403: "Forbidden",
        404: "Not Found",
        405: "Method Not Allowed",
        413: "Payload Too Large",
        502: "Bad Gateway",
    }.get(status, "Broker")
    head = (
        f"HTTP/1.1 {status} {reason}\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(payload)}\r\n"
        "Connection: close\r\n\r\n"
    ).encode("iso-8859-1")
    return head + payload
