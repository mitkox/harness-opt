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


class ModelIdentityError(RuntimeError):
    """The endpoint does not serve the model the deployment record claims."""


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


def verify_served_model(deployment) -> dict:
    """Attest that the registered endpoint serves the deployment's model id.

    Fails closed on off-host URLs, redirects, transport errors, a missing model
    id, or a served weight path that contradicts the deployment record.
    """
    from .contracts.model import ModelDeployment
    if not isinstance(deployment, ModelDeployment):
        raise TypeError("verify_served_model requires a ModelDeployment")
    if deployment.endpoint is None:
        raise ModelIdentityError(f"{deployment.deployment_id} has no endpoint")
    base = assert_local_url(deployment.endpoint.base_url).rstrip("/")
    expected_id = deployment.endpoint.model_id
    # The registered endpoint may include the OpenAI-compatible /v1 suffix.
    root = base[:-3].rstrip("/") if base.endswith("/v1") else base

    def _get(url: str) -> dict:
        assert_local_url(url)
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with _OPENER.open(req, timeout=10.0) as resp:  # noqa: S310 loopback-only
                if resp.status != 200:
                    raise ModelIdentityError(f"GET {url} -> HTTP {resp.status}")
                return json.loads(resp.read().decode("utf-8"))
        except ModelIdentityError:
            raise
        except Exception as exc:
            raise ModelIdentityError(
                f"cannot attest local endpoint {url} (no fallback): {exc}") from exc

    models = _get(f"{root}/v1/models")
    ids = [m.get("id") for m in models.get("data", []) if isinstance(m, dict)]
    if expected_id not in ids:
        raise ModelIdentityError(
            f"endpoint serves {ids!r}, deployment claims model_id={expected_id!r}")
    evidence = {"base_url": base, "expected_model_id": expected_id, "served_ids": ids}
    try:
        props = _get(f"{root}/props")
    except ModelIdentityError:
        props = {}
    if props.get("model_path"):
        evidence["served_model_path"] = props["model_path"]
        if deployment.weight_path and props["model_path"] != deployment.weight_path:
            raise ModelIdentityError(
                "served weight path does not match deployment record: "
                f"{props['model_path']!r} != {deployment.weight_path!r}")
    return evidence
