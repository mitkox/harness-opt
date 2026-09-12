"""High-level profile lifecycle facade: define -> resolve -> lock -> compile ->
materialize / export / inspect / explain / diff (M3).

This module is the single entry point the CLI and the runner use. It keeps the
local HOP workspace layout explicit and versioned so old locked profiles stay
materializable and M1/M2 run evidence is never rewritten.
"""
from __future__ import annotations

import json
import os

import yaml
from pydantic import ValidationError

from .apm_export import export_apm, verify_export
from .compiler import CompilerError, get_target
from .contracts.profile import (
    COMPILER_VERSION,
    ComponentType,
    Lockfile,
    Profile,
    ResolvedProfile,
)
from .envcompat import resolve_env
from .lockfile import build_lockfile, verify_lock
from .registry import ComponentRegistry
from .resolver import ResolutionError, resolve_profile

HOP_HOME_VARS = ("HOP_HOME", "AOP_HOME")


class ProfileError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def hop_home(root: str | None = None) -> str:
    return root or resolve_env(*HOP_HOME_VARS,
                               default=os.path.join(repo_root(), ".hop"))


def registry_root(home: str | None = None) -> str:
    return os.path.join(hop_home(home), "registry")


def profile_store_root(home: str | None = None) -> str:
    return os.path.join(hop_home(home), "profiles")


def compiled_root(home: str | None = None) -> str:
    return os.path.join(hop_home(home), "compiled")


def open_registry(home: str | None = None) -> ComponentRegistry:
    return ComponentRegistry(registry_root(home))


# ---------------------------------------------------------------------------
# Load / validate
# ---------------------------------------------------------------------------

def parse_profile(data: dict) -> Profile:
    try:
        profile = Profile.model_validate(data)
    except ValidationError as exc:
        raise ProfileError("invalid_profile_schema", str(exc)) from exc
    if profile.apiVersion != "hop/v1":
        raise ProfileError("unsupported_api_version",
                           f"unsupported apiVersion {profile.apiVersion!r}")
    return profile


def load_profile(path: str) -> Profile:
    if not os.path.isfile(path):
        raise ProfileError("profile_not_found", f"no profile at {path}")
    with open(path) as fh:
        if path.endswith(".json"):
            data = json.load(fh)
        else:
            data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ProfileError("invalid_profile_schema", f"{path} must be a mapping")
    profile = parse_profile(data)
    if profile.kind != "Profile":
        raise ProfileError("invalid_profile_schema",
                           f"expected kind Profile, got {profile.kind!r}")
    return profile


def _resolution_context(profile: Profile,
                        overrides: dict[str, str] | None = None) -> dict[str, str]:
    context: dict[str, str] = {}
    for selector in profile.variant_selectors:
        context[selector.dimension.value] = selector.value
    for selector in profile.model.variant_selectors:
        context[selector.dimension.value] = selector.value
    context.update(overrides or {})
    return context


def resolve(profile: Profile, registry: ComponentRegistry,
            overrides: dict[str, str] | None = None) -> ResolvedProfile:
    return resolve_profile(profile, registry, _resolution_context(profile, overrides))


def validate_profile(path: str, registry: ComponentRegistry | None = None) -> dict:
    registry = registry or open_registry()
    try:
        profile = load_profile(path)
    except ProfileError as exc:
        return {"valid": False, "code": exc.code, "error": str(exc), "errors": [str(exc)]}
    try:
        resolved = resolve(profile, registry)
    except ResolutionError as exc:
        return {"valid": False, "code": exc.code, "error": str(exc),
                "detail": exc.detail, "errors": [str(exc)]}
    return {
        "valid": True, "code": "ok",
        "profile_digest": resolved.profile_digest,
        "components": len(resolved.components),
        "warnings": resolved.warnings,
    }


# ---------------------------------------------------------------------------
# Resolve / lock
# ---------------------------------------------------------------------------

def profile_store_path(lock_digest: str, home: str | None = None) -> str:
    return os.path.join(profile_store_root(home),
                        lock_digest.split(":")[1] + ".json")


def profile_store_index_path(home: str | None = None) -> str:
    return os.path.join(profile_store_root(home), "index.json")


def _load_store_index(home: str | None = None) -> dict:
    path = profile_store_index_path(home)
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_store_index(index: dict, home: str | None = None) -> None:
    path = profile_store_index_path(home)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".partial"
    with open(tmp, "w") as fh:
        json.dump(index, fh, sort_keys=True, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def persist_resolved(resolved: ResolvedProfile, lock: Lockfile,
                     home: str | None = None) -> str:
    """Persist one frozen resolution keyed by ``lock_digest``.

    Keying on the lock digest (not the source profile digest) is what makes
    historical materialization exact: two different resolutions of the same
    source profile coexist instead of overwriting each other.
    """
    path = profile_store_path(lock.lock_digest, home=home)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {"schema_version": "0.3", "resolved": resolved.model_dump(mode="json"),
               "lock": lock.model_dump(mode="json")}
    tmp = path + ".partial"
    with open(tmp, "w") as fh:
        json.dump(payload, fh, sort_keys=True, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    index = _load_store_index(home)
    locks = index.setdefault(resolved.profile_digest, [])
    if lock.lock_digest not in locks:
        locks.append(lock.lock_digest)
    _write_store_index(index, home)
    return path


def load_persisted(reference: str, home: str | None = None) -> dict:
    """Load a stored resolution by lock digest, falling back to profile digest.

    A profile digest resolves to the most recently persisted lock for that
    source profile; a lock digest is always exact.
    """
    if not reference.startswith("sha256:"):
        raise ProfileError("profile_not_found", f"invalid digest {reference!r}")
    path = profile_store_path(reference, home=home)
    if not os.path.exists(path):
        index = _load_store_index(home)
        candidates = index.get(reference) or []
        if candidates:
            path = profile_store_path(candidates[-1], home=home)
    if not os.path.exists(path):
        raise ProfileError(
            "profile_not_materializable",
            f"no stored resolution for {reference}; lock/compile it first")
    with open(path) as fh:
        return json.load(fh)


def lock_profile(path: str, registry: ComponentRegistry, *,
                 target: str = "pi", hop_version: str = "",
                 overrides: dict[str, str] | None = None,
                 home: str | None = None) -> tuple[Profile, ResolvedProfile, Lockfile]:
    profile = load_profile(path)
    resolved = resolve(profile, registry, overrides)
    if not hop_version:
        from hop import __version__

        hop_version = __version__
    lock = build_lockfile(resolved, hop_version=hop_version, compilation_target=target,
                          compiler_version=target_compiler_version(target))
    verify_lock(lock, registry)
    persist_resolved(resolved, lock, home=home)
    return profile, resolved, lock


def target_compiler_version(target: str) -> str:
    try:
        return get_target(target).compiler_version
    except CompilerError:
        return COMPILER_VERSION


def lock_path_for(profile_path: str) -> str:
    base = os.path.splitext(profile_path)[0]
    return base + ".hop.lock"


def write_lock(lock: Lockfile, path: str) -> None:
    with open(path, "w") as fh:
        json.dump(lock.model_dump(mode="json"), fh, sort_keys=True, indent=2)
        fh.write("\n")


def read_lock(path: str) -> Lockfile:
    with open(path) as fh:
        return Lockfile.model_validate(json.load(fh))


# ---------------------------------------------------------------------------
# Compile / materialize
# ---------------------------------------------------------------------------

def compile_resolved(profile: Profile, resolved: ResolvedProfile,
                     lock: Lockfile, registry: ComponentRegistry, *,
                     target: str = "pi", out_dir: str | None = None):
    compiler = get_target(target)
    artifact, files = compiler.compile(profile, resolved, registry, lock)
    if out_dir:
        for rel, data in sorted(files.items()):
            destination = os.path.join(out_dir, rel)
            os.makedirs(os.path.dirname(destination) or out_dir, exist_ok=True)
            with open(destination, "wb") as fh:
                fh.write(data)
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "compile-manifest.json"), "w") as fh:
            json.dump(artifact.model_dump(mode="json"), fh, sort_keys=True, indent=2)
    return artifact, files


def compile_profile(path: str, registry: ComponentRegistry, *, target: str = "pi",
                    out_dir: str | None = None, home: str | None = None):
    profile, resolved, lock = lock_profile(path, registry, target=target, home=home)
    artifact, files = compile_resolved(profile, resolved, lock, registry,
                                       target=target, out_dir=out_dir)
    record_path = os.path.join(compiled_root(home),
                               f"{resolved.profile_digest.split(':')[1]}.{target}.json")
    os.makedirs(os.path.dirname(record_path), exist_ok=True)
    with open(record_path, "w") as fh:
        json.dump({"artifact": artifact.model_dump(mode="json"),
                   "lock_digest": lock.lock_digest,
                   "profile_digest": resolved.profile_digest},
                  fh, sort_keys=True, indent=2)
    return profile, resolved, lock, artifact, files


def materialize(reference: str, registry: ComponentRegistry, *,
                target: str = "pi", out_dir: str | None = None,
                home: str | None = None) -> dict:
    """Reconstruct the exact locked configuration for a profile or digest."""
    if os.path.isfile(reference):
        profile = load_profile(reference)
        resolved = resolve(profile, registry)
        from hop import __version__

        lock = build_lockfile(resolved, hop_version=__version__,
                              compilation_target=target,
                              compiler_version=target_compiler_version(target))
    elif reference.startswith("sha256:"):
        stored = load_persisted(reference, home=home)
        resolved = ResolvedProfile.model_validate(stored["resolved"])
        lock = Lockfile.model_validate(stored["lock"])
        profile = _profile_from_resolved(resolved)
    else:
        raise ProfileError("profile_not_found",
                           f"{reference} is neither a profile file nor a digest")
    # Structural lock integrity first (no registry needed) so a tampered lock
    # is reported as such, then availability of every historical component.
    lock.verify()
    missing = []
    for comp in lock.components:
        try:
            record, _ = registry.get(comp.name, comp.version, comp.type)
            if record.record_digest != comp.digest:
                missing.append(f"{comp.name}@{comp.version} (stale digest)")
        except Exception as exc:  # noqa: BLE001
            missing.append(f"{comp.name}@{comp.version} ({exc})")
    if missing:
        raise ProfileError(
            "historical_component_unavailable",
            "cannot materialize locked profile: " + "; ".join(missing))
    verify_lock(lock, registry)
    artifact, files = compile_resolved(profile, resolved, lock, registry,
                                       target=target, out_dir=out_dir)
    return {"profile_digest": resolved.profile_digest, "lock_digest": lock.lock_digest,
            "target": target, "artifact_digest": artifact.artifact_digest,
            "artifact": artifact.model_dump(mode="json"),
            "out_dir": out_dir, "files": sorted(files)}


def _profile_from_resolved(resolved: ResolvedProfile) -> Profile:
    """Best-effort source profile used only for target compilation."""
    from .contracts.profile import ProfileHarness, ProfileMetadata

    agents = [f"{c.name}@{c.version}" for c in resolved.components
              if c.type == ComponentType.AGENT]
    skills = [f"{c.name}@{c.version}" for c in resolved.components
              if c.type == ComponentType.SKILL]
    prompt = next((c for c in resolved.components
                   if c.type == ComponentType.SYSTEM_PROMPT), None)
    tools = next((c for c in resolved.components
                  if c.type == ComponentType.TOOL_POLICY), None)
    context = next((c for c in resolved.components
                    if c.type == ComponentType.CONTEXT_POLICY), None)
    return Profile(
        metadata=ProfileMetadata(name=resolved.profile_name,
                                 version=resolved.profile_version),
        harness=ProfileHarness(name="pi"),
        system={"prompt_ref": f"{prompt.name}@{prompt.version}"} if prompt else {},
        agents=agents, skills=skills,
        tools={"policy_ref": f"{tools.name}@{tools.version}"} if tools else {},
        context={"policy_ref": f"{context.name}@{context.version}"} if context else {},
    )


# ---------------------------------------------------------------------------
# Inspect / explain / diff
# ---------------------------------------------------------------------------

def inspect(path: str, registry: ComponentRegistry, *, target: str = "pi") -> dict:
    if path.endswith(".lock") or path.endswith(".hop.lock"):
        lock = read_lock(path)
        verify_lock(lock, registry)
        return {"kind": "lock", "lock": lock.model_dump(mode="json"),
                "lock_digest": lock.lock_digest}
    profile, resolved, lock = lock_profile(path, registry, target=target)
    return {
        "kind": "profile", "profile_digest": resolved.profile_digest,
        "profile": profile.model_dump(mode="json"),
        "resolved": resolved.model_dump(mode="json"),
        "lock_digest": lock.lock_digest,
    }


def deps(path: str, registry: ComponentRegistry) -> dict:
    profile = load_profile(path)
    resolved = resolve(profile, registry)
    edges = []
    for node, children in sorted(resolved.dependency_graph.items()):
        for child in children:
            edges.append({"from": node, "to": child})
    return {
        "profile_digest": resolved.profile_digest,
        "resolution_digest": resolved.resolution_digest,
        "components": [{"type": c.type.value, "name": c.name, "version": c.version,
                        "digest": c.digest, "reason": c.reason,
                        "variant": c.variant}
                       for c in resolved.components],
        "graph": resolved.dependency_graph,
        "edges": edges,
    }


def explain(path: str, registry: ComponentRegistry, *, target: str = "pi") -> dict:
    profile = load_profile(path)
    resolved = resolve(profile, registry)
    from hop import __version__

    lock = build_lockfile(resolved, hop_version=__version__,
                          compilation_target=target,
                          compiler_version=target_compiler_version(target))
    compiler = get_target(target)
    transformations: list[dict] = []
    if target == "pi":
        transformations = [
            {"input": "system_prompt", "output": "system-prompt.md",
             "note": "passed via pi --system-prompt"},
            {"input": "skill", "output": "skills/<name>/",
             "note": "each emitted skill is passed via pi --skill"},
            {"input": "variant", "output": "skills/<name>/SKILL.md",
             "note": "selected variant replaces canonical instructions"},
            {"input": "policy", "output": "effective-policy.json",
             "note": "classified merge, mandatory rules preserved"},
        ]
    return {
        "profile_digest": resolved.profile_digest,
        "resolution_digest": resolved.resolution_digest,
        "lock_digest": lock.lock_digest,
        "target": target,
        "compiler": {"name": compiler.name, "version": compiler.compiler_version},
        "resolution_context": resolved.resolution_context,
        "resolved_components": [
            {"node": f"{c.type.value}:{c.name}@{c.version}", "digest": c.digest,
             "reason": c.reason, "variant": c.variant,
             "variant_reason": c.variant_reason,
             "dependencies": c.dependencies}
            for c in resolved.components],
        "variant_selections": resolved.variant_selections,
        "excluded_alternatives": resolved.excluded_alternatives,
        "policy_layers": {
            key: {"value": rule.get("value"), "class": rule.get("class"),
                  "sources": rule.get("sources"), "layer": rule.get("layer")}
            for key, rule in sorted(resolved.effective_policy.items())},
        "dependency_graph": resolved.dependency_graph,
        "target_transformations": transformations,
        "warnings": resolved.warnings,
    }


def diff_profiles(path_a: str, path_b: str, registry: ComponentRegistry, *,
                  verbose: bool = False) -> dict:
    profile_a = load_profile(path_a)
    profile_b = load_profile(path_b)
    a = resolve(profile_a, registry)
    b = resolve(profile_b, registry)

    def by_name(resolved: ResolvedProfile) -> dict:
        return {f"{c.type.value}:{c.name}": c for c in resolved.components}

    comp_a, comp_b = by_name(a), by_name(b)
    added = sorted(set(comp_b) - set(comp_a))
    removed = sorted(set(comp_a) - set(comp_b))
    changed = []
    for key in sorted(set(comp_a) & set(comp_b)):
        ca, cb = comp_a[key], comp_b[key]
        if (ca.version, ca.digest, ca.variant) != (cb.version, cb.digest, cb.variant):
            entry = {"component": key, "a": {"version": ca.version, "digest": ca.digest,
                                             "variant": ca.variant},
                     "b": {"version": cb.version, "digest": cb.digest,
                           "variant": cb.variant}}
            changed.append(entry)

    policy_changes = []
    keys = sorted(set(a.effective_policy) | set(b.effective_policy))
    for key in keys:
        ra = a.effective_policy.get(key)
        rb = b.effective_policy.get(key)
        if ra != rb:
            policy_changes.append({"key": key,
                                   "a": (ra or {}).get("value"),
                                   "b": (rb or {}).get("value"),
                                   "a_class": (ra or {}).get("class"),
                                   "b_class": (rb or {}).get("class")})

    result = {
        "a": {"name": a.profile_name, "version": a.profile_version,
              "profile_digest": a.profile_digest,
              "resolution_digest": a.resolution_digest},
        "b": {"name": b.profile_name, "version": b.profile_version,
              "profile_digest": b.profile_digest,
              "resolution_digest": b.resolution_digest},
        "identical": (a.resolution_digest == b.resolution_digest),
        "components": {"added": added, "removed": removed, "changed": changed},
        "variants": _variant_diff(a, b),
        "policy": policy_changes,
        "warnings": {"a": a.warnings, "b": b.warnings},
        "note": "content is compared by digest; use --verbose for summaries",
    }
    if verbose:
        result["prompt_a"] = _component_digest(a, ComponentType.SYSTEM_PROMPT)
        result["prompt_b"] = _component_digest(b, ComponentType.SYSTEM_PROMPT)
        result["agents_a"] = [c.name for c in a.components
                              if c.type == ComponentType.AGENT]
        result["agents_b"] = [c.name for c in b.components
                              if c.type == ComponentType.AGENT]
    return result


def _variant_diff(a: ResolvedProfile, b: ResolvedProfile) -> dict:
    keys = sorted(set(a.variant_selections) | set(b.variant_selections))
    changes = []
    for key in keys:
        va = a.variant_selections.get(key, {}).get("variant", "")
        vb = b.variant_selections.get(key, {}).get("variant", "")
        if va != vb:
            changes.append({"component": key, "a": va or "canonical",
                            "b": vb or "canonical"})
    return {"changed": changes}


def _component_digest(resolved: ResolvedProfile,
                      ctype: ComponentType) -> str:
    for comp in resolved.components:
        if comp.type == ctype:
            return comp.digest
    return ""


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export(path: str, registry: ComponentRegistry, *, target: str = "apm",
           out_dir: str | None = None, home: str | None = None) -> dict:
    if target != "apm":
        raise ProfileError("unsupported_target",
                           f"distribution target {target!r} is not implemented")
    profile, resolved, lock = lock_profile(path, registry, target="pi", home=home)
    from hop import __version__

    out = out_dir or os.path.join(hop_home(home), "exports",
                                  f"{resolved.profile_name}-"
                                  f"{resolved.profile_version}")
    summary = export_apm(resolved, registry, lock, out, hop_version=__version__)
    summary["lock_digest"] = lock.lock_digest
    return summary


def verify_exported(path: str, *, registry: ComponentRegistry | None = None,
                    source_profile: str | None = None,
                    home: str | None = None) -> dict:
    registry = registry or open_registry(home)
    resolved = None
    if source_profile:
        profile = load_profile(source_profile)
        resolved = resolve(profile, registry)
    return verify_export(path, registry=registry, resolved=resolved)


def component_list(registry: ComponentRegistry, component_type=None) -> list[dict]:
    return [{"type": r.component_type.value, "name": r.logical_name,
             "version": r.version, "digest": r.record_digest,
             "content_digest": r.content_digest,
             "dependencies": [d.ref for d in r.dependencies],
             "variants": [v.get("name") for v in (r.variants or [])]}
            for r in registry.all_components()
            if component_type is None or r.component_type == component_type]


def component_show(registry: ComponentRegistry, name: str, version: str = "") -> dict:
    if version:
        record, files = registry.get(name, version)
    else:
        versions = registry.versions(name)
        if not versions:
            raise ProfileError("missing_dependency", f"component {name} not found")
        record, files = registry.get(name, versions[0])
    return {
        "record": record.model_dump(mode="json"),
        "files": {rel: {"size_bytes": len(data)} for rel, data in sorted(files.items())},
    }
