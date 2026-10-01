"""M3 negative/integrity tests: altered components, stale history, tamper."""

import os

import pytest

from hop.contracts.profile import ComponentType, tree_digest
from hop.registry import ComponentRegistry, RegistryIntegrityError
from hop.resolver import ResolutionError
from tests.m3_helpers import base_profile_dict, register, skill_files


def _seed(registry):
    register(registry, name="sys", ctype=ComponentType.SYSTEM_PROMPT, files={"system.md": b"# s\n"})
    register(registry, name="alpha", ctype=ComponentType.SKILL, files=skill_files("a"))
    register(
        registry,
        name="overlay",
        ctype=ComponentType.HARNESS_OVERLAY,
        files={"component.md": b"o\n"},
    )


def test_altered_registered_component_blocks_resolution(tmp_path):
    from hop import profiles as P
    from hop.contracts.profile import Profile

    registry = ComponentRegistry(str(tmp_path / "r"))
    _seed(registry)
    record = registry.find(name="alpha")[0]
    content = os.path.join(
        str(tmp_path / "r"), "content", record.content_digest.split(":")[1], "canonical", "SKILL.md"
    )
    with open(content, "wb") as fh:
        fh.write(b"tampered\n")
    data = base_profile_dict(agents=[], tools={}, context={}, policy=[])
    with pytest.raises(Exception) as exc:
        P.resolve(Profile.model_validate(data), registry)
    assert getattr(exc.value, "code", "") in ("digest_mismatch", "missing_dependency")
    assert isinstance(exc.value, (ResolutionError, RegistryIntegrityError))


def test_historical_profile_unavailable_after_component_removed(tmp_path):
    from hop import profiles as P
    from hop.contracts.profile import Profile

    registry = ComponentRegistry(str(tmp_path / "r"))
    _seed(registry)
    profile = Profile.model_validate(base_profile_dict(agents=[], tools={}, context={}, policy=[]))
    resolved = P.resolve(profile, registry)
    from hop.lockfile import build_lockfile

    lock = build_lockfile(resolved, hop_version="0.1.0", compilation_target="pi")
    P.persist_resolved(resolved, lock, home=str(tmp_path))
    # A fresh registry without the historical component cannot materialize it.
    empty = ComponentRegistry(str(tmp_path / "empty"))
    with pytest.raises(P.ProfileError) as exc:
        P.materialize(resolved.profile_digest, empty, home=str(tmp_path))
    assert exc.value.code == "historical_component_unavailable"


def test_altered_compiled_output_changes_digest(tmp_path):
    from hop import profiles as P
    from hop.contracts.profile import Profile

    registry = ComponentRegistry(str(tmp_path / "r"))
    _seed(registry)
    profile = Profile.model_validate(base_profile_dict(agents=[], tools={}, context={}, policy=[]))
    resolved = P.resolve(profile, registry)
    from hop.compiler import get_target
    from hop.lockfile import build_lockfile

    lock = build_lockfile(resolved, hop_version="0.1.0", compilation_target="pi")
    artifact, files = get_target("pi").compile(profile, resolved, registry, lock)
    assert tree_digest(files) == artifact.artifact_digest
    files["system-prompt.md"] = files["system-prompt.md"] + b"\nmalicious\n"
    assert tree_digest(files) != artifact.artifact_digest


def test_historical_lock_survives_relock(tmp_path):
    from hop import profiles as P
    from hop.contracts.profile import Profile
    from hop.lockfile import build_lockfile

    registry = ComponentRegistry(str(tmp_path / "r"))
    _seed(registry)
    profile = Profile.model_validate(base_profile_dict(agents=[], tools={}, context={}, policy=[]))
    res1 = P.resolve(profile, registry)
    lock1 = build_lockfile(res1, hop_version="0.1.0", compilation_target="pi")
    P.persist_resolved(res1, lock1, home=str(tmp_path))
    # New compatible version; re-resolution must produce a *different* lock.
    register(
        registry,
        name="alpha",
        version="1.5.0",
        ctype=ComponentType.SKILL,
        files=skill_files("new-alpha"),
    )
    res2 = P.resolve(profile, registry)
    lock2 = build_lockfile(res2, hop_version="0.1.0", compilation_target="pi")
    P.persist_resolved(res2, lock2, home=str(tmp_path))
    assert lock1.lock_digest != lock2.lock_digest
    # Exact historical lock remains materializable and distinct.
    first = P.materialize(lock1.lock_digest, registry, home=str(tmp_path))
    second = P.materialize(lock2.lock_digest, registry, home=str(tmp_path))
    assert first["lock_digest"] == lock1.lock_digest
    assert second["lock_digest"] == lock2.lock_digest
    assert first["artifact_digest"] != second["artifact_digest"]
    # Profile-digest lookup resolves to the most recent lock.
    latest = P.materialize(res1.profile_digest, registry, home=str(tmp_path))
    assert latest["lock_digest"] == lock2.lock_digest


def test_resolution_digest_tracks_component_content_but_profile_digest_does_not(tmp_path):
    from hop import profiles as P
    from hop.contracts.profile import Profile

    registry = ComponentRegistry(str(tmp_path / "r"))
    _seed(registry)
    profile = Profile.model_validate(base_profile_dict(agents=[], tools={}, context={}, policy=[]))
    first = P.resolve(profile, registry)
    register(
        registry,
        name="alpha",
        version="1.5.0",
        ctype=ComponentType.SKILL,
        files=skill_files("changed"),
    )
    second = P.resolve(profile, registry)
    assert first.profile_digest == second.profile_digest  # source unchanged
    assert first.resolution_digest != second.resolution_digest  # content changed


def test_unsupported_api_version_is_rejected(tmp_path):
    from hop import profiles as P

    registry = ComponentRegistry(str(tmp_path / "r"))
    _seed(registry)
    data = base_profile_dict()
    data["apiVersion"] = "hop/v999"
    path = tmp_path / "bad-api.yaml"
    import yaml

    with open(path, "w") as fh:
        yaml.safe_dump(data, fh)
    result = P.validate_profile(str(path), registry)
    assert result["valid"] is False
    assert result["code"] == "unsupported_api_version"


def test_missing_dependency_reported_by_code(tmp_path):
    from hop import profiles as P
    from hop.contracts.profile import Profile

    registry = ComponentRegistry(str(tmp_path / "r"))
    _seed(registry)
    data = base_profile_dict(agents=[], tools={}, context={}, policy=[], skills=["ghost@^1.0.0"])
    with pytest.raises(ResolutionError) as exc:
        P.resolve(Profile.model_validate(data), registry)
    assert exc.value.code == "missing_dependency"
