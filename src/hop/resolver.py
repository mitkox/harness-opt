"""Deterministic dependency resolution, variant selection, and policy merge (M3).

Given a :class:`~hop.contracts.profile.Profile` and the immutable component
registry, resolution produces a :class:`~hop.contracts.profile.ResolvedProfile`:

* every floating reference becomes an exact version + content digest;
* missing dependencies, cycles, version conflicts, and ambiguous component
  identities fail closed with a machine-readable code;
* skill variants are chosen by compatibility selectors, and an ambiguous
  selection fails rather than picking arbitrarily;
* classified policy rules are merged with explicit precedence, and a mandatory
  rule can never be weakened by a lower-precedence layer.

Resolution is pure and offline: identical registry contents and inputs give an
identical result independent of declaration or filesystem ordering.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .contracts.profile import (
    ComponentRef,
    ComponentType,
    Profile,
    ResolvedComponent,
    ResolvedProfile,
    digest_payload,
)
from .registry import ComponentNotFound, ComponentRegistry, RegistryIntegrityError
from .semver import Constraint

# Precedence from lowest to highest for non-mandatory rules.
POLICY_LAYERS = ("component", "overlay", "workflow")


class ResolutionError(Exception):
    """Machine-readable resolution failure. ``code`` is stable."""

    def __init__(self, code: str, message: str, detail: dict | None = None):
        super().__init__(message)
        self.code = code
        self.detail = detail or {}


def resolve_profile(
    profile: Profile, registry: ComponentRegistry, context: dict[str, str] | None = None
) -> ResolvedProfile:
    context = {k.lower(): str(v) for k, v in (context or {}).items()}
    context.setdefault("harness", profile.harness.name)
    if profile.model.family_hint:
        context.setdefault("model_family", profile.model.family_hint)
    if profile.metadata.workflow:
        context.setdefault("workflow", profile.metadata.workflow)

    roots: list[tuple[ComponentType | None, ComponentRef]] = []
    if profile.system.prompt_ref:
        roots.append((ComponentType.SYSTEM_PROMPT, _ref(profile.system.prompt_ref)))
    for ref in profile.agents:
        roots.append((ComponentType.AGENT, _ref(ref)))
    for ref in profile.skills:
        roots.append((ComponentType.SKILL, _ref(ref)))
    if profile.tools.policy_ref:
        roots.append((ComponentType.TOOL_POLICY, _ref(profile.tools.policy_ref)))
    if profile.context.policy_ref:
        roots.append((ComponentType.CONTEXT_POLICY, _ref(profile.context.policy_ref)))
    for ref in profile.policy:
        roots.append((None, _ref(ref)))
    for ref in profile.hooks:
        roots.append((ComponentType.HOOK, _ref(ref)))
    for ref in profile.mcp:
        roots.append((ComponentType.MCP, _ref(ref)))
    if profile.harness.overlay_ref:
        roots.append((ComponentType.HARNESS_OVERLAY, _ref(profile.harness.overlay_ref)))

    resolver = _Resolver(registry, profile, context)
    for ctype, ref in roots:
        resolver.require(ctype, ref, reason=f"profile.{_ref_owner(ref, profile)}")
    resolver.resolve_all()
    return resolver.build()


def _ref(text: str) -> ComponentRef:
    from .contracts.profile import parse_ref

    return parse_ref(text)


def _ref_owner(ref: ComponentRef, profile: Profile) -> str:
    return "root"


@dataclass
class _Candidate:
    node: str
    ctype: ComponentType
    name: str
    version: str
    record_digest: str
    content_digest: str
    dependencies: list[ComponentRef]
    reasons: list[str] = field(default_factory=list)


@dataclass
class _Requirement:
    ctype: ComponentType | None
    constraint: Constraint
    reasons: list[str] = field(default_factory=list)


class _Resolver:
    def __init__(self, registry: ComponentRegistry, profile: Profile, context: dict[str, str]):
        self.registry = registry
        self.profile = profile
        self.context = context
        self.requirements: dict[str, _Requirement] = {}
        self.candidates: dict[str, _Candidate] = {}
        self.order: list[str] = []
        self.warnings: list[str] = []
        self.variant_selections: dict[str, dict] = {}
        self.excluded: list[dict] = []

    # -- requirement collection -------------------------------------------
    def require(self, ctype: ComponentType | None, ref: ComponentRef, reason: str) -> None:
        key = ref.name
        constraint = Constraint.parse(ref.version)
        existing = self.requirements.get(key)
        if existing is None:
            self.requirements[key] = _Requirement(ctype, constraint, [reason])
        else:
            if existing.ctype is not None and ctype is not None and existing.ctype != ctype:
                raise ResolutionError(
                    "ambiguous_component",
                    f"{ref.name} is required as both {existing.ctype.value} and {ctype.value}",
                    {"name": ref.name},
                )
            if existing.ctype is None:
                existing.ctype = ctype
            existing.reasons.extend([reason, f"constraint:{constraint.raw}"])

    # -- resolution --------------------------------------------------------
    def resolve_all(self) -> None:
        # Worklist: dependencies discovered while resolving must also be
        # resolved, so iterate until no new requirement appears.
        processed: set[str] = set()
        while True:
            pending = sorted(set(self.requirements) - processed)
            if not pending:
                break
            for name in pending:
                processed.add(name)
                requirement = self.requirements[name]
                existing = self._candidate_for_name(name)
                if existing is not None:
                    self.candidates[existing].reasons.extend(requirement.reasons)
                    continue
                record = self._select(name, requirement)
                node = f"{record.component_type.value}:{record.logical_name}@{record.version}"
                candidate = _Candidate(
                    node=node,
                    ctype=record.component_type,
                    name=record.logical_name,
                    version=record.version,
                    record_digest=record.record_digest or "",
                    content_digest=record.content_digest,
                    dependencies=list(record.dependencies),
                    reasons=list(requirement.reasons),
                )
                self.candidates[node] = candidate
                for dep in record.dependencies:
                    self.require(dep.type, dep, reason=f"{node} -> {dep.ref}")
        self._topological_order()
        self._select_variants()
        self._validate_compatibility()

    def _select(self, name: str, requirement: _Requirement):
        types = None
        if requirement.ctype is not None:
            types = [requirement.ctype]
        versions = self.registry.versions(name, types[0] if types else None)
        if not versions:
            # Distinguish unknown component from unsupported type.
            any_versions = self.registry.versions(name)
            if any_versions:
                types_present = sorted(
                    {r.component_type.value for r in self.registry.find(name=name)}
                )
                raise ResolutionError(
                    "unsupported_component_type",
                    f"{name} is registered, but not as "
                    f"{requirement.ctype.value if requirement.ctype else 'any type'}"
                    f" (present types: {types_present})",
                    {"name": name, "present_types": types_present},
                )
            raise ResolutionError(
                "missing_dependency",
                f"no registered component satisfies {name} "
                f"constraint {requirement.constraint.raw!r}",
                {"name": name, "constraint": requirement.constraint.raw},
            )
        matching = [v for v in versions if requirement.constraint.matches(v)]
        if not matching:
            raise ResolutionError(
                "version_conflict",
                f"no version of {name} satisfies constraint "
                f"{requirement.constraint.raw!r}; available: {versions}",
                {"name": name, "constraint": requirement.constraint.raw, "available": versions},
            )
        # versions() is sorted descending; pick the highest satisfying version.
        selected = matching[0]
        matches = self.registry.find(name=name, version=selected, component_type=requirement.ctype)
        if len(matches) > 1:
            raise ResolutionError(
                "ambiguous_component",
                f"{name}@{selected} is registered under multiple component types",
                {"name": name, "version": selected},
            )
        try:
            record, _ = self.registry.get(name, selected, requirement.ctype)
        except (ComponentNotFound, RegistryIntegrityError) as exc:
            raise ResolutionError(
                "digest_mismatch"
                if isinstance(exc, RegistryIntegrityError)
                else "missing_dependency",
                str(exc),
                {"name": name},
            ) from exc
        return record

    def _topological_order(self) -> None:
        # node -> dependency node keys, resolved against candidates by name.
        edges: dict[str, set[str]] = {node: set() for node in self.candidates}
        for node, candidate in self.candidates.items():
            for dep in candidate.dependencies:
                target = self._candidate_for_name(dep.name)
                if target is None:
                    raise ResolutionError(
                        "missing_dependency",
                        f"{node} depends on unregistered {dep.ref}",
                        {"from": node, "dependency": dep.ref},
                    )
                edges[node].add(target)
        # Kahn with lexicographic tie-break for determinism.
        indegree = {node: 0 for node in edges}
        for node, deps in edges.items():
            for dep in deps:
                indegree[dep] += 1
        ready = sorted(n for n, d in indegree.items() if d == 0)
        order: list[str] = []
        while ready:
            node = ready.pop(0)
            order.append(node)
            for dep in sorted(edges[node]):
                indegree[dep] -= 1
                if indegree[dep] == 0:
                    ready.append(dep)
                    ready.sort()
        if len(order) != len(edges):
            cyclic = sorted(n for n in edges if n not in order)
            raise ResolutionError(
                "dependency_cycle",
                f"dependency cycle detected among: {cyclic}",
                {"cycle_nodes": cyclic},
            )
        self.order = order
        self._edges = edges

    def _candidate_for_name(self, name: str) -> str | None:
        matches = [n for n in self.candidates if self.candidates[n].name == name]
        if len(matches) > 1:
            raise ResolutionError(
                "ambiguous_component",
                f"{name} resolved to multiple component nodes: {matches}",
                {"name": name, "nodes": sorted(matches)},
            )
        return matches[0] if matches else None

    def _select_variants(self) -> None:
        for node, candidate in self.candidates.items():
            if candidate.ctype != ComponentType.SKILL:
                continue
            record, files = self.registry.get(candidate.name, candidate.version)
            variants = record.variants or []
            selected, reason, excluded = select_variant(candidate.name, variants, self.context)
            self.excluded.extend(excluded)
            selection: dict = {"variant": selected, "reason": reason}
            if selected:
                variant_files = _variant_files(files, selected)
                if not variant_files:
                    raise ResolutionError(
                        "malformed_skill_package",
                        f"variant {selected!r} of {candidate.name} has no content files",
                        {"skill": candidate.name, "variant": selected},
                    )
                selection["content_digest"] = digest_payload(
                    {p: _sha(data) for p, data in sorted(variant_files.items())}
                )
                candidate.reasons.append(reason)
            self.variant_selections[node] = selection

    def _validate_compatibility(self) -> None:
        harness = self.profile.harness.name
        for node, candidate in self.candidates.items():
            record, _ = self.registry.get(candidate.name, candidate.version)
            compatibility = record.compatibility or {}
            harnesses = [str(h) for h in compatibility.get("harnesses", [])]
            if harnesses and harness not in harnesses:
                raise ResolutionError(
                    "incompatible_harness",
                    f"{candidate.name}@{candidate.version} declares harness "
                    f"compatibility {harnesses}, not {harness!r}",
                    {"component": node, "harness": harness},
                )
            families = [str(f) for f in compatibility.get("model_families", [])]
            hint = self.context.get("model_family", "")
            if families and hint and hint not in families:
                self.warnings.append(
                    f"{candidate.name}@{candidate.version} lists model families "
                    f"{families}; profile family hint is {hint!r} (descriptive only)"
                )
            self._check_required_tools(record)

    def _check_required_tools(self, record) -> None:
        required = []
        if record.component_type == ComponentType.SKILL:
            required = list((record.skill_manifest or {}).get("tool_requirements", []))
        if not required:
            return
        allowed = self._allowed_tools()
        if allowed is None:
            return
        missing = [tool for tool in required if tool not in allowed]
        if missing:
            raise ResolutionError(
                "missing_required_tool",
                f"{record.logical_name}@{record.version} requires tools "
                f"{missing} not allowed by the resolved tool policy",
                {"component": record.logical_name, "missing_tools": missing},
            )

    def _allowed_tools(self) -> set[str] | None:
        rule = self._raw_rule("tools.allow")
        if rule is None:
            return None
        value = rule["value"]
        if isinstance(value, list):
            return {str(v) for v in value}
        if isinstance(value, str):
            return {value}
        return None

    def _raw_rule(self, key: str) -> dict | None:
        # Search all resolved components for the rule; last by deterministic order.
        found = None
        for node in self.order:
            candidate = self.candidates[node]
            record, _ = self.registry.get(candidate.name, candidate.version)
            if key in (record.policy_rules or {}):
                found = record.policy_rules[key]
        return found

    # -- output ------------------------------------------------------------
    def build(self) -> ResolvedProfile:
        components = []
        for node in self.order:
            candidate = self.candidates[node]
            selection = self.variant_selections.get(node, {})
            record, _ = self.registry.get(candidate.name, candidate.version)
            components.append(
                ResolvedComponent(
                    type=candidate.ctype,
                    name=candidate.name,
                    version=candidate.version,
                    digest=candidate.record_digest,
                    dependencies=sorted(
                        self._candidate_for_name(d.name) or d.ref for d in candidate.dependencies
                    ),
                    reason="; ".join(sorted(set(candidate.reasons))),
                    source=record.source,
                    variant=selection.get("variant", ""),
                    variant_reason=selection.get("reason", ""),
                )
            )
        graph = {node: sorted(self._edges.get(node, set())) for node in self.order}
        overlay = next((c for c in components if c.type == ComponentType.HARNESS_OVERLAY), None)
        effective_policy = merge_policies(self)
        resolution_digest = digest_payload(
            {
                "profile_digest": self.profile.profile_digest(),
                "components": [
                    {
                        "type": c.type.value,
                        "name": c.name,
                        "version": c.version,
                        "digest": c.digest,
                        "variant": c.variant,
                    }
                    for c in components
                ],
                "policy": effective_policy,
                "context": dict(sorted(self.context.items())),
            }
        )
        return ResolvedProfile(
            profile_name=self.profile.metadata.name,
            profile_version=self.profile.metadata.version,
            profile_digest=self.profile.profile_digest(),
            resolution_digest=resolution_digest,
            components=components,
            harness_overlay=overlay,
            effective_policy=effective_policy,
            variant_selections=self.variant_selections,
            excluded_alternatives=sorted(
                self.excluded, key=lambda e: (e.get("component", ""), e.get("variant", ""))
            ),
            warnings=sorted(set(self.warnings)),
            dependency_graph=graph,
            resolution_context=dict(sorted(self.context.items())),
        )


def select_variant(
    name: str, variants: list[dict], context: dict[str, str]
) -> tuple[str, str, list[dict]]:
    """Deterministic variant selection.

    Returns ``(variant_name, reason, excluded)``. An empty name means the
    canonical instructions are used. More than one match is an error.
    """
    matches: list[tuple[str, list[str]]] = []
    excluded: list[dict] = []
    for variant in variants:
        matched = True
        reasons: list[str] = []
        for selector in variant.get("selectors", []):
            dimension = selector.get("dimension", "")
            value = str(selector.get("value", ""))
            actual = context.get(dimension, "")
            if dimension == "capability":
                capabilities = context.get("capability", "")
                capabilities = {c.strip() for c in capabilities.split(",") if c.strip()}
                if value not in capabilities:
                    matched = False
                    break
                reasons.append(f"capability:{value}")
            elif actual and actual.lower() == value.lower():
                reasons.append(f"{dimension}={value}")
            else:
                matched = False
                break
        if matched:
            matches.append((variant["name"], reasons))
        else:
            excluded.append(
                {
                    "component": name,
                    "variant": variant["name"],
                    "reason": _exclusion_reason(variant, context),
                }
            )
    if not matches:
        return "", "canonical: no variant selectors matched", excluded
    if len(matches) > 1:
        raise ResolutionError(
            "ambiguous_variant",
            f"{name}: {len(matches)} variants match: "
            f"{sorted(m for m, _ in matches)}; selection is ambiguous",
            {"component": name, "matches": sorted(m for m, _ in matches)},
        )
    variant_name, reasons = matches[0]
    return variant_name, "variant " + variant_name + " matched " + ",".join(reasons), excluded


def _exclusion_reason(variant: dict, context: dict[str, str]) -> str:
    parts = []
    for selector in variant.get("selectors", []):
        dimension = selector.get("dimension", "")
        value = str(selector.get("value", ""))
        actual = context.get(dimension, "<unset>")
        parts.append(f"{dimension}={value} (context {dimension}={actual})")
    return "selector mismatch: " + ", ".join(parts)


def _variant_files(files: dict[str, bytes], variant: str) -> dict[str, bytes]:
    prefix = f"variants/{variant}/"
    return {rel: data for rel, data in files.items() if rel.startswith(prefix)}


def _sha(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Policy merge
# ---------------------------------------------------------------------------

_LAYER_FOR_TYPE = {
    ComponentType.HARNESS_OVERLAY: "overlay",
    ComponentType.TOOL_POLICY: "workflow",
    ComponentType.CONTEXT_POLICY: "workflow",
    ComponentType.ORG_POLICY: "workflow",
    ComponentType.SYSTEM_PROMPT: "component",
    ComponentType.SKILL: "component",
    ComponentType.AGENT: "component",
    ComponentType.HOOK: "component",
    ComponentType.MCP: "component",
}


def merge_policies(resolver: _Resolver) -> dict[str, dict]:
    """Explicit classified merge with mandatory rules at highest precedence."""
    declarations: list[dict] = []
    for node in resolver.order:
        candidate = resolver.candidates[node]
        record, _ = resolver.registry.get(candidate.name, candidate.version)
        layer = _LAYER_FOR_TYPE.get(candidate.ctype, "component")
        for key, spec in sorted((record.policy_rules or {}).items()):
            declarations.append(
                {
                    "key": key,
                    "value": spec.get("value"),
                    "class": spec.get("class", "default"),
                    "layer": layer,
                    "source": node,
                }
            )
    return apply_policy_layers(declarations)


def apply_policy_layers(declarations: list[dict]) -> dict[str, dict]:
    """Apply classified rule declarations. Raises ResolutionError on weakening."""
    effective: dict[str, dict] = {}

    def forbid(code: str, message: str, detail: dict) -> None:
        raise ResolutionError(code, message, detail)

    # 1. forbidden_override guards.
    forbidden = {d["key"] for d in declarations if d["class"] == "forbidden_override"}
    for key in sorted(forbidden):
        attempts = [d for d in declarations if d["key"] == key]
        if len(attempts) > 1:
            forbid(
                "forbidden_override",
                f"policy key {key!r} is forbidden from override but is declared "
                f"{len(attempts)} times",
                {"key": key, "sources": sorted(d["source"] for d in attempts)},
            )
        if len(attempts) == 1 and attempts[0]["class"] != "forbidden_override":
            forbid("forbidden_override", f"policy key {key!r} cannot be set", {"key": key})

    # 2. mandatory declarations must agree with each other.
    mandatory: dict[str, dict] = {}
    for decl in declarations:
        if decl["class"] != "mandatory":
            continue
        prior = mandatory.get(decl["key"])
        if prior is not None and prior["value"] != decl["value"]:
            forbid(
                "policy_conflict",
                f"mandatory policy key {decl['key']!r} has conflicting values "
                f"from {prior['source']} and {decl['source']}",
                {"key": decl["key"]},
            )
        mandatory[decl["key"]] = decl

    # 3. Merge non-mandatory, non-forbidden declarations layer by layer.
    for layer in POLICY_LAYERS:
        layer_decls = [
            d
            for d in declarations
            if d["layer"] == layer and d["class"] not in ("mandatory", "forbidden_override")
        ]
        for decl in sorted(layer_decls, key=lambda d: (d["key"], d["source"])):
            key = decl["key"]
            current = effective.get(key)
            if decl["class"] == "additive":
                if current is None:
                    effective[key] = {
                        "value": _as_list(decl["value"]),
                        "class": "additive",
                        "sources": [decl["source"]],
                        "layer": layer,
                    }
                else:
                    current["value"] = _as_list(current["value"]) + _as_list(decl["value"])
                    current["sources"].append(decl["source"])
                    current["class"] = "additive"
                continue
            effective[key] = {
                "value": decl["value"],
                "class": decl["class"],
                "sources": [decl["source"]],
                "layer": layer,
            }

    # 4. Mandatory rules win last; a pre-existing different value is weakening.
    for key in sorted(mandatory):
        decl = mandatory[key]
        current = effective.get(key)
        if current is not None and current["value"] != decl["value"]:
            forbid(
                "policy_violation",
                f"lower-precedence configuration sets {key!r}={current['value']!r} "
                f"but mandatory policy requires {decl['value']!r} "
                f"(attempted weakening)",
                {
                    "key": key,
                    "mandatory": decl["value"],
                    "attempted": current["value"],
                    "sources": current["sources"],
                },
            )
        effective[key] = {
            "value": decl["value"],
            "class": "mandatory",
            "sources": [decl["source"]],
            "layer": "mandatory",
        }

    return {key: effective[key] for key in sorted(effective)}


def _as_list(value) -> list:
    if isinstance(value, list):
        return list(value)
    return [value]


def policy_digest(effective_policy: dict[str, dict]) -> str:
    return digest_payload(effective_policy)
