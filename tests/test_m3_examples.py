"""M3 committed example profiles/components stay valid and meaningfully differ."""
import os

import pytest

from hop import profiles as P
from tests.m3_helpers import fresh_registry

PROFILE_DIR = os.path.join("examples", "profiles")
NAMES = ("coding", "debugging", "pr-review", "ci-repair", "security-review")


@pytest.fixture()
def registry(tmp_path):
    return fresh_registry(tmp_path)


@pytest.mark.parametrize("name", NAMES)
def test_example_profile_locks_compiles_and_exports(name, registry, tmp_path):
    path = os.path.join(PROFILE_DIR, f"{name}.yaml")
    assert P.validate_profile(path, registry)["valid"] is True
    _, resolved, lock = P.lock_profile(path, registry, home=str(tmp_path))
    assert lock.lock_digest.startswith("sha256:")
    assert all(c.version.count(".") == 2 for c in lock.components)
    artifact, files = P.compile_resolved(P.load_profile(path), resolved, lock,
                                         registry, target="pi")
    assert artifact.artifact_digest.startswith("sha256:")
    assert files
    exported = P.export(path, registry, out_dir=str(tmp_path / "apm"),
                        home=str(tmp_path))
    report = P.verify_exported(str(tmp_path / "apm"), registry=registry,
                               source_profile=path)
    assert report["ok"] is True
    assert report["package_digest"] == exported["package_digest"]


def test_pr_review_is_read_only(registry, tmp_path):
    _, resolved, _ = P.lock_profile(os.path.join(PROFILE_DIR, "pr-review.yaml"),
                                    registry, home=str(tmp_path))
    assert resolved.effective_policy["writes.allowed"]["value"] is False
    tools = resolved.effective_policy["tools.allow"]["value"]
    assert "write" not in tools and "edit" not in tools


def test_ci_repair_allows_controlled_writes(registry, tmp_path):
    _, resolved, _ = P.lock_profile(os.path.join(PROFILE_DIR, "ci-repair.yaml"),
                                    registry, home=str(tmp_path))
    assert resolved.effective_policy["writes.allowed"]["value"] is True


def test_security_review_denies_network_scanning(registry, tmp_path):
    _, resolved, _ = P.lock_profile(
        os.path.join(PROFILE_DIR, "security-review.yaml"), registry,
        home=str(tmp_path))
    assert resolved.effective_policy["network.scanning"]["value"] == "deny"
    assert resolved.effective_policy["network.scanning"]["class"] == "mandatory"


def test_coding_selects_model_family_variant(registry, tmp_path):
    _, resolved, _ = P.lock_profile(os.path.join(PROFILE_DIR, "coding.yaml"),
                                    registry, home=str(tmp_path))
    assert resolved.component("debugging").variant == "qwen"


def test_deepseek_selector_selects_deepseek_variant(registry, tmp_path):
    path = os.path.join(PROFILE_DIR, "debugging.yaml")
    profile = P.load_profile(path)
    resolved = P.resolve(profile, registry, {"model_family": "deepseek"})
    assert resolved.component("debugging").variant == "deepseek"


def test_examples_are_marked_not_optimized(registry):
    for name in NAMES:
        profile = P.load_profile(os.path.join(PROFILE_DIR, f"{name}.yaml"))
        assert any("not optimized" in note for note in profile.metadata.notes), name
