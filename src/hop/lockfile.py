"""Deterministic HOP lockfile build + verification (M3, BUILD_PLAN §7).

A lockfile is the frozen, byte-stable result of resolving a profile. It records
exact versions and content digests, the dependency graph, the resolved harness
overlay, the merge policy digest, the compilation target, and the compiler
version. ``lock_digest`` binds all of it; any edit to any field is detected.
"""

from __future__ import annotations

from .contracts.profile import (
    COMPILER_VERSION,
    LOCKFILE_VERSION,
    LockComponent,
    Lockfile,
    ResolvedProfile,
    digest_payload,
)
from .resolver import policy_digest


def build_lockfile(
    resolved: ResolvedProfile,
    *,
    hop_version: str,
    compilation_target: str = "pi",
    compiler_version: str = COMPILER_VERSION,
) -> Lockfile:
    components = [
        LockComponent(
            type=c.type,
            name=c.name,
            version=c.version,
            digest=c.digest,
            dependencies=sorted(c.dependencies),
            variant=c.variant,
            source=c.source,
        )
        for c in sorted(resolved.components, key=lambda c: (c.type.value, c.name))
    ]
    overlay = None
    if resolved.harness_overlay is not None:
        overlay = LockComponent(
            type=resolved.harness_overlay.type,
            name=resolved.harness_overlay.name,
            version=resolved.harness_overlay.version,
            digest=resolved.harness_overlay.digest,
            dependencies=sorted(resolved.harness_overlay.dependencies),
            variant=resolved.harness_overlay.variant,
            source=resolved.harness_overlay.source,
        )
    graph = {node: sorted(deps) for node, deps in sorted(resolved.dependency_graph.items())}
    lock = Lockfile(
        lockfile_version=LOCKFILE_VERSION,
        hop_version=hop_version,
        profile_name=resolved.profile_name,
        profile_version=resolved.profile_version,
        profile_digest=resolved.profile_digest,
        compiler_version=compiler_version,
        compilation_target=compilation_target,
        components=components,
        dependency_graph=graph,
        harness_overlay=overlay,
        policy_digest=policy_digest(resolved.effective_policy),
    )
    lock.lock_digest = lock.compute_lock_digest()
    return lock


class LockVerificationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


_FLOATING_MARKERS = ("^", "~", ">", "<", "*", " ", ",")


def verify_lock(lock: Lockfile, registry=None) -> list[str]:
    """Return warnings; raise ``LockVerificationError`` on integrity failure."""
    lock.verify()
    if lock.lockfile_version != LOCKFILE_VERSION:
        raise LockVerificationError(
            "unsupported_lockfile_version",
            f"unsupported lockfile version {lock.lockfile_version!r}",
        )
    warnings: list[str] = []
    seen: set[tuple[str, str]] = set()
    for comp in lock.components:
        if any(marker in comp.version for marker in _FLOATING_MARKERS):
            raise LockVerificationError(
                "floating_reference", f"locked component {comp.name}@{comp.version} still floats"
            )
        key = (comp.type.value, comp.name)
        if key in seen:
            raise LockVerificationError(
                "duplicate_component", f"duplicate locked component {comp.type.value}/{comp.name}"
            )
        seen.add(key)
        if registry is not None:
            try:
                record, _ = registry.get(comp.name, comp.version, comp.type)
            except Exception as exc:
                raise LockVerificationError(
                    "missing_registry_component",
                    f"locked {comp.name}@{comp.version} is not in the registry: {exc}",
                ) from exc
            if record.record_digest != comp.digest:
                raise LockVerificationError(
                    "stale_digest",
                    f"{comp.name}@{comp.version} registry digest "
                    f"{record.record_digest} != locked {comp.digest}",
                )
        else:
            warnings.append("registry not supplied; digests not re-checked")
    # Every graph node must be a locked component (or the overlay).
    names = {f"{c.type.value}:{c.name}@{c.version}" for c in lock.components}
    for node, deps in lock.dependency_graph.items():
        if node not in names:
            raise LockVerificationError(
                "unknown_graph_node", f"dependency graph node {node} is not locked"
            )
        for dep in deps:
            if dep not in names:
                raise LockVerificationError(
                    "missing_graph_edge", f"{node} depends on unlocked {dep}"
                )
    return warnings


def lock_digest_payload(lock: Lockfile) -> str:
    return digest_payload(lock.canonical_payload())
