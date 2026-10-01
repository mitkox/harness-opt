"""Shared helpers for M3 tests: hermetic registries and component fixtures."""

from __future__ import annotations

import os

from hop.contracts.profile import (
    ComponentRef,
    ComponentType,
    RegistryComponent,
    VariantSelector,
    tree_digest,
)
from hop.registry import ComponentRegistry, compute_record_digest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMPONENTS_DIR = os.path.join(ROOT, "components")


def fresh_registry(tmp_path, populate: bool = True) -> ComponentRegistry:
    registry = ComponentRegistry(str(tmp_path / "registry"))
    if populate:
        from hop.components import import_directory

        import_directory(registry, COMPONENTS_DIR)
    return registry


def register(
    registry: ComponentRegistry,
    *,
    name: str,
    version: str = "1.0.0",
    ctype: ComponentType = ComponentType.SKILL,
    files: dict[str, bytes] | None = None,
    dependencies: list[str] | None = None,
    compatibility: dict | None = None,
    policy_rules: dict | None = None,
    variants: list[dict] | None = None,
    skill_manifest: dict | None = None,
) -> RegistryComponent:
    files = files if files is not None else {f"{name}.md": f"# {name}\n".encode()}
    deps = []
    for item in dependencies or []:
        joined = item if isinstance(item, ComponentRef) else _ref(item)
        deps.append(joined)
    record = RegistryComponent(
        logical_name=name,
        version=version,
        component_type=ctype,
        content_digest=tree_digest(files),
        dependencies=deps,
        compatibility=compatibility or {},
        policy_rules=policy_rules or {},
        variants=variants or [],
        skill_manifest=skill_manifest or {},
        source="test-fixture",
    )
    record.record_digest = compute_record_digest(record)
    return registry.register(record, files)


def _ref(text: str) -> ComponentRef:
    from hop.contracts.profile import parse_ref

    return parse_ref(text)


def skill_files(body: str = "canonical", resource: str = "") -> dict[str, bytes]:
    files = {"canonical/SKILL.md": f"# skill\n\n{body}\n".encode()}
    if resource:
        files[resource] = b"resource\n"
    return files


def variant(name: str, dimension: str, value: str, body: str = "") -> dict:
    return {
        "name": name,
        "selectors": [VariantSelector(dimension=dimension, value=value).model_dump(mode="json")],
        "path": f"variants/{name}",
    }


def base_profile_dict(name: str = "test", **overrides) -> dict:
    data = {
        "apiVersion": "hop/v1",
        "kind": "Profile",
        "metadata": {"name": name, "version": "0.1.0", "workflow": "debugging"},
        "harness": {"name": "pi", "overlay_ref": "overlay@^1.0.0"},
        "system": {"prompt_ref": "sys@^1.0.0"},
        "agents": ["agent@^1.0.0"],
        "skills": ["alpha@^1.0.0"],
        "tools": {"policy_ref": "tools@^1.0.0"},
        "context": {"policy_ref": "context@^1.0.0"},
        "policy": ["org@^1.0.0"],
        "distribution": {"target": "apm"},
    }
    data.update(overrides)
    return data
