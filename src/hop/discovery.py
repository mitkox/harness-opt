"""Actual local discovery: models (llama-server) and harness builds (AOP-002)."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import urllib.request
from dataclasses import dataclass, field

from .contracts.harness import HarnessName
from .contracts.model import LocalEndpoint, ModelDeployment, ModelFamily, ModelStatus
from .inference import _OPENER
from .policy import assert_local_url

PARTIAL_HASH_BYTES = 4 * 1024 * 1024


def http_get_json(url: str, timeout_s: float = 5.0) -> dict:
    assert_local_url(url)
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with _OPENER.open(req, timeout=timeout_s) as resp:
        if resp.status != 200:
            raise ValueError(f"GET {url} -> HTTP {resp.status}")
        return json.loads(resp.read().decode("utf-8"))


def probe_llama_endpoint(alias: str, base_url: str, timeout_s: float = 5.0) -> dict:
    """Probe a llama-server OpenAI-compatible endpoint. Returns raw evidence."""
    base = base_url.rstrip("/")
    health = http_get_json(f"{base}/health", timeout_s)
    models = http_get_json(f"{base}/v1/models", timeout_s)
    try:
        props = http_get_json(f"{base}/props", timeout_s)
    except (OSError, ValueError, RuntimeError) as exc:
        props = {"_unavailable": str(exc)}
    return {"alias": alias, "base_url": base, "health": health, "models": models, "props": props}


def fingerprint_model_file(path: str) -> dict:
    """Partial fingerprint for large weight files (full 85GB hash deferred)."""
    st = os.stat(path)
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        head = fh.read(PARTIAL_HASH_BYTES)
        h.update(head)
        if st.st_size > PARTIAL_HASH_BYTES:
            fh.seek(max(0, st.st_size - PARTIAL_HASH_BYTES))
            h.update(fh.read(PARTIAL_HASH_BYTES))
    return {
        "path": path,
        "size_bytes": st.st_size,
        "mtime_ns": st.st_mtime_ns,
        "partial_sha256_head_tail_4m": h.hexdigest(),
        "note": "partial hash only; full-file hash deferred (multi-GB weights)",
    }


def build_model_deployment(
    alias: str, discovery_label: str, family: ModelFamily, evidence: dict
) -> ModelDeployment:
    props = evidence.get("props", {})
    model_path = props.get("model_path", "") or ""
    fp = fingerprint_model_file(model_path) if model_path and os.path.exists(model_path) else {}
    data = evidence.get("models", {}).get("data", [{}])[0]
    meta = data.get("meta", {}) if isinstance(data, dict) else {}
    n_params = meta.get("n_params", 0) or 0
    return ModelDeployment(
        deployment_id=f"{alias}-{meta.get('ftype', 'unknown')}".replace(" ", "_"),
        discovery_label=discovery_label,
        family=family,
        weight_path=model_path,
        weight_size_bytes=fp.get("size_bytes", 0),
        weight_partial_sha256=fp.get("partial_sha256_head_tail_4m", ""),
        quantization=str(meta.get("ftype", "")),
        server_build=evidence.get("server_build", ""),
        server_command=evidence.get("server_command", ""),
        context_length_configured=int(meta.get("n_ctx", 0) or 0),
        endpoint=LocalEndpoint(
            alias=alias,
            base_url=evidence["base_url"] + "/v1",
            model_id=data.get("id", alias) if isinstance(data, dict) else alias,
        ),
        status=ModelStatus.QUALIFIED,
        evidence=[f"n_params={n_params}", f"weight_size={fp.get('size_bytes', 0)}"],
    )


@dataclass
class HarnessProbe:
    name: HarnessName
    executable: str
    version_args: list[str] = field(default_factory=lambda: ["--version"])


def probe_harness(executable: str, version_args: list[str], timeout_s: float = 15.0) -> dict:
    path = shutil.which(executable)
    if path is None:
        return {"executable": executable, "found": False}
    try:
        proc = subprocess.run(
            [path, *version_args], check=False, capture_output=True, text=True, timeout=timeout_s
        )
        output = (proc.stdout + proc.stderr).strip()
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {"executable": executable, "path": path, "found": True, "error": str(exc)}
    return {
        "executable": executable,
        "path": path,
        "found": True,
        "version_output": output[:2000],
        "exit_code": proc.returncode,
    }


HARNESS_PROBES = [
    HarnessProbe(HarnessName.PI, "pi"),
    HarnessProbe(HarnessName.OPENCODE_V2, "opencode"),
    HarnessProbe(HarnessName.PRIME, "prime", ["--version", "--plain"]),
    HarnessProbe(HarnessName.DEEPSEEK_HARNESS, "dsh"),
]
