"""M3 deterministic compiler + capability tests."""

import pytest

from hop.compiler import CompilerError, PiCompilerTarget, get_target
from hop.contracts.profile import ComponentType, Profile
from hop.lockfile import build_lockfile
from hop.registry import ComponentRegistry
from tests.m3_helpers import base_profile_dict, register, skill_files


def _setup(tmp_path):
    from hop import profiles as P

    registry = ComponentRegistry(str(tmp_path / "r"))
    register(
        registry, name="sys", ctype=ComponentType.SYSTEM_PROMPT, files={"system.md": b"# system\n"}
    )
    register(
        registry,
        name="alpha",
        ctype=ComponentType.SKILL,
        files=skill_files("alpha", resource="resources/r.md"),
        skill_manifest={
            "canonical_path": "canonical/SKILL.md",
            "resources": ["resources/r.md"],
            "scripts": [],
        },
    )
    register(
        registry,
        name="agent",
        ctype=ComponentType.AGENT,
        dependencies=["alpha@^1.0.0"],
        files={"agent.md": b"# agent\n"},
    )
    register(
        registry, name="tools", ctype=ComponentType.TOOL_POLICY, files={"component.md": b"tools\n"}
    )
    register(
        registry,
        name="context",
        ctype=ComponentType.CONTEXT_POLICY,
        files={"component.md": b"ctx\n"},
    )
    register(registry, name="org", ctype=ComponentType.ORG_POLICY, files={"component.md": b"org\n"})
    register(
        registry,
        name="overlay",
        ctype=ComponentType.HARNESS_OVERLAY,
        files={"component.md": b"overlay\n"},
    )
    profile = Profile.model_validate(base_profile_dict())
    resolved = P.resolve(profile, registry)
    lock = build_lockfile(resolved, hop_version="0.1.0", compilation_target="pi")
    return registry, profile, resolved, lock


def test_compile_is_deterministic(tmp_path):
    registry, profile, resolved, lock = _setup(tmp_path)
    target = get_target("pi")
    a1, f1 = target.compile(profile, resolved, registry, lock)
    a2, f2 = target.compile(profile, resolved, registry, lock)
    assert a1.artifact_digest == a2.artifact_digest
    assert f1 == f2
    assert a1.files and all(f.digest.startswith("sha256:") for f in a1.files)


def test_changing_component_content_changes_artifact_digest(tmp_path):
    registry, profile, resolved, lock = _setup(tmp_path)
    target = get_target("pi")
    before, _ = target.compile(profile, resolved, registry, lock)
    # Register a new version of the skill; ^1.0.0 now resolves to it.
    register(
        registry,
        name="alpha",
        version="1.1.0",
        ctype=ComponentType.SKILL,
        files={"canonical/SKILL.md": b"# changed\n"},
        skill_manifest={"canonical_path": "canonical/SKILL.md", "resources": [], "scripts": []},
    )
    from hop import profiles as P

    resolved2 = P.resolve(profile, registry)
    lock2 = build_lockfile(resolved2, hop_version="0.1.0", compilation_target="pi")
    after, _ = target.compile(profile, resolved2, registry, lock2)
    assert before.artifact_digest != after.artifact_digest
    assert resolved2.component("alpha").version == "1.1.0"


def test_unsupported_target_fails_closed(tmp_path):
    registry, profile, resolved, lock = _setup(tmp_path)
    with pytest.raises(CompilerError) as exc:
        get_target("prime").compile(profile, resolved, registry, lock)
    assert exc.value.code == "unsupported_target"
    with pytest.raises(CompilerError):
        get_target("does-not-exist")


def test_target_capability_missing_for_hooks(tmp_path):
    from hop import profiles as P

    registry, _, _, _ = _setup(tmp_path)
    register(registry, name="audit-hook", ctype=ComponentType.HOOK, files={"hooks.json": b"{}"})
    profile = Profile.model_validate(base_profile_dict(hooks=["audit-hook@^1.0.0"]))
    resolved = P.resolve(profile, registry)
    lock = build_lockfile(resolved, hop_version="0.1.0", compilation_target="pi")
    with pytest.raises(CompilerError) as exc:
        get_target("pi").compile(profile, resolved, registry, lock)
    assert exc.value.code == "target_capability_missing"


def test_missing_declared_skill_resource_fails(tmp_path):
    from hop import profiles as P

    registry = ComponentRegistry(str(tmp_path / "r"))
    register(registry, name="sys", ctype=ComponentType.SYSTEM_PROMPT, files={"system.md": b"# s\n"})
    register(
        registry,
        name="alpha",
        ctype=ComponentType.SKILL,
        files={"canonical/SKILL.md": b"# a\n"},
        skill_manifest={
            "canonical_path": "canonical/SKILL.md",
            "resources": ["resources/missing.md"],
            "scripts": [],
        },
    )
    register(
        registry,
        name="overlay",
        ctype=ComponentType.HARNESS_OVERLAY,
        files={"component.md": b"o\n"},
    )
    profile = Profile.model_validate(base_profile_dict(agents=[], tools={}, context={}, policy=[]))
    resolved = P.resolve(profile, registry)
    lock = build_lockfile(resolved, hop_version="0.1.0", compilation_target="pi")
    with pytest.raises(CompilerError) as exc:
        get_target("pi").compile(profile, resolved, registry, lock)
    assert exc.value.code == "missing_skill_resource"


class _CollidingTarget(PiCompilerTarget):
    name = "collide"

    def compile_agents(self, profile, resolved, registry):
        return {"same.txt": b"agent"}

    def compile_skills(self, profile, resolved, registry):
        return {"same.txt": b"skill"}


def test_duplicate_resource_collision_fails(tmp_path):
    registry, profile, resolved, lock = _setup(tmp_path)
    with pytest.raises(CompilerError) as exc:
        _CollidingTarget().compile(profile, resolved, registry, lock)
    assert exc.value.code == "duplicate_resource_collision"


def test_executable_script_is_marked_and_content_preserved(tmp_path):
    from hop import profiles as P

    registry = ComponentRegistry(str(tmp_path / "r"))
    register(registry, name="sys", ctype=ComponentType.SYSTEM_PROMPT, files={"system.md": b"# s\n"})
    register(
        registry,
        name="alpha",
        ctype=ComponentType.SKILL,
        files={**skill_files("a"), "scripts/run.sh": b"#!/bin/sh\necho hi\n"},
        skill_manifest={
            "canonical_path": "canonical/SKILL.md",
            "resources": [],
            "scripts": ["scripts/run.sh"],
        },
    )
    register(
        registry,
        name="overlay",
        ctype=ComponentType.HARNESS_OVERLAY,
        files={"component.md": b"o\n"},
    )
    profile = Profile.model_validate(base_profile_dict(agents=[], tools={}, context={}, policy=[]))
    resolved = P.resolve(profile, registry)
    lock = build_lockfile(resolved, hop_version="0.1.0", compilation_target="pi")
    artifact, files = get_target("pi").compile(profile, resolved, registry, lock)
    assert files["skills/alpha/scripts/run.sh"].startswith(b"#!")
    entry = next(f for f in artifact.files if f.path.endswith("run.sh"))
    assert entry.executable is True


def test_selected_variant_content_is_compiled(tmp_path):
    from hop import profiles as P

    registry = ComponentRegistry(str(tmp_path / "r"))
    register(registry, name="sys", ctype=ComponentType.SYSTEM_PROMPT, files={"system.md": b"# s\n"})
    register(
        registry,
        name="alpha",
        ctype=ComponentType.SKILL,
        files={**skill_files("canonical"), "variants/qwen/SKILL.md": b"# QWEN VARIANT MARKER\n"},
        variants=[
            {
                "name": "qwen",
                "path": "variants/qwen",
                "selectors": [{"dimension": "model_family", "value": "qwen"}],
            }
        ],
        skill_manifest={"canonical_path": "canonical/SKILL.md", "resources": [], "scripts": []},
    )
    register(
        registry,
        name="overlay",
        ctype=ComponentType.HARNESS_OVERLAY,
        files={"component.md": b"o\n"},
    )
    profile = Profile.model_validate(
        base_profile_dict(
            agents=[],
            tools={},
            context={},
            policy=[],
            variant_selectors=[{"dimension": "model_family", "value": "qwen"}],
        )
    )
    resolved = P.resolve(profile, registry)
    lock = build_lockfile(resolved, hop_version="0.1.0", compilation_target="pi")
    _, files = get_target("pi").compile(profile, resolved, registry, lock)
    assert b"QWEN VARIANT MARKER" in files["skills/alpha/SKILL.md"]
    # The canonical body must not leak into the selected variant output.
    assert b"canonical" not in files["skills/alpha/SKILL.md"]
