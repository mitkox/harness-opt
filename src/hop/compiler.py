"""Deterministic profile compiler with pluggable harness targets (M3, §7/§10).

``ProfileCompilerTarget`` translates a resolved profile into a harness-native
layout. The abstract base owns the pipeline and determinism guarantees; each
target only implements its own ``compile_*`` methods. Compilation never calls a
model, never rewrites prompts, and never reads the clock: identical inputs
produce identical output bytes.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod

import yaml

from .contracts.profile import (
    COMPILER_VERSION,
    CompiledArtifact,
    CompiledFile,
    ComponentType,
    Lockfile,
    Profile,
    ResolvedProfile,
    digest_payload,
    tree_digest,
)
from .registry import ComponentRegistry


class CompilerError(Exception):
    def __init__(self, code: str, message: str, detail: dict | None = None):
        super().__init__(message)
        self.code = code
        self.detail = detail or {}


def _norm_text(data: bytes) -> str:
    return data.decode("utf-8", errors="replace").replace("\r\n", "\n")


def _pick(files: dict[str, bytes], candidates: list[str]) -> tuple[str, bytes] | None:
    for candidate in candidates:
        if candidate in files:
            return candidate, files[candidate]
    raise CompilerError(
        "missing_required_resource", f"none of the expected files {candidates} present"
    )


class ProfileCompilerTarget(ABC):
    """Base class: one target per harness/distribution layout."""

    name = "base"
    status = "implemented"
    # Capability kinds this target can natively carry.
    supported_kinds: frozenset[str] = frozenset()

    @property
    def compiler_version(self) -> str:
        return COMPILER_VERSION

    # -- capability gate ---------------------------------------------------
    def validate_capabilities(
        self, profile: Profile, resolved: ResolvedProfile, registry: ComponentRegistry
    ) -> list[str]:
        """Raise ``CompilerError`` when a required feature is unsupported.

        Returns advisory warnings otherwise.
        """
        warnings: list[str] = []
        present = {c.type.value for c in resolved.components}
        required = present - set(self.supported_kinds)
        if required:
            raise CompilerError(
                "target_capability_missing",
                f"target {self.name} cannot compile: {sorted(required)}",
                {"target": self.name, "unsupported": sorted(required)},
            )
        # Executable skill resources must exist in the frozen content.
        for comp in resolved.components:
            if comp.type != ComponentType.SKILL:
                continue
            record, files = registry.get(comp.name, comp.version, comp.type)
            manifest = record.skill_manifest or {}
            for rel in list(manifest.get("resources", [])) + list(manifest.get("scripts", [])):
                if rel not in files:
                    raise CompilerError(
                        "missing_skill_resource",
                        f"{comp.name}@{comp.version} declares {rel!r} but it is "
                        "absent from the registered content",
                        {"component": comp.name, "resource": rel},
                    )
        return warnings

    # -- target-specific compilation --------------------------------------
    @abstractmethod
    def compile_profile(
        self, profile: Profile, resolved: ResolvedProfile, registry: ComponentRegistry
    ) -> dict[str, bytes]: ...

    @abstractmethod
    def compile_system_prompt(
        self, profile: Profile, resolved: ResolvedProfile, registry: ComponentRegistry
    ) -> dict[str, bytes]: ...

    @abstractmethod
    def compile_agents(
        self, profile: Profile, resolved: ResolvedProfile, registry: ComponentRegistry
    ) -> dict[str, bytes]: ...

    @abstractmethod
    def compile_skills(
        self, profile: Profile, resolved: ResolvedProfile, registry: ComponentRegistry
    ) -> dict[str, bytes]: ...

    @abstractmethod
    def compile_hooks(
        self, profile: Profile, resolved: ResolvedProfile, registry: ComponentRegistry
    ) -> dict[str, bytes]: ...

    @abstractmethod
    def compile_mcp(
        self, profile: Profile, resolved: ResolvedProfile, registry: ComponentRegistry
    ) -> dict[str, bytes]: ...

    def compile_policies(
        self, profile: Profile, resolved: ResolvedProfile, registry: ComponentRegistry
    ) -> dict[str, bytes]:
        return {}

    def emit_manifest(
        self, profile: Profile, resolved: ResolvedProfile, lock: Lockfile, files: dict[str, bytes]
    ) -> dict[str, bytes]:
        return {}

    # -- pipeline ----------------------------------------------------------
    def compile(
        self,
        profile: Profile,
        resolved: ResolvedProfile,
        registry: ComponentRegistry,
        lock: Lockfile | None = None,
    ) -> tuple[CompiledArtifact, dict[str, bytes]]:
        warnings = self.validate_capabilities(profile, resolved, registry)
        stages: list[tuple[str, dict[str, bytes]]] = [
            ("profile", self.compile_profile(profile, resolved, registry)),
            ("system_prompt", self.compile_system_prompt(profile, resolved, registry)),
            ("agents", self.compile_agents(profile, resolved, registry)),
            ("skills", self.compile_skills(profile, resolved, registry)),
            ("hooks", self.compile_hooks(profile, resolved, registry)),
            ("mcp", self.compile_mcp(profile, resolved, registry)),
            ("policies", self.compile_policies(profile, resolved, registry)),
        ]
        files: dict[str, bytes] = {}
        for stage, produced in stages:
            for path, data in produced.items():
                _validate_path(path)
                if path in files and files[path] != data:
                    raise CompilerError(
                        "duplicate_resource_collision",
                        f"two {stage} inputs produced different bytes for {path!r}",
                        {"path": path, "stage": stage},
                    )
                files[path] = data
        manifest = self.emit_manifest(profile, resolved, lock, files)
        for path, data in manifest.items():
            _validate_path(path)
            files[path] = data
        artifact_digest = tree_digest(files)
        artifact = CompiledArtifact(
            target=self.name,
            compiler_version=self.compiler_version,
            profile_digest=resolved.profile_digest,
            lock_digest=(lock.lock_digest if lock else ""),
            artifact_digest=artifact_digest,
            files=[
                CompiledFile(
                    path=rel,
                    size_bytes=len(files[rel]),
                    digest="sha256:" + _sha(files[rel]),
                    executable=files[rel].startswith(b"#!"),
                )
                for rel in sorted(files)
            ],
            warnings=warnings,
        )
        return artifact, files


def _validate_path(path: str) -> None:
    if path.startswith("/") or ".." in path.split("/"):
        raise CompilerError("unsafe_output_path", f"refusing output path {path!r}")


def _sha(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


class PiCompilerTarget(ProfileCompilerTarget):
    """Pi-native layout: system prompt, skills, agent prompts, policy files.

    Pi is driven with ``--system-prompt`` and repeated ``--skill`` flags. The
    emitted ``pi-profile.json`` records exactly which flags the runner supplies,
    so the compiled artifact remains the single source of truth.
    """

    name = "pi"
    supported_kinds = frozenset(
        {
            "system_prompt",
            "skill",
            "agent",
            "tool_policy",
            "context_policy",
            "org_policy",
            "harness_overlay",
        }
    )

    def compile_profile(self, profile, resolved, registry):
        return {}

    def compile_system_prompt(self, profile, resolved, registry):
        comp = _component(resolved, ComponentType.SYSTEM_PROMPT)
        if comp is None:
            return {}
        _record, files = registry.get(comp.name, comp.version, comp.type)
        _path, data = _pick(files, ["system.md", "system-prompt.md", "prompt.md"])
        return {"system-prompt.md": _norm_text(data).encode()}

    def compile_agents(self, profile, resolved, registry):
        out: dict[str, bytes] = {}
        for comp in resolved.components:
            if comp.type != ComponentType.AGENT:
                continue
            _record, files = registry.get(comp.name, comp.version, comp.type)
            _, data = _pick(files, [f"{comp.name}.md", "AGENT.md", "agent.md"])
            out[f"agents/{comp.name}.md"] = _norm_text(data).encode()
        return out

    def compile_skills(self, profile, resolved, registry):
        out: dict[str, bytes] = {}
        for comp in resolved.components:
            if comp.type != ComponentType.SKILL:
                continue
            record, files = registry.get(comp.name, comp.version, comp.type)
            manifest = record.skill_manifest or {}
            canonical = manifest.get("canonical_path", "canonical/SKILL.md")
            if comp.variant:
                variant_skill = f"variants/{comp.variant}/SKILL.md"
                if variant_skill in files:
                    out[f"skills/{comp.name}/SKILL.md"] = _norm_text(files[variant_skill]).encode()
                else:
                    out[f"skills/{comp.name}/SKILL.md"] = _norm_text(files[canonical]).encode()
            else:
                out[f"skills/{comp.name}/SKILL.md"] = _norm_text(files[canonical]).encode()
            # Resources and scripts: declared entries, deterministic order.
            for rel in sorted(
                set(manifest.get("resources", [])) | set(manifest.get("scripts", []))
            ):
                if rel not in files:
                    continue
                out[f"skills/{comp.name}/{rel}"] = files[rel]
            # Selected variant extras (excluding the variant SKILL.md itself).
            if comp.variant:
                prefix = f"variants/{comp.variant}/"
                for rel in sorted(files):
                    if (
                        rel.startswith(prefix)
                        and not rel.endswith("variant.yaml")
                        and rel != f"{prefix}SKILL.md"
                    ):
                        out[f"skills/{comp.name}/{rel[len(prefix) :]}"] = files[rel]
        return out

    def compile_hooks(self, profile, resolved, registry):
        # Pi carries no hook primitive in the pinned build; the capability gate
        # already rejects profiles that require hooks.
        return {}

    def compile_mcp(self, profile, resolved, registry):
        out: dict[str, bytes] = {}
        entries = []
        for comp in resolved.components:
            if comp.type != ComponentType.MCP:
                continue
            _record, files = registry.get(comp.name, comp.version, comp.type)
            candidate = _pick(files, ["mcp.json", "mcp.yaml", "mcp.yml", f"{comp.name}.json"])
            _, data = candidate
            try:
                payload = yaml.safe_load(_norm_text(data)) or {}
            except yaml.YAMLError as exc:
                raise CompilerError("malformed_mcp_definition", f"{comp.name}: {exc}") from exc
            entries.append({"name": comp.name, "config": payload})
        if entries:
            out["mcp.json"] = json.dumps(entries, sort_keys=True, indent=2).encode()
        return out

    def compile_policies(self, profile, resolved, registry):
        out: dict[str, bytes] = {}
        for kind, filename in (
            (ComponentType.TOOL_POLICY, "tool-policy.json"),
            (ComponentType.CONTEXT_POLICY, "context-policy.json"),
            (ComponentType.ORG_POLICY, "org-policy.json"),
        ):
            for comp in resolved.components:
                if comp.type != kind:
                    continue
                record, _ = registry.get(comp.name, comp.version, comp.type)
                if record.policy_rules:
                    out[filename] = json.dumps(
                        record.policy_rules, sort_keys=True, indent=2
                    ).encode()
        if resolved.effective_policy:
            out["effective-policy.json"] = json.dumps(
                resolved.effective_policy, sort_keys=True, indent=2
            ).encode()
        return out

    def emit_manifest(self, profile, resolved, lock, files):
        skills = sorted(
            f"skills/{c.name}" for c in resolved.components if c.type == ComponentType.SKILL
        )
        agents = sorted(
            f"agents/{c.name}.md" for c in resolved.components if c.type == ComponentType.AGENT
        )
        argv = ["pi"]
        if "system-prompt.md" in files:
            argv += ["--system-prompt", "system-prompt.md"]
        for skill in skills:
            argv += ["--skill", skill]
        tool_policy = None
        for comp in resolved.components:
            if comp.type == ComponentType.TOOL_POLICY:
                tool_policy = comp
                break
        manifest = {
            "kind": "HopPiCompiledProfile",
            "target": self.name,
            "compiler_version": self.compiler_version,
            "profile_digest": resolved.profile_digest,
            "lock_digest": (lock.lock_digest if lock else ""),
            "harness": profile.harness.name,
            "system_prompt": "system-prompt.md" if "system-prompt.md" in files else "",
            "skills": skills,
            "agents": agents,
            "invocation": {"argv": argv},
            "tool_policy_component": (tool_policy.name if tool_policy else ""),
        }
        return {"pi-profile.json": json.dumps(manifest, sort_keys=True, indent=2).encode()}


class PrimeCompilerTarget(ProfileCompilerTarget):
    name = "prime"
    status = "not_implemented"
    supported_kinds = frozenset()

    def validate_capabilities(self, profile, resolved, registry):
        raise CompilerError(
            "unsupported_target",
            "Prime Agent compilation target is an M3 extension point and is not implemented",
            {"target": self.name},
        )

    def compile_profile(self, *a, **k):
        raise CompilerError("unsupported_target", "prime target not implemented")

    compile_system_prompt = compile_agents = compile_skills = compile_profile
    compile_hooks = compile_mcp = compile_profile


class OpenCodeV2CompilerTarget(PrimeCompilerTarget):
    name = "opencode_v2"


class DeepSeekHarnessCompilerTarget(PrimeCompilerTarget):
    name = "deepseek_harness"


TARGETS: dict[str, ProfileCompilerTarget] = {
    "pi": PiCompilerTarget(),
    "prime": PrimeCompilerTarget(),
    "opencode_v2": OpenCodeV2CompilerTarget(),
    "deepseek_harness": DeepSeekHarnessCompilerTarget(),
}


def get_target(name: str) -> ProfileCompilerTarget:
    try:
        return TARGETS[name]
    except KeyError as exc:
        raise CompilerError(
            "unsupported_target",
            f"unknown compilation target {name!r}",
            {"target": name, "available": sorted(TARGETS)},
        ) from exc


def _component(resolved: ResolvedProfile, ctype: ComponentType):
    for comp in resolved.components:
        if comp.type == ctype:
            return comp
    return None


def artifact_manifest_digest(artifact: CompiledArtifact) -> str:
    return digest_payload(artifact.model_dump(mode="json", exclude={"artifact_digest"}))
