"""Regenerate the pinned local inventory (AOP-002). Local-only: fails closed off-host."""
from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from aop.contracts.harness import HarnessBuild, HarnessName, HarnessStatus  # noqa: E402
from aop.contracts.model import ModelFamily, WeightShard  # noqa: E402
from aop.discovery import (  # noqa: E402
    HARNESS_PROBES,
    build_model_deployment,
    fingerprint_model_file,
    probe_harness,
    probe_llama_endpoint,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MODEL_TARGETS = [
    ("qwen-flash-next", "http://127.0.0.1:8000", "Qwen/Qwen3.8-Flash-Next", ModelFamily.QWEN),
    ("qwen-27b-abliterated", "http://127.0.0.1:8001", "Qwen/Qwen3.8-27B-abliterated",
     ModelFamily.QWEN),
]
PENDING_LABELS = [
    ("deepseek-v4-flash", "deepseek", "deepseek-ai/DeepSeek-V4-Flash"),
    ("deepseek-v4-1-flash", "deepseek", "deepseek-ai/DeepSeek-V4.1-Flash"),
    ("qwen3-8-flash-next", "qwen", "Qwen/Qwen3.8-Flash-Next"),
    ("qwen3-8-27b", "qwen", "Qwen/Qwen3.8-27B"),
    ("glm-5-3-flash", "glm", "zai-org/GLM-5.3-Flash"),
]

ADAPTER_REVISIONS = {
    HarnessName.PI: "pi-json-adapter-v1",
    HarnessName.PRIME: "unimplemented",
    HarnessName.OPENCODE_V2: "unimplemented",
    HarnessName.DEEPSEEK_HARNESS: "unimplemented",
}

HARNESS_NOTES = {
    HarnessName.PI: ["@earendil-works/pi-coding-agent headless JSONL mode; first adapter."],
    HarnessName.PRIME: ["PATH prime is the Prime Intellect cloud CLI, NOT the Prime Agent "
                        "harness; Prime Agent integration not started (M5)."],
    HarnessName.OPENCODE_V2: ["opencode and opencode2 resolve to the same v2.0.2 binary; "
                              "adapter not started (M5)."],
    HarnessName.DEEPSEEK_HARNESS: ["dsh 0.1.5-rc.1 developer preview; local-provider "
                                   "support unprobed (M5)."],
}


def shard_set(first_path: str) -> list[dict]:
    """llama.cpp split weights: 00001-of-00006 siblings share one deployment."""
    m = re.search(r"^(.*-)(\d+)(-of-\d+\.\w+)$", first_path)
    if not m:
        return []
    out = []
    for path in sorted(glob.glob(f"{m.group(1)}*{m.group(3)}")):
        try:
            out.append(fingerprint_model_file(path))
        except OSError:
            continue
    return out


def pid_for_port(base: str) -> str:
    port = re.search(r":(\d+)$", base).group(1)
    out = os.popen(f"ss -ltnp 2>/dev/null | grep ':{port} ' | head -1").read()
    m = re.search(r"pid=(\d+)", out)
    return m.group(1) if m else "1"


def main() -> None:
    try:
        out = subprocess.run(
            [os.path.expanduser("~/dev/llama.cpp/build/bin/llama-server"), "--version"],
            capture_output=True, text=True, timeout=10,
        )
        server_build = (out.stdout + out.stderr).strip().splitlines()[0][:200]
    except Exception as exc:
        server_build = f"unavailable:{exc}"

    deployments = []
    for alias, base, label, family in MODEL_TARGETS:
        try:
            evidence = probe_llama_endpoint(alias, base)
        except Exception as exc:
            print(f"WARN: {alias} unreachable: {exc}")
            continue
        evidence["server_build"] = server_build
        try:
            with open(f"/proc/{pid_for_port(base)}/cmdline", "rb") as fh:
                evidence["server_command"] = fh.read().decode().replace("\x00", " ")[:2000]
        except Exception:
            evidence["server_command"] = "unavailable"
        dep = build_model_deployment(alias, label, family, evidence)
        template = evidence.get("props", {}).get("chat_template", "")
        if template:
            dep.chat_template_sha256 = hashlib.sha256(template.encode()).hexdigest()
        shards = shard_set(dep.weight_path) or [fingerprint_model_file(dep.weight_path)]
        dep.weight_shards = [WeightShard(
            path=s["path"], size_bytes=s["size_bytes"],
            mtime_ns=s.get("mtime_ns", 0),
            partial_sha256_head_tail_4m=s.get("partial_sha256_head_tail_4m", ""),
            note=s.get("note", ""),
        ) for s in shards]
        dep.weight_total_bytes = sum(s["size_bytes"] for s in shards)
        dep.weight_manifest_digest = "sha256:" + hashlib.sha256(
            json.dumps([s.model_dump(mode="json") for s in dep.weight_shards],
                       sort_keys=True).encode()).hexdigest()
        dep.serving_config = {
            "reasoning": "on" in evidence.get("server_command", ""),
            "context_length": dep.context_length_configured,
        }
        record = dep.model_dump()
        deployments.append(record)

    harnesses = []
    for probe in HARNESS_PROBES:
        result = probe_harness(probe.executable, probe.version_args)
        status = HarnessStatus.DISCOVERED if result.get("found") else HarnessStatus.BLOCKED
        harnesses.append(HarnessBuild(
            harness=probe.name,
            executable=result.get("path", probe.executable),
            version=result.get("version_output", "")[:300],
            adapter_revision=ADAPTER_REVISIONS[probe.name],
            protocol="pi-jsonl-v3" if probe.name == HarnessName.PI else "",
            status=status,
            blocked_reason="" if result.get("found") else "executable not found",
            capabilities={"headless": probe.name == HarnessName.PI},
            notes=HARNESS_NOTES[probe.name],
        ).model_dump())

    os.makedirs(f"{ROOT}/profiles/models", exist_ok=True)
    os.makedirs(f"{ROOT}/profiles/harnesses", exist_ok=True)
    with open(f"{ROOT}/profiles/models/local-inventory.json", "w") as fh:
        json.dump({"schema_version": "0.1", "server_build": server_build,
                   "deployments": deployments}, fh, indent=2)
    with open(f"{ROOT}/profiles/harnesses/local-inventory.json", "w") as fh:
        json.dump({"schema_version": "0.1", "harnesses": harnesses}, fh, indent=2)

    targets = []
    for tid, family, label in PENDING_LABELS:
        qualified = [d["deployment_id"] for d in deployments
                     if d["discovery_label"] == label and d["status"] == "qualified"]
        targets.append({
            "id": tid, "family": family, "source_label": label,
            "status": "qualified" if qualified else "pending_local_discovery",
            "qualified_deployment_ids": qualified,
        })
    with open(f"{ROOT}/profiles/models/targets.json", "w") as fh:
        json.dump({"schema_version": "0.1", "kind": "ModelTargetList",
                   "inference_policy": "local_only", "targets": targets}, fh, indent=2)
    print(f"deployments={len(deployments)} harnesses={len(harnesses)}")


if __name__ == "__main__":
    main()
