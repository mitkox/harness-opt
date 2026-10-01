"""HOP -> APM distribution export + round-trip verification (M3, §11-§14).

HOP stays authoritative for profile/component/evaluation identity. This module
turns a locked profile into an APM-consumable project (``apm.yml`` + skills,
agents, instructions, hooks, MCP config), embeds HOP provenance, and can verify
an exported package against its HOP source.

The deterministic payload excludes the generation timestamp; if APM tooling is
installed the package can additionally be packed by ``apm pack`` in the demo
script (kept out of hermetic unit tests).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import UTC, datetime

import yaml

from .compiler import CompilerError, _norm_text, _pick
from .contracts.profile import (
    COMPILER_VERSION,
    ComponentType,
    ProvenanceManifest,
    ResolvedProfile,
    tree_digest,
)
from .registry import ComponentRegistry

METADATA_DIR = ".hop"
PROVENANCE_NAME = "provenance.json"
EXPORT_META_NAME = "export-meta.json"
LOCK_NAME = "hop.lock"


def _sha(data: bytes) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(data).hexdigest()


def _component(resolved: ResolvedProfile, ctype: ComponentType):
    for comp in resolved.components:
        if comp.type == ctype:
            return comp
    return None


def build_apm_payload(resolved: ResolvedProfile, registry: ComponentRegistry) -> dict[str, bytes]:
    """Deterministic APM package content (no provenance, no timestamp)."""
    files: dict[str, bytes] = {}

    system = _component(resolved, ComponentType.SYSTEM_PROMPT)
    if system is not None:
        _, content = registry.get(system.name, system.version, system.type)
        _, data = _pick(content, ["system.md", "system-prompt.md", "prompt.md"])
        files["instructions/system.md"] = _norm_text(data).encode()

    for comp in resolved.components:
        if comp.type == ComponentType.AGENT:
            _, content = registry.get(comp.name, comp.version, comp.type)
            _, data = _pick(content, [f"{comp.name}.md", "AGENT.md", "agent.md"])
            files[f"agents/{comp.name}.md"] = _norm_text(data).encode()

    for comp in resolved.components:
        if comp.type != ComponentType.SKILL:
            continue
        record, content = registry.get(comp.name, comp.version, comp.type)
        manifest = record.skill_manifest or {}
        canonical = manifest.get("canonical_path", "canonical/SKILL.md")
        if comp.variant and f"variants/{comp.variant}/SKILL.md" in content:
            files[f"skills/{comp.name}/SKILL.md"] = _norm_text(
                content[f"variants/{comp.variant}/SKILL.md"]
            ).encode()
        else:
            files[f"skills/{comp.name}/SKILL.md"] = _norm_text(content[canonical]).encode()
        for rel in sorted(set(manifest.get("resources", [])) | set(manifest.get("scripts", []))):
            if rel in content:
                files[f"skills/{comp.name}/{rel}"] = content[rel]
        if comp.variant:
            prefix = f"variants/{comp.variant}/"
            for rel in sorted(content):
                if (
                    rel.startswith(prefix)
                    and rel != f"{prefix}SKILL.md"
                    and not rel.endswith("variant.yaml")
                ):
                    files[f"skills/{comp.name}/{rel[len(prefix) :]}"] = content[rel]

    hook_entries = []
    for comp in resolved.components:
        if comp.type == ComponentType.HOOK:
            _, content = registry.get(comp.name, comp.version, comp.type)
            _, data = _pick(content, ["hooks.json", "hooks.yaml", "hooks.yml", f"{comp.name}.json"])
            try:
                hook_entries.append(
                    {"name": comp.name, "config": yaml.safe_load(_norm_text(data)) or {}}
                )
            except yaml.YAMLError as exc:
                raise CompilerError("malformed_hook_definition", f"{comp.name}: {exc}") from exc
    if hook_entries:
        files["hooks/hooks.json"] = json.dumps(hook_entries, sort_keys=True, indent=2).encode()

    mcp_entries = []
    for comp in resolved.components:
        if comp.type == ComponentType.MCP:
            _, content = registry.get(comp.name, comp.version, comp.type)
            _, data = _pick(content, ["mcp.json", "mcp.yaml", "mcp.yml"])
            try:
                mcp_entries.append(yaml.safe_load(_norm_text(data)) or {})
            except yaml.YAMLError as exc:
                raise CompilerError("malformed_mcp_definition", f"{comp.name}: {exc}") from exc
    if mcp_entries:
        files[".mcp.json"] = json.dumps(mcp_entries, sort_keys=True, indent=2).encode()

    # `includes` is exhaustive for APM: only list primitive directories that
    # actually exist in this payload, or `apm pack` fails on a missing path.
    includes = sorted({rel.split("/")[0] for rel in files if "/" in rel})
    files["apm.yml"] = _apm_manifest(resolved, includes)
    return files


def _apm_manifest(resolved: ResolvedProfile, includes: list[str]) -> bytes:
    manifest = {
        "name": f"hop-{resolved.profile_name}",
        "version": resolved.profile_version,
        "description": (
            f"HOP profile {resolved.profile_name}@"
            f"{resolved.profile_version}; generated from locked source."
        ),
        "license": "UNLICENSED",
        "includes": includes,
        "dependencies": {"apm": [], "mcp": []},
    }
    return yaml.safe_dump(manifest, sort_keys=True, default_flow_style=False).encode()


def export_apm(
    resolved: ResolvedProfile,
    registry: ComponentRegistry,
    lock,
    out_dir: str,
    *,
    hop_version: str,
    compilation_target: str = "pi",
    generated_at: str | None = None,
) -> dict:
    """Write the HOP APM export. Returns a summary with the package digest."""
    payload = build_apm_payload(resolved, registry)
    lock_digest = lock.lock_digest
    payload[f"{METADATA_DIR}/{LOCK_NAME}"] = json.dumps(
        lock.model_dump(mode="json"), sort_keys=True, indent=2
    ).encode()
    package_digest = tree_digest(payload)

    if os.path.exists(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    for rel, data in sorted(payload.items()):
        target = os.path.join(out_dir, rel)
        os.makedirs(os.path.dirname(target) or out_dir, exist_ok=True)
        with open(target, "wb") as fh:
            fh.write(data)

    provenance = ProvenanceManifest(
        hop_version=hop_version,
        profile_name=resolved.profile_name,
        profile_version=resolved.profile_version,
        profile_digest=resolved.profile_digest,
        compiler_version=COMPILER_VERSION,
        compilation_target=compilation_target,
        lock_digest=lock_digest,
        export_target="apm",
        package_digest=package_digest,
        source_components=[
            {
                "type": c.type.value,
                "name": c.name,
                "version": c.version,
                "digest": c.digest,
                "variant": c.variant,
            }
            for c in sorted(resolved.components, key=lambda c: (c.type.value, c.name))
        ],
        package_files=[
            {"path": rel, "digest": _sha(payload[rel]), "size_bytes": len(payload[rel])}
            for rel in sorted(payload)
        ],
        generated_at=generated_at or "",
        deterministic_payload_digest=package_digest,
    )
    prov_path = os.path.join(out_dir, METADATA_DIR, PROVENANCE_NAME)
    os.makedirs(os.path.dirname(prov_path), exist_ok=True)
    with open(prov_path, "w") as fh:
        json.dump(provenance.model_dump(mode="json"), fh, sort_keys=True, indent=2)
    stamp = generated_at or datetime.now(UTC).isoformat()
    with open(os.path.join(out_dir, METADATA_DIR, EXPORT_META_NAME), "w") as fh:
        json.dump(
            {
                "generated_at": stamp,
                "hop_version": hop_version,
                "note": "timestamp intentionally outside the hashed payload",
            },
            fh,
            sort_keys=True,
            indent=2,
        )
    return {
        "export_dir": out_dir,
        "package_digest": package_digest,
        "profile_digest": resolved.profile_digest,
        "lock_digest": lock_digest,
        "files": sorted(payload),
        "generated_at": stamp,
    }


class ExportVerificationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def verify_export(
    path: str,
    *,
    registry: ComponentRegistry | None = None,
    resolved: ResolvedProfile | None = None,
    lock_digest: str = "",
) -> dict:
    """Verify an exported APM package against its HOP provenance.

    Checks: provenance validity, exact file set (no unexpected/missing files),
    per-file digests, package digest, lock/profile digests, and -- when a
    resolved profile and registry are supplied -- deterministic recompilation.
    """
    prov_path = os.path.join(path, METADATA_DIR, PROVENANCE_NAME)
    if not os.path.exists(prov_path):
        raise ExportVerificationError("missing_provenance", f"no HOP provenance at {prov_path}")
    with open(prov_path) as fh:
        provenance = ProvenanceManifest.model_validate(json.load(fh))

    on_disk: dict[str, bytes] = {}
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, path).replace(os.sep, "/")
            if rel in (f"{METADATA_DIR}/{PROVENANCE_NAME}", f"{METADATA_DIR}/{EXPORT_META_NAME}"):
                continue
            with open(full, "rb") as fh:
                on_disk[rel] = fh.read()

    declared = {entry["path"]: entry for entry in provenance.package_files}
    errors: list[str] = []
    unexpected = sorted(set(on_disk) - set(declared))
    missing = sorted(set(declared) - set(on_disk))
    if unexpected:
        errors.append(f"unexpected files: {unexpected}")
    if missing:
        errors.append(f"missing files: {missing}")
    for rel, data in sorted(on_disk.items()):
        entry = declared.get(rel)
        if entry is None:
            continue
        if entry.get("digest") != _sha(data):
            errors.append(f"digest mismatch for {rel}")
        if int(entry.get("size_bytes", -1)) != len(data):
            errors.append(f"size mismatch for {rel}")

    recomputed_package = tree_digest(on_disk)
    if recomputed_package != provenance.package_digest:
        errors.append(
            f"package digest mismatch: recomputed {recomputed_package}, "
            f"recorded {provenance.package_digest}"
        )
    if provenance.deterministic_payload_digest != provenance.package_digest:
        errors.append("deterministic payload digest does not match package digest")

    embedded_lock = on_disk.get(f"{METADATA_DIR}/{LOCK_NAME}")
    lock_ok = False
    if embedded_lock is None:
        errors.append("embedded HOP lock missing")
    else:
        try:
            from .contracts.profile import Lockfile

            lock = Lockfile.model_validate(json.loads(embedded_lock.decode()))
            lock.verify()
            if lock.lock_digest != provenance.lock_digest:
                errors.append("embedded lock digest does not match provenance")
            elif lock.profile_digest != provenance.profile_digest:
                errors.append("embedded lock profile digest does not match provenance")
            else:
                lock_ok = True
        except Exception as exc:  # noqa: BLE001
            errors.append(f"embedded lock invalid: {exc}")

    recompiled_ok = None
    if resolved is not None and registry is not None:
        expected = build_apm_payload(resolved, registry)
        # The embedded lock is written by the exporter outside the canonical
        # payload builder; include the on-disk copy for a byte comparison. The
        # lock's own digest is verified independently above.
        if embedded_lock is not None:
            expected[f"{METADATA_DIR}/{LOCK_NAME}"] = embedded_lock
        expected_digest = tree_digest(expected)
        recompiled_ok = expected_digest == provenance.package_digest
        if not recompiled_ok:
            errors.append(
                f"deterministic recompilation mismatch: {expected_digest} != "
                f"{provenance.package_digest}"
            )
    if lock_digest and provenance.lock_digest != lock_digest:
        errors.append("caller lock digest does not match provenance")

    if errors:
        raise ExportVerificationError("export_verification_failed", "; ".join(errors))
    return {
        "ok": True,
        "package_digest": provenance.package_digest,
        "profile_digest": provenance.profile_digest,
        "lock_digest": provenance.lock_digest,
        "files": len(on_disk),
        "lock_ok": lock_ok,
        "recompiled_ok": recompiled_ok,
        "export_target": provenance.export_target,
        "source_components": provenance.source_components,
    }


def apm_available() -> bool:
    return shutil.which("apm") is not None


def apm_pack(export_dir: str, out_dir: str | None = None) -> dict:
    """Optional real-APM validation of a HOP export (used by the demo)."""
    if not apm_available():
        return {"apm_available": False}
    args = ["apm", "pack", "--format", "apm"]
    if out_dir:
        args += ["-o", out_dir]
    result = subprocess.run(
        args, check=False, cwd=export_dir, capture_output=True, text=True, timeout=120
    )
    return {
        "apm_available": True,
        "returncode": result.returncode,
        "stdout_tail": result.stdout[-2000:],
        "stderr_tail": result.stderr[-2000:],
        "out_dir": out_dir or os.path.join(export_dir, "build"),
    }
