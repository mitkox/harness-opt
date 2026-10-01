"""Identity and reproducibility tests (Priority 1).

Every weight shard participates in bundle identity; changing any shard (not
only the first) changes the execution-bundle digest. Harness identity likewise
covers the executable digest and probe evidence, not just a version string.
"""

from copy import deepcopy

from hop.bundle import compile_bundle, harness_build_digest, model_deployment_digest
from hop.contracts.harness import HarnessBuild, HarnessName, HarnessStatus
from hop.contracts.model import (
    LocalEndpoint,
    ModelDeployment,
    ModelFamily,
    ModelStatus,
    WeightShard,
)


def _deployment(shards):
    return ModelDeployment(
        deployment_id="d1",
        discovery_label="Qwen test",
        family=ModelFamily.QWEN,
        status=ModelStatus.QUALIFIED,
        weight_shards=shards,
        weight_total_bytes=sum(s.size_bytes for s in shards),
        quantization="Q4_K_M",
        endpoint=LocalEndpoint(alias="d1", base_url="http://127.0.0.1:8000/v1", model_id="mitko"),
    )


def _shards(n=4):
    return [
        WeightShard(
            path=f"model-0000{i}-of-0000{n}.gguf",
            size_bytes=100 + i,
            partial_sha256_head_tail_4m=f"{i:016x}",
        )
        for i in range(1, n + 1)
    ]


def _harness(exec_sha="a" * 64, probe="sha256:" + "b" * 64):
    build = HarnessBuild(
        harness=HarnessName.PI,
        executable="/home/user/.npm-global/bin/pi",
        version="0.85.1",
        adapter_revision="pi-json-adapter-v1",
        protocol="pi-jsonl-v3",
        status=HarnessStatus.QUALIFIED,
        executable_sha256=exec_sha,
        probe_digest=probe,
    )
    build.harness_digest = harness_build_digest(build)
    return build


def test_changing_any_shard_changes_bundle_identity():
    base = _deployment(_shards(4))
    base_digest = compile_bundle(base, _harness(), "prompt")[0].digest
    for index in range(4):
        mutated_shards = deepcopy(_shards(4))
        mutated_shards[index].partial_sha256_head_tail_4m = "f" * 16
        mutated = _deployment(mutated_shards)
        assert model_deployment_digest(mutated) != model_deployment_digest(base)
        assert compile_bundle(mutated, _harness(), "prompt")[0].digest != base_digest, (
            f"shard {index} change did not propagate to bundle identity"
        )


def test_harness_identity_covers_binary_and_probe():
    base = _deployment(_shards(2))
    h1 = _harness(exec_sha="a" * 64)
    h2 = _harness(exec_sha="c" * 64)
    assert compile_bundle(base, h1, "p")[0].digest != compile_bundle(base, h2, "p")[0].digest
    h3 = _harness(probe="sha256:" + "d" * 64)
    assert compile_bundle(base, h1, "p")[0].digest != compile_bundle(base, h3, "p")[0].digest


def test_output_identity_exposes_digests():
    bundle, source_map = compile_bundle(_deployment(_shards(3)), _harness(), "p")
    assert bundle.model_deployment_digest.startswith("sha256:")
    assert bundle.harness_digest.startswith("sha256:")
    assert source_map["model_deployment_digest"] == bundle.model_deployment_digest
    assert source_map["harness_digest"] == bundle.harness_digest
