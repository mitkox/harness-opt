"""Immutable execution bundle compiler (M1 slice): resolve + digest + refuse drafts."""
from __future__ import annotations

import hashlib
import json

from .contracts.base import sha256_hex
from .contracts.harness import HarnessBuild
from .contracts.model import ModelDeployment
from .policy import POLICY_ID


def compile_bundle(model: ModelDeployment, harness: HarnessBuild, base_prompt: str,
                   skill_variant_ids: list[str] | None = None,
                   inference_config: dict | None = None,
                   sandbox_digest: str = "", hardware_envelope: str = "") -> dict:
    """Returns (ExecutionBundle, source_map). Raises on unresolved inputs."""
    from .contracts.records import ExecutionBundle
    model.require_qualified()
    if harness.status.value != "qualified":
        raise ValueError(f"harness {harness.harness.value} is {harness.status.value}")
    inference_config = inference_config or {}
    source_map = {
        "model_deployment_id": model.deployment_id,
        "harness": f"{harness.harness.value}@{harness.version}",
        "base_prompt_sha256": sha256_hex(base_prompt.encode()),
        "policy_id": POLICY_ID,
    }
    canonical = json.dumps({
        "model": model.model_dump(mode="json"),
        "harness": harness.model_dump(mode="json"),
        "prompt_sha": source_map["base_prompt_sha256"],
        "policy": POLICY_ID,
        "skills": sorted(skill_variant_ids or []),
        "inference": inference_config,
        "sandbox": sandbox_digest,
        "hardware": hardware_envelope,
    }, sort_keys=True)
    digest = "sha256:" + sha256_hex(canonical.encode())
    bundle = ExecutionBundle(
        digest=digest, model_deployment_id=model.deployment_id,
        harness=harness.harness.value, harness_version=harness.version,
        adapter_revision=harness.adapter_revision,
        base_prompt_sha256=source_map["base_prompt_sha256"], policy_id=POLICY_ID,
        skill_variant_ids=sorted(skill_variant_ids or []),
        inference_config_digest=sha256_hex(json.dumps(inference_config,
                                                      sort_keys=True).encode()),
        sandbox_digest=sandbox_digest, hardware_envelope=hardware_envelope,
        source_map=source_map, deployable=True)
    return bundle, source_map
