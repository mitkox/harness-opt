"""Component source loading and import into the immutable registry (M3).

A *component source* is a directory with a manifest (``skill.yaml`` for skills,
``component.yaml`` otherwise) plus content. The manifest names the component
type, logical name, version, dependencies, compatibility, and (for policies)
classified rules. The whole directory becomes the component's content; its
digest therefore changes whenever any file or the manifest changes.

Import is deterministic and offline. There is no implicit network lookup.
"""

from __future__ import annotations

import os

import yaml

from .contracts.profile import (
    ComponentRef,
    ComponentType,
    RegistryComponent,
    VariantSelector,
    tree_digest,
)
from .registry import ComponentRegistry, compute_record_digest

SKILL_MANIFEST_NAMES = ("skill.yaml", "skill.yml")
COMPONENT_MANIFEST_NAMES = ("component.yaml", "component.yml")


class ComponentSourceError(ValueError):
    pass


def _read_manifest(directory: str) -> tuple[dict, str]:
    for name in SKILL_MANIFEST_NAMES + COMPONENT_MANIFEST_NAMES:
        path = os.path.join(directory, name)
        if os.path.isfile(path):
            with open(path) as fh:
                data = yaml.safe_load(fh) or {}
            if not isinstance(data, dict):
                raise ComponentSourceError(f"{path} must be a mapping")
            return data, name
    raise ComponentSourceError(
        f"no component manifest in {directory} "
        f"(expected one of {SKILL_MANIFEST_NAMES + COMPONENT_MANIFEST_NAMES})"
    )


def _collect_files(directory: str) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for dirpath, dirnames, filenames in os.walk(directory):
        dirnames[:] = sorted(d for d in dirnames if d not in ("__pycache__", ".git"))
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                raise ComponentSourceError(f"symlink not allowed in component: {full}")
            rel = os.path.relpath(full, directory).replace(os.sep, "/")
            with open(full, "rb") as fh:
                files[rel] = fh.read()
    return files


def _infer_type(directory: str, manifest: dict) -> ComponentType:
    raw = manifest.get("component_type") or manifest.get("type") or ""
    if raw:
        try:
            return ComponentType(raw)
        except ValueError as exc:
            raise ComponentSourceError(f"unsupported component type {raw!r}") from exc
    parent = os.path.basename(os.path.dirname(os.path.abspath(directory)))
    mapping = {
        "prompts": ComponentType.SYSTEM_PROMPT,
        "skills": ComponentType.SKILL,
        "agents": ComponentType.AGENT,
        "tool_policies": ComponentType.TOOL_POLICY,
        "context_policies": ComponentType.CONTEXT_POLICY,
        "org_policies": ComponentType.ORG_POLICY,
        "overlays": ComponentType.HARNESS_OVERLAY,
        "hooks": ComponentType.HOOK,
        "mcp": ComponentType.MCP,
        "resources": ComponentType.RESOURCE,
    }
    if parent in mapping:
        return mapping[parent]
    raise ComponentSourceError(f"cannot infer component type for {directory}; set component_type")


def _parse_dependencies(raw) -> list[ComponentRef]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ComponentSourceError("dependencies must be a list")
    refs: list[ComponentRef] = []
    for item in raw:
        if isinstance(item, str):
            from .contracts.profile import parse_ref

            refs.append(parse_ref(item))
        elif isinstance(item, dict):
            refs.append(
                ComponentRef(
                    name=item["name"],
                    version=str(item.get("version", "*")),
                    type=ComponentType(item["type"]) if item.get("type") else None,
                )
            )
        else:
            raise ComponentSourceError(f"invalid dependency entry {item!r}")
    refs.sort(key=lambda r: (r.name, r.version, r.type.value if r.type else ""))
    return refs


def _parse_variants(directory: str) -> list[dict]:
    variants_dir = os.path.join(directory, "variants")
    if not os.path.isdir(variants_dir):
        return []
    variants: list[dict] = []
    for name in sorted(os.listdir(variants_dir)):
        vdir = os.path.join(variants_dir, name)
        if not os.path.isdir(vdir):
            continue
        manifest_path = os.path.join(vdir, "variant.yaml")
        if not os.path.isfile(manifest_path):
            raise ComponentSourceError(
                f"variant {name!r} has no variant.yaml (selectors are required)"
            )
        with open(manifest_path) as fh:
            manifest = yaml.safe_load(fh) or {}
        selectors = manifest.get("selectors")
        if not selectors or not isinstance(selectors, list):
            raise ComponentSourceError(f"variant {name!r} must declare non-empty selectors")
        parsed = []
        for selector in selectors:
            try:
                parsed.append(
                    VariantSelector(dimension=selector["dimension"], value=str(selector["value"]))
                )
            except (KeyError, ValueError) as exc:
                raise ComponentSourceError(
                    f"invalid selector in variant {name!r}: {selector!r}"
                ) from exc
        variants.append(
            {
                "name": name,
                "selectors": [s.model_dump(mode="json") for s in parsed],
                "path": f"variants/{name}",
            }
        )
    names = [v["name"] for v in variants]
    if len(names) != len(set(names)):
        raise ComponentSourceError("duplicate variant names")
    return variants


def _parse_policy_rules(manifest: dict) -> dict[str, dict]:
    raw = manifest.get("rules") or {}
    if not isinstance(raw, dict):
        raise ComponentSourceError("policy 'rules' must be a mapping")
    valid = {"mandatory", "default", "overridable", "additive", "forbidden_override"}
    rules: dict[str, dict] = {}
    for key, spec in raw.items():
        if not isinstance(spec, dict) or "value" not in spec:
            raise ComponentSourceError(f"policy rule {key!r} needs a value")
        classification = str(spec.get("class", "default"))
        if classification not in valid:
            raise ComponentSourceError(f"policy rule {key!r} has invalid class {classification!r}")
        rules[key] = {"value": spec["value"], "class": classification}
    return rules


def load_component_source(
    directory: str, component_type: ComponentType | None = None
) -> tuple[RegistryComponent, dict[str, bytes]]:
    """Load and normalize one component directory (no registry write)."""
    manifest, manifest_name = _read_manifest(directory)
    ctype = component_type or _infer_type(directory, manifest)
    name = (
        manifest.get("logical_name")
        or manifest.get("name")
        or os.path.basename(os.path.abspath(directory))
    )
    version = str(manifest.get("version", ""))
    if not version:
        raise ComponentSourceError(f"{directory} manifest missing 'version'")

    files = _collect_files(directory)
    content_digest = tree_digest(files)
    dependencies = _parse_dependencies(manifest.get("dependencies"))
    compatibility = manifest.get("compatibility") or {}
    if not isinstance(compatibility, dict):
        raise ComponentSourceError("compatibility must be a mapping")
    policy_rules = _parse_policy_rules(manifest)
    variants = _parse_variants(directory) if ctype == ComponentType.SKILL else []
    skill_manifest = {}
    if ctype == ComponentType.SKILL:
        skill_manifest = _normalize_skill_manifest(manifest, directory, variants)

    record = RegistryComponent(
        logical_name=name,
        version=version,
        component_type=ctype,
        content_digest=content_digest,
        metadata={
            "description": manifest.get("description", ""),
            "purpose": manifest.get("purpose", ""),
            "manifest": manifest_name,
        },
        dependencies=dependencies,
        compatibility=compatibility,
        source=os.path.abspath(directory),
        source_revision="",
        policy_rules=policy_rules,
        variants=variants,
        skill_manifest=skill_manifest,
    )
    record.record_digest = compute_record_digest(record)
    return record, files


def _normalize_skill_manifest(manifest: dict, directory: str, variants: list[dict]) -> dict:
    canonical = None
    for candidate in ("canonical/SKILL.md", "SKILL.md"):
        if os.path.isfile(os.path.join(directory, candidate)):
            canonical = candidate
            break
    if canonical is None:
        raise ComponentSourceError(f"skill {directory} has no canonical/SKILL.md or SKILL.md")
    triggers = manifest.get("triggers") or {}
    normalized = {
        "name": manifest.get("logical_name")
        or manifest.get("name")
        or os.path.basename(os.path.abspath(directory)),
        "description": manifest.get("description", ""),
        "purpose": manifest.get("purpose", ""),
        "canonical_path": canonical,
        "triggers": {
            "positive": list(triggers.get("positive", [])),
            "negative": list(triggers.get("negative", [])),
        },
        "tool_requirements": list(manifest.get("tool_requirements", [])),
        "permission_requirements": list(manifest.get("permission_requirements", [])),
        "resources": list(manifest.get("resources", [])),
        "scripts": list(manifest.get("scripts", [])),
        "model_family_hints": list(manifest.get("model_family_hints", [])),
        "harness_hints": list(manifest.get("harness_hints", [])),
        "provenance": manifest.get("provenance", {}) or {},
        "variants": [v["name"] for v in variants],
    }
    return normalized


def import_directory(registry: ComponentRegistry, directory: str) -> list[dict]:
    """Recursively import every component directory under ``directory``.

    A directory is a component if it contains ``skill.yaml`` or
    ``component.yaml``; nested component directories are still discovered.
    """
    imported: list[dict] = []
    for dirpath, dirnames, filenames in os.walk(directory):
        dirnames[:] = sorted(d for d in dirnames if d not in ("__pycache__", ".git"))
        has_manifest = any(n in filenames for n in SKILL_MANIFEST_NAMES + COMPONENT_MANIFEST_NAMES)
        if not has_manifest:
            continue
        record, files = load_component_source(dirpath)
        stored = registry.register(record, files)
        imported.append(
            {
                "type": stored.component_type.value,
                "name": stored.logical_name,
                "version": stored.version,
                "digest": stored.record_digest,
                "content_digest": stored.content_digest,
                "source": dirpath,
            }
        )
        # Do not descend into a component's own subdirectories.
        dirnames[:] = []
    imported.sort(key=lambda r: (r["type"], r["name"], r["version"]))
    return imported
