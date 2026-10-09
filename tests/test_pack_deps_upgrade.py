"""Dependency-upgrade resolution and compatibility for the deps pack (AOP-025).

The pack must resolve upgrade targets only from local artifacts, record their
sha256, and let trusted compatibility tests decide whether the intended
compatibility is preserved. A known-breaking upgrade is only accepted when the
verifier rejects it, and a declared target with no local artifact is reported as
blocked rather than passed.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from hop.evaluation.packs.deps import runner
from hop.evaluation.packs.deps.wheelbuild import build_wheel, source_digest

CASE = (
    Path(__file__).resolve().parents[1]
    / "benchmarks"
    / "deps"
    / "dependency-upgrade"
    / "depdemo-compat"
)


@pytest.fixture(scope="module")
def upgrade_result(tmp_path_factory):
    work = tmp_path_factory.mktemp("deps-upgrade")
    return runner.run_upgrade_case(CASE, work_root=work)


def _by_version(result, version):
    matches = [upgrade for upgrade in result.upgrades if upgrade.to_version == version]
    assert len(matches) == 1, version
    return matches[0]


def test_baseline_from_version_satisfies_the_compatibility_contract(upgrade_result):
    assert upgrade_result.baseline_exit == 0
    assert upgrade_result.baseline_resolution == "built-from-local-source"
    assert upgrade_result.baseline_artifact_sha256.startswith("sha256:")
    assert upgrade_result.baseline_error == ""


def test_compatible_upgrade_passes_with_compatibility_intact(upgrade_result):
    upgrade = _by_version(upgrade_result, "1.1.0")
    assert upgrade.verdict == "pass"
    assert upgrade.error_class == ""
    assert upgrade.compatibility_exit == 0
    # The target came from the prebuilt local approved artifact, hash-recorded.
    assert upgrade.resolution == "prebuilt-local-artifact"
    assert upgrade.artifact_sha256.startswith("sha256:")


def test_breaking_upgrade_is_rejected_by_the_compatibility_verifier(upgrade_result):
    upgrade = _by_version(upgrade_result, "2.0.0")
    # The verifier fails, so the pack correctly rejects the breaking change.
    assert upgrade.compatibility_exit != 0
    assert upgrade.verdict == "pass"
    assert upgrade.error_class == "breaking_upgrade_rejected"
    assert upgrade.resolution == "built-from-local-source"
    assert upgrade.source_digest.startswith("sha256:")
    assert upgrade.artifact_sha256.startswith("sha256:")


def test_missing_local_artifact_is_explicitly_blocked(upgrade_result):
    upgrade = _by_version(upgrade_result, "3.0.0")
    assert upgrade.outcome == "inconclusive"
    assert upgrade.verdict == "error"
    assert upgrade.error_class == "blocked_missing_artifact"
    assert upgrade.resolution == "missing"
    assert upgrade.artifact_sha256 == ""


def test_every_resolved_artifact_is_local_and_hash_recorded(upgrade_result):
    resolved = [
        upgrade
        for upgrade in upgrade_result.upgrades
        if upgrade.resolution in ("prebuilt-local-artifact", "built-from-local-source")
    ]
    assert len(resolved) == 2
    for upgrade in resolved:
        assert upgrade.artifact_sha256.startswith("sha256:")
        assert Path(upgrade.artifact_path).is_absolute()


def test_install_steps_never_consult_an_index(upgrade_result):
    install_steps = [
        step
        for upgrade in upgrade_result.upgrades
        for step in upgrade.steps
        if step["step"] == "install"
    ]
    assert install_steps
    assert all(step["no_index"] is True for step in install_steps)


def test_prebuilt_artifact_hash_is_enforced(tmp_path):
    """A tampered approved artifact fails closed instead of being installed."""
    copy = tmp_path / "depdemo-compat"
    shutil.copytree(CASE, copy)
    wheel = copy / "artifacts" / "depdemo-1.1.0-py3-none-any.whl"
    wheel.write_bytes(wheel.read_bytes() + b"tampered")
    result = runner.run_upgrade_case(copy, work_root=tmp_path / "work")
    upgrade = _by_version(result, "1.1.0")
    assert upgrade.error_class == "artifact_hash_mismatch"
    assert upgrade.resolution == "rejected"
    assert upgrade.verdict == "error"
    assert upgrade.outcome == "inconclusive"


def test_wheel_builder_is_deterministic_and_content_addressed(tmp_path):
    source = CASE / "deps" / "depdemo-1.1.0"
    first = build_wheel(source, "depdemo", "1.1.0", tmp_path / "a")
    second = build_wheel(source, "depdemo", "1.1.0", tmp_path / "b")
    assert first.read_bytes() == second.read_bytes()
    assert source_digest(source).startswith("sha256:")
