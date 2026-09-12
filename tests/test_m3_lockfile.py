"""M3 lockfile determinism, floating-reference, and stale-digest tests."""
import json

import pytest

from hop.contracts.profile import ComponentType, Lockfile, Profile
from hop.lockfile import LockVerificationError, build_lockfile, verify_lock
from hop.registry import ComponentRegistry
from tests.m3_helpers import base_profile_dict, register, skill_files


def _resolved(tmp_path):
    from hop import profiles as P

    registry = ComponentRegistry(str(tmp_path / "r"))
    register(registry, name="sys", ctype=ComponentType.SYSTEM_PROMPT,
             files={"system.md": b"# s\n"})
    register(registry, name="alpha", ctype=ComponentType.SKILL, files=skill_files("a"))
    register(registry, name="agent", ctype=ComponentType.AGENT,
             dependencies=["alpha@^1.0.0"], files={"agent.md": b"a\n"})
    register(registry, name="tools", ctype=ComponentType.TOOL_POLICY,
             files={"component.md": b"t\n"})
    register(registry, name="context", ctype=ComponentType.CONTEXT_POLICY,
             files={"component.md": b"c\n"})
    register(registry, name="org", ctype=ComponentType.ORG_POLICY,
             files={"component.md": b"o\n"})
    register(registry, name="overlay", ctype=ComponentType.HARNESS_OVERLAY,
             files={"component.md": b"ov\n"})
    profile = Profile.model_validate(base_profile_dict())
    resolved = P.resolve(profile, registry)
    lock = build_lockfile(resolved, hop_version="0.1.0", compilation_target="pi")
    return registry, resolved, lock


def test_lockfile_has_no_floating_references(tmp_path):
    registry, _, lock = _resolved(tmp_path)
    assert verify_lock(lock, registry) == []
    for comp in lock.components:
        assert comp.version.count(".") == 2
        assert comp.digest.startswith("sha256:")


def test_lock_is_deterministic(tmp_path):
    _, _, lock1 = _resolved(tmp_path)
    _, _, lock2 = _resolved(tmp_path)
    assert lock1.lock_digest == lock2.lock_digest
    assert json.dumps(lock1.model_dump(mode="json"), sort_keys=True) == \
        json.dumps(lock2.model_dump(mode="json"), sort_keys=True)


def test_lock_tamper_is_detected(tmp_path):
    _, _, lock = _resolved(tmp_path)
    lock.components[0].version = "9.9.9"
    with pytest.raises(ValueError):
        lock.verify()


def test_stale_digest_detected(tmp_path):
    registry, _, lock = _resolved(tmp_path)
    lock.components[0].digest = "sha256:" + "cd" * 32
    # Re-seal so only the digest-vs-registry comparison can fail.
    lock.lock_digest = lock.compute_lock_digest()
    with pytest.raises(LockVerificationError) as exc:
        verify_lock(lock, registry)
    assert exc.value.code == "stale_digest"


def test_floating_reference_injected_into_lock_is_rejected(tmp_path):
    registry, _, lock = _resolved(tmp_path)
    lock.components[0].version = "^1.0.0"
    lock.lock_digest = lock.compute_lock_digest()
    with pytest.raises(LockVerificationError) as exc:
        verify_lock(lock, registry)
    assert exc.value.code == "floating_reference"


def test_duplicate_component_injected_into_lock_is_rejected(tmp_path):
    registry, _, lock = _resolved(tmp_path)
    lock.components.append(lock.components[0].model_copy())
    lock.lock_digest = lock.compute_lock_digest()
    with pytest.raises(LockVerificationError) as exc:
        verify_lock(lock, registry)
    assert exc.value.code == "duplicate_component"


def test_target_and_compiler_affect_lock_identity(tmp_path):
    _, resolved, lock_pi = _resolved(tmp_path)
    lock_prime = build_lockfile(resolved, hop_version="0.1.0",
                                compilation_target="prime",
                                compiler_version="other-compiler")
    assert lock_pi.lock_digest != lock_prime.lock_digest


def test_lock_roundtrips_through_json(tmp_path):
    _, _, lock = _resolved(tmp_path)
    restored = Lockfile.model_validate(json.loads(lock.model_dump_json()))
    restored.verify()
    assert restored.lock_digest == lock.lock_digest
