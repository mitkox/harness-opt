"""M2 model/skill/tool/resource observation helpers (AOP-013/015).

Evidence only: skill_loaded != skill_helped; causal/marginal utility is M7.
All free-text fields are redacted and truncated; large payloads become
content-addressed artifacts referenced by digest, never inline blobs or
metric labels.
"""
from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass, field

from .redaction import digest_of, sanitize_attributes

# Metric provenance taxonomy (BUILD_PLAN §10-11 honesty rule).
MEASURED = "measured"      # directly observed (client span boundaries, rusage)
DERIVED = "derived"        # computed from measured values (durations, rates)
ESTIMATED = "estimated"    # host-level approximation, method recorded
UNAVAILABLE = "unavailable"  # server did not expose it; never fabricated

METRIC_PROVENANCE = {
    "wall_time_s": MEASURED,
    "cpu_time_s": MEASURED,
    "peak_rss_bytes": MEASURED,
    "queue_delay_s": MEASURED,
    "model_seconds": MEASURED,
    "harness_tool_seconds": MEASURED,
    "verifier_seconds": MEASURED,
    "tokens_per_sec": DERIVED,
    "gpu_memory_bytes": ESTIMATED,
    "gpu_utilization": ESTIMATED,
    "ttft_s": UNAVAILABLE,
    "server_prefill_s": UNAVAILABLE,
    "server_decode_s": UNAVAILABLE,
}

MAX_INLINE_CHARS = 2000
MAX_TOOL_OUTPUT_BYTES = 256 * 1024


@dataclass
class SkillCatalogEntry:
    skill_id: str
    version: str
    digest: str
    trigger_description: str
    resources: list[str] = field(default_factory=list)


def skill_catalog_digest(entries: list[SkillCatalogEntry]) -> str:
    return digest_of(*sorted(f"{e.skill_id}@{e.version}:{e.digest}" for e in entries))


@dataclass
class ToolCall:
    tool_name: str
    tool_version: str
    invocation_id: str
    caller_agent: str
    args_digest: str = ""
    args_summary: dict = field(default_factory=dict)
    result_digest: str = ""
    result_summary: dict = field(default_factory=dict)
    start_wall: float = 0.0
    end_wall: float = 0.0
    exit_status: str = ""
    timed_out: bool = False
    retry_count: int = 0
    artifact_refs: list[str] = field(default_factory=list)


def normalize_tool_call(tool_name: str, tool_version: str, invocation_id: str,
                        caller_agent: str, args: dict, result: dict,
                        start_wall: float, end_wall: float,
                        exit_status: str = "ok", timed_out: bool = False,
                        retry_count: int = 0,
                        artifact_refs: list[str] | None = None) -> ToolCall:
    raw_args = str(args)
    raw_result = str(result)
    if len(raw_result.encode()) > MAX_TOOL_OUTPUT_BYTES:
        result_summary = {"truncated": True,
                          "digest": digest_of(raw_result),
                          "note": "oversized output stored as artifact, not inline"}
    else:
        result_summary = sanitize_attributes(result)
    return ToolCall(
        tool_name=tool_name, tool_version=tool_version,
        invocation_id=invocation_id, caller_agent=caller_agent,
        args_digest=digest_of(raw_args),
        args_summary=sanitize_attributes(args),
        result_digest=digest_of(raw_result),
        result_summary=result_summary,
        start_wall=start_wall, end_wall=end_wall,
        exit_status=exit_status, timed_out=timed_out,
        retry_count=retry_count,
        artifact_refs=list(artifact_refs or []))


@dataclass
class InferenceRecord:
    deployment_id: str
    deployment_digest: str
    model_id: str
    tokenizer_id: str = "unknown"
    quantization: str = "unknown"
    server_build: str = "unknown"
    request_id: str = ""
    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    duration_s: float | None = None
    ttft_s: float | None = None  # only if truly observable
    finish_reason: str = "unknown"
    context_limit: int | None = None
    cache_info: dict = field(default_factory=dict)
    error_class: str = ""
    extra: dict = field(default_factory=dict)  # runtime-specific, namespaced

    def to_attributes(self) -> dict:
        attrs = {
            "deployment_id": self.deployment_id,
            "deployment_digest": self.deployment_digest,
            "model_id": self.model_id,
            "tokenizer": self.tokenizer_id,
            "quantization": self.quantization,
            "server_build": self.server_build,
            "request_id": self.request_id,
            "finish_reason": self.finish_reason,
            "provenance": dict(METRIC_PROVENANCE),
        }
        # Token counts: explicit nulls when unavailable (never estimated silently).
        attrs["input_tokens"] = self.input_tokens
        attrs["output_tokens"] = self.output_tokens
        attrs["reasoning_tokens"] = self.reasoning_tokens
        attrs["duration_s"] = self.duration_s
        attrs["ttft_s"] = self.ttft_s
        if self.context_limit is not None:
            attrs["context_limit"] = self.context_limit
        if self.cache_info:
            attrs["cache"] = self.cache_info
        if self.error_class:
            attrs["error_class"] = self.error_class
        attrs.update({f"runtime.{k}": v for k, v in self.extra.items()})
        return sanitize_attributes(attrs)


@dataclass
class ResourceSample:
    wall_s: float = 0.0
    cpu_s: float = 0.0
    peak_rss_bytes: int = 0
    queue_s: float = 0.0
    model_s: float = 0.0
    harness_tool_s: float = 0.0
    verifier_s: float = 0.0
    gpu_mem_bytes: int | None = None
    gpu_util: float | None = None
    provenance: dict = field(default_factory=lambda: dict(METRIC_PROVENANCE))

    def to_dict(self) -> dict:
        return {
            "wall_time_s": round(self.wall_s, 3),
            "cpu_time_s": round(self.cpu_s, 3),
            "peak_rss_bytes": self.peak_rss_bytes,
            "queue_delay_s": round(self.queue_s, 3),
            "model_seconds": round(self.model_s, 3),
            "harness_tool_seconds": round(self.harness_tool_s, 3),
            "verifier_seconds": round(self.verifier_s, 3),
            "gpu_memory_bytes": self.gpu_mem_bytes,
            "gpu_utilization": self.gpu_util,
            "provenance": self.provenance,
            "note": "queue/model/harness/verification kept separate; "
                    "no combined latency metric",
        }


def sample_resources() -> ResourceSample:
    cpu_s, peak_rss = 0.0, 0
    try:
        import resource as _resource
        usage = _resource.getrusage(_resource.RUSAGE_SELF)
        cpu_s = usage.ru_utime + usage.ru_stime
        peak_rss = usage.ru_maxrss * 1024  # Linux kilobytes
    except Exception:
        pass
    gpu_mem, gpu_util = None, None
    for probe in ("rocm-smi", "nvidia-smi"):
        try:
            import shutil as _shutil
            import subprocess as _sp
            if _shutil.which(probe):
                out = _sp.run([probe, "--showmeminfo", "vram"],
                              capture_output=True, text=True, timeout=5)
                if out.returncode == 0 and out.stdout.strip():
                    gpu_mem = -1  # host-level output present but unattributed
                    break
        except Exception:
            continue
    return ResourceSample(cpu_s=cpu_s, peak_rss_bytes=peak_rss,
                          gpu_mem_bytes=gpu_mem, gpu_util=gpu_util)


def file_digest_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def workspace_snapshot_manifest(workspace: str) -> dict:
    manifest = {}
    for dirpath, dirnames, filenames in os.walk(workspace):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            if os.path.islink(full) or "__pycache__" in full:
                continue
            manifest[os.path.relpath(full, workspace)] = file_digest_of(full)
    return manifest


def current_mono() -> float:
    return time.monotonic()
