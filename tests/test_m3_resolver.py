"""M3 deterministic resolution, variants, and policy merge tests."""

import pytest

from hop.contracts.profile import ComponentType, Profile
from hop.registry import ComponentRegistry
from hop.resolver import ResolutionError, select_variant
from tests.m3_helpers import base_profile_dict, register, skill_files


def seed(registry: ComponentRegistry) -> None:
    register(
        registry, name="sys", ctype=ComponentType.SYSTEM_PROMPT, files={"system.md": b"# system\n"}
    )
    register(registry, name="alpha", ctype=ComponentType.SKILL, files=skill_files("alpha"))
    register(
        registry,
        name="agent",
        ctype=ComponentType.AGENT,
        dependencies=["alpha@^1.0.0"],
        files={"agent.md": b"# agent\n"},
        compatibility={"harnesses": ["pi"]},
    )
    register(
        registry,
        name="tools",
        ctype=ComponentType.TOOL_POLICY,
        files={"component.md": b"tools\n"},
        policy_rules={"tools.allow": {"value": ["read", "write"], "class": "default"}},
    )
    register(
        registry,
        name="context",
        ctype=ComponentType.CONTEXT_POLICY,
        files={"component.md": b"ctx\n"},
    )
    register(
        registry,
        name="org",
        ctype=ComponentType.ORG_POLICY,
        files={"component.md": b"org\n"},
        policy_rules={"network.egress": {"value": "deny", "class": "mandatory"}},
    )
    register(
        registry,
        name="overlay",
        ctype=ComponentType.HARNESS_OVERLAY,
        files={"component.md": b"overlay\n"},
        compatibility={"harnesses": ["pi"]},
    )


def resolve_dict(registry, data):
    from hop import profiles as P

    return P.resolve(Profile.model_validate(data), registry)


def test_base_resolution_is_complete_and_deterministic(tmp_path):
    registry = ComponentRegistry(str(tmp_path / "r"))
    seed(registry)
    resolved = resolve_dict(registry, base_profile_dict())
    again = resolve_dict(registry, base_profile_dict())
    assert resolved.profile_digest == again.profile_digest
    names = {c.name for c in resolved.components}
    assert {"sys", "agent", "alpha", "tools", "context", "org", "overlay"} <= names
    # Every locked identity is exact and digest-bearing.
    for comp in resolved.components:
        assert comp.version.count(".") == 2
        assert comp.digest.startswith("sha256:")


def test_missing_dependency_fails_closed(tmp_path):
    registry = ComponentRegistry(str(tmp_path / "r"))
    seed(registry)
    with pytest.raises(ResolutionError) as exc:
        resolve_dict(registry, base_profile_dict(skills=["ghost@^1.0.0"]))
    assert exc.value.code == "missing_dependency"


def test_dependency_cycle_detected(tmp_path):
    registry = ComponentRegistry(str(tmp_path / "r"))
    register(registry, name="x", dependencies=["y@^1.0.0"], files={"x.md": b"x"})
    register(registry, name="y", dependencies=["x@^1.0.0"], files={"y.md": b"y"})
    data = base_profile_dict(
        harness={"name": "pi"},
        agents=[],
        system={},
        tools={},
        context={},
        policy=[],
        skills=["x@^1.0.0"],
    )
    with pytest.raises(ResolutionError) as exc:
        resolve_dict(registry, data)
    assert exc.value.code == "dependency_cycle"


def test_version_conflict_detected(tmp_path):
    registry = ComponentRegistry(str(tmp_path / "r"))
    seed(registry)
    with pytest.raises(ResolutionError) as exc:
        resolve_dict(registry, base_profile_dict(skills=["alpha@^2.0.0"]))
    assert exc.value.code == "version_conflict"


def test_ambiguous_component_type_rejected(tmp_path):
    registry = ComponentRegistry(str(tmp_path / "r"))
    register(registry, name="dup", ctype=ComponentType.SKILL, files={"s.md": b"s"})
    register(registry, name="dup", ctype=ComponentType.AGENT, files={"a.md": b"a"})
    data = base_profile_dict(
        harness={"name": "pi"},
        agents=[],
        system={},
        tools={},
        context={},
        policy=["dup@^1.0.0"],
        skills=[],
    )
    with pytest.raises(ResolutionError) as exc:
        resolve_dict(registry, data)
    assert exc.value.code == "ambiguous_component"


def test_unsupported_component_type_rejected(tmp_path):
    registry = ComponentRegistry(str(tmp_path / "r"))
    register(registry, name="alpha", ctype=ComponentType.SKILL, files={"s.md": b"s"})
    with pytest.raises(ResolutionError) as exc:
        resolve_dict(
            registry,
            base_profile_dict(
                harness={"name": "pi"},
                system={"prompt_ref": "alpha@^1.0.0"},
                agents=[],
                skills=[],
                tools={},
                context={},
                policy=[],
            ),
        )
    assert exc.value.code == "unsupported_component_type"


def test_transitive_dependency_is_locked(tmp_path):
    registry = ComponentRegistry(str(tmp_path / "r"))
    register(registry, name="alpha", dependencies=["gamma@^1.0.0"], files=skill_files("alpha"))
    register(registry, name="gamma", files={"gamma.md": b"gamma"})
    data = base_profile_dict(
        harness={"name": "pi"},
        system={},
        agents=[],
        tools={},
        context={},
        policy=[],
        skills=["alpha@^1.0.0"],
    )
    resolved = resolve_dict(registry, data)
    assert "gamma" in {c.name for c in resolved.components}
    assert resolved.component("gamma").reason


def test_variant_selection_prefers_matching_context(tmp_path):
    registry = ComponentRegistry(str(tmp_path / "r"))
    register(
        registry,
        name="alpha",
        files={
            **skill_files("canonical"),
            "variants/qwen/variant.yaml": b"name: qwen\nselectors:\n- dimension: model_family\n  value: qwen\n",
            "variants/qwen/SKILL.md": b"# qwen variant\n",
        },
        variants=[
            {
                "name": "qwen",
                "path": "variants/qwen",
                "selectors": [{"dimension": "model_family", "value": "qwen"}],
            },
        ],
    )
    data = base_profile_dict(
        harness={"name": "pi"},
        system={},
        agents=[],
        tools={},
        context={},
        policy=[],
        skills=["alpha@^1.0.0"],
        variant_selectors=[{"dimension": "model_family", "value": "qwen"}],
    )
    resolved = resolve_dict(registry, data)
    assert resolved.component("alpha").variant == "qwen"
    assert resolved.component("alpha").variant_reason


def test_canonical_used_when_no_variant_matches(tmp_path):
    registry = ComponentRegistry(str(tmp_path / "r"))
    register(
        registry,
        name="alpha",
        files=skill_files("canonical"),
        variants=[
            {
                "name": "qwen",
                "path": "variants/qwen",
                "selectors": [{"dimension": "model_family", "value": "qwen"}],
            },
            {
                "name": "glm",
                "path": "variants/glm",
                "selectors": [{"dimension": "model_family", "value": "glm"}],
            },
        ],
    )
    data = base_profile_dict(
        harness={"name": "pi"},
        system={},
        agents=[],
        tools={},
        context={},
        policy=[],
        skills=["alpha@^1.0.0"],
        variant_selectors=[{"dimension": "model_family", "value": "deepseek"}],
    )
    resolved = resolve_dict(registry, data)
    assert resolved.component("alpha").variant == ""
    assert resolved.component("alpha").variant_reason.startswith("canonical")
    assert len(resolved.excluded_alternatives) == 2


def test_ambiguous_variant_rejected(tmp_path):
    registry = ComponentRegistry(str(tmp_path / "r"))
    register(
        registry,
        name="alpha",
        files=skill_files("canonical"),
        variants=[
            {
                "name": "qwen-a",
                "path": "variants/qwen-a",
                "selectors": [{"dimension": "model_family", "value": "qwen"}],
            },
            {
                "name": "qwen-b",
                "path": "variants/qwen-b",
                "selectors": [{"dimension": "model_family", "value": "qwen"}],
            },
        ],
    )
    data = base_profile_dict(
        harness={"name": "pi"},
        system={},
        agents=[],
        tools={},
        context={},
        policy=[],
        skills=["alpha@^1.0.0"],
        variant_selectors=[{"dimension": "model_family", "value": "qwen"}],
    )
    with pytest.raises(ResolutionError) as exc:
        resolve_dict(registry, data)
    assert exc.value.code == "ambiguous_variant"


def test_select_variant_is_order_independent():
    variants = [
        {"name": "b", "selectors": [{"dimension": "harness", "value": "pi"}]},
        {"name": "a", "selectors": [{"dimension": "harness", "value": "pi"}]},
    ]
    with pytest.raises(ResolutionError):
        select_variant("s", variants, {"harness": "pi"})
    with pytest.raises(ResolutionError):
        select_variant("s", list(reversed(variants)), {"harness": "pi"})


def test_mandatory_policy_cannot_be_weakened(tmp_path):
    registry = ComponentRegistry(str(tmp_path / "r"))
    seed(registry)
    register(
        registry,
        name="bad-tools",
        ctype=ComponentType.TOOL_POLICY,
        files={"component.md": b"bad\n"},
        policy_rules={"network.egress": {"value": "allow", "class": "overridable"}},
    )
    data = base_profile_dict(tools={"policy_ref": "bad-tools@^1.0.0"})
    with pytest.raises(ResolutionError) as exc:
        resolve_dict(registry, data)
    assert exc.value.code == "policy_violation"


def test_forbidden_override_is_rejected(tmp_path):
    registry = ComponentRegistry(str(tmp_path / "r"))
    register(
        registry, name="sys", ctype=ComponentType.SYSTEM_PROMPT, files={"system.md": b"# system\n"}
    )
    register(
        registry,
        name="org",
        ctype=ComponentType.ORG_POLICY,
        files={"component.md": b"org\n"},
        policy_rules={"secrets.x": {"value": "deny", "class": "forbidden_override"}},
    )
    register(
        registry,
        name="overlay",
        ctype=ComponentType.HARNESS_OVERLAY,
        files={"component.md": b"overlay\n"},
        policy_rules={"secrets.x": {"value": "allow", "class": "default"}},
    )
    data = base_profile_dict(
        skills=[], agents=[], tools={}, context={}, policy=["org@^1.0.0"], system={}
    )
    with pytest.raises(ResolutionError) as exc:
        resolve_dict(registry, data)
    assert exc.value.code == "forbidden_override"


def test_additive_policy_merges(tmp_path):
    registry = ComponentRegistry(str(tmp_path / "r"))
    register(
        registry,
        name="overlay",
        ctype=ComponentType.HARNESS_OVERLAY,
        files={"component.md": b"overlay\n"},
        policy_rules={"allow.tools": {"value": ["read"], "class": "additive"}},
    )
    register(
        registry,
        name="tools",
        ctype=ComponentType.TOOL_POLICY,
        files={"component.md": b"tools\n"},
        policy_rules={"allow.tools": {"value": ["write"], "class": "additive"}},
    )
    data = base_profile_dict(
        system={}, agents=[], skills=[], policy=[], context={}, tools={"policy_ref": "tools@^1.0.0"}
    )
    resolved = resolve_dict(registry, data)
    assert resolved.effective_policy["allow.tools"]["value"] == [
        "write",
        "read",
    ] or resolved.effective_policy["allow.tools"]["value"] == ["read", "write"]


def test_resolution_order_independent_of_registration_order(tmp_path):
    r1 = ComponentRegistry(str(tmp_path / "r1"))
    seed(r1)
    r2 = ComponentRegistry(str(tmp_path / "r2"))
    register(
        r2,
        name="overlay",
        ctype=ComponentType.HARNESS_OVERLAY,
        files={"component.md": b"overlay\n"},
        compatibility={"harnesses": ["pi"]},
    )
    register(
        r2,
        name="org",
        ctype=ComponentType.ORG_POLICY,
        files={"component.md": b"org\n"},
        policy_rules={"network.egress": {"value": "deny", "class": "mandatory"}},
    )
    register(r2, name="alpha", ctype=ComponentType.SKILL, files=skill_files("alpha"))
    register(r2, name="sys", ctype=ComponentType.SYSTEM_PROMPT, files={"system.md": b"# system\n"})
    register(
        r2,
        name="agent",
        ctype=ComponentType.AGENT,
        dependencies=["alpha@^1.0.0"],
        files={"agent.md": b"# agent\n"},
        compatibility={"harnesses": ["pi"]},
    )
    register(
        r2, name="context", ctype=ComponentType.CONTEXT_POLICY, files={"component.md": b"ctx\n"}
    )
    register(
        r2,
        name="tools",
        ctype=ComponentType.TOOL_POLICY,
        files={"component.md": b"tools\n"},
        policy_rules={"tools.allow": {"value": ["read", "write"], "class": "default"}},
    )
    a = resolve_dict(r1, base_profile_dict())
    b = resolve_dict(r2, base_profile_dict())
    assert a.profile_digest == b.profile_digest
    assert [c.digest for c in a.components] == [c.digest for c in b.components]
