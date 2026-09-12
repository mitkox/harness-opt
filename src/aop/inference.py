"""Local inference transport (AOP-007). Registered loopback endpoints only.

No redirects, no cloud fallback. Unsupported parameters are explicit errors.
Streaming (SSE), cancellation, and Unicode/tool-call passthrough covered.
"""
from __future__ import annotations

import io
import json
import threading
import urllib.request
from dataclasses import dataclass, field

from .policy import assert_local_url

# Parameters the local llama-server OpenAI endpoint demonstrably accepts.
SUPPORTED_PARAMS = {"temperature", "top_p", "top_k", "min_p", "max_tokens",
                    "stop", "seed", "stream"}


class UnsupportedParameterError(ValueError):
    pass


class LocalEndpointError(RuntimeError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise LocalEndpointError(f"refusing redirect to {newurl} (fail closed)")


_OPENER = urllib.request.build_opener(_NoRedirect)


@dataclass
class ChatRequest:
    messages: list[dict]
    params: dict = field(default_factory=dict)

    def validate(self) -> None:
        unknown = set(self.params) - SUPPORTED_PARAMS
        if unknown:
            raise UnsupportedParameterError(
                f"unsupported inference parameters (no silent translation): {sorted(unknown)}")


@dataclass
class LocalEndpointClient:
    base_url: str  # e.g. http://127.0.0.1:8000 (without /v1)
    model_id: str
    timeout_s: float = 120.0

    def __post_init__(self):
        assert_local_url(self.base_url)

    def _post(self, path: str, payload: dict, timeout: float) -> urllib.request.addinfourl:
        url = self.base_url.rstrip("/") + path
        assert_local_url(url)
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": "Bearer local"},
            method="POST")
        try:
            return _OPENER.open(req, timeout=timeout)  # noqa: S310 loopback-only
        except LocalEndpointError:
            raise
        except Exception as exc:
            raise LocalEndpointError(f"local inference failed (no fallback): {exc}") from exc

    def complete(self, request: ChatRequest) -> dict:
        request.validate()
        payload = {"model": self.model_id, "messages": request.messages,
                   "stream": False, **request.params}
        with self._post("/v1/chat/completions", payload, self.timeout_s) as resp:
            if resp.status != 200:
                raise LocalEndpointError(f"HTTP {resp.status}")
            return json.loads(resp.read().decode("utf-8"))

    def stream(self, request: ChatRequest, cancel: threading.Event | None = None,
               on_chunk=None) -> list[dict]:
        """SSE stream; returns parsed chunks. Cancellation stops reading."""
        request.validate()
        payload = {"model": self.model_id, "messages": request.messages,
                   "stream": True, "cache_prompt": True, **request.params}
        chunks: list[dict] = []
        with self._post("/v1/chat/completions", payload, self.timeout_s) as resp:
            if resp.status != 200:
                raise LocalEndpointError(f"HTTP {resp.status}")
            buf = io.BytesIO()
            while True:
                if cancel is not None and cancel.is_set():
                    break
                byte = resp.read(1)
                if not byte:
                    break
                buf.write(byte)
                if byte == b"\n":
                    line = buf.getvalue().decode("utf-8", errors="replace").strip()
                    buf = io.BytesIO()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        raise LocalEndpointError("malformed stream chunk (fail closed)")
                    chunks.append(chunk)
                    if on_chunk is not None:
                        on_chunk(chunk)
        return chunks
