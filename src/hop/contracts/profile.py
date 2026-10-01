"""M3 profile / component / lock contracts (BUILD_PLAN §4, §7).

These contracts separate the distributable *agent-side* configuration
(``Profile`` / ``ResolvedProfile``) from the full evaluation environment
(``ExecutionBundle``, defined in :mod:`hop.contracts.records`).

Nothing here runs a model, optimizes a skill, or promotes anything. A profile
is a versioned, resolvable description of agent configuration; resolution
turns floating references into exact ``version`` + ``digest`` pairs.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from .base import sha256_hex

PROFILE_API_VERSION = "hop/v1"
PROFILE_SCHEMA_VERSION = "0.3"
LOCKFILE_VERSION = "1"
COMPILER_VERSION = "hop-compiler-v1"

DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"
COMPONENT_NAME_PATTERN = r"^[a-z][a-z0-9._-]*$"
VERSION_PATTERN = r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.\-]+)?$"


class ComponentType(str, Enum):
    """Every component kind the M3 registry can hold."""

    SYSTEM_PROMPT = "system_prompt"
    SKILL = "skill"
    AGENT = "agent"
    TOOL_POLICY = "tool_policy"
    CONTEXT_POLICY = "context_policy"
    ORG_POLICY = "org_policy"
    HARNESS_OVERLAY = "harness_overlay"
    HOOK = "hook"
    MCP = "mcp"
    RESOURCE = "resource"
    SKILL_SCRIPT = "skill_script"


class PolicyClass(str, Enum):
    """How a policy rule participates in the merge (BUILD_PLAN §7)."""

    MANDATORY = "mandatory"  # highest precedence; lower layers cannot change it
    DEFAULT = "default"  # wins only when no higher layer sets the key
    OVERRIDABLE = "overridable"  # a higher layer may replace it
    ADDITIVE = "additive"  # values accumulate across layers
    FORBIDDEN_OVERRIDE = "forbidden_override"  # nobody may set/change this key


class SelectorDimension(str, Enum):
    MODEL_FAMILY = "model_family"
    MODEL = "model"
    HARNESS = "harness"
    WORKFLOW = "workflow"
    CAPABILITY = "capability"
    RUNTIME = "runtime"


class _ProfileBase(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ComponentRef(_ProfileBase):
    """A floating or exact reference to a registry component."""

    name: str = Field(min_length=1, pattern=COMPONENT_NAME_PATTERN)
    version: str = Field(default="*", min_length=1)
    type: ComponentType | None = None

    @property
    def ref(self) -> str:
        return f"{self.name}@{self.version}"


def parse_ref(text: str) -> ComponentRef:
    """Parse ``name`` or ``name@constraint`` (constraint may contain ``@``? no)."""
    if "@" in text:
        name, version = text.split("@", 1)
    else:
        name, version = text, "*"
    return ComponentRef(name=name.strip(), version=version.strip() or "*")


class VariantSelector(_ProfileBase):
    dimension: SelectorDimension
    value: str = Field(min_length=1)


class ProfileMetadata(_ProfileBase):
    name: str = Field(min_length=1, pattern=COMPONENT_NAME_PATTERN)
    version: str = Field(default="0.1.0", pattern=VERSION_PATTERN)
    description: str = ""
    workflow: str = ""
    labels: dict[str, str] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class ProfileModel(_ProfileBase):
    deployment_ref: str = ""
    family_hint: str = ""
    variant_selectors: list[VariantSelector] = Field(default_factory=list)


class ProfileHarness(_ProfileBase):
    name: str = Field(min_length=1)
    version: str = ""
    overlay_ref: str = ""


class ProfilePrompt(_ProfileBase):
    prompt_ref: str = ""


class ProfileToolPolicy(_ProfileBase):
    policy_ref: str = ""


class ProfileContextPolicy(_ProfileBase):
    policy_ref: str = ""


class ProfileDistribution(_ProfileBase):
    target: str = Field(default="apm")


class Profile(_ProfileBase):
    """The distributable HOP profile (source form; refs may float)."""

    apiVersion: str = Field(default=PROFILE_API_VERSION)
    kind: str = Field(default="Profile")
    schema_version: str = Field(default=PROFILE_SCHEMA_VERSION)
    metadata: ProfileMetadata
    model: ProfileModel = Field(default_factory=ProfileModel)
    harness: ProfileHarness
    system: ProfilePrompt = Field(default_factory=ProfilePrompt)
    agents: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    tools: ProfileToolPolicy = Field(default_factory=ProfileToolPolicy)
    context: ProfileContextPolicy = Field(default_factory=ProfileContextPolicy)
    policy: list[str] = Field(default_factory=list)
    hooks: list[str] = Field(default_factory=list)
    mcp: list[str] = Field(default_factory=list)
    variant_selectors: list[VariantSelector] = Field(default_factory=list)
    distribution: ProfileDistribution = Field(default_factory=ProfileDistribution)

    def profile_digest(self) -> str:
        """Content identity of the *source* profile (floating refs included)."""
        return digest_payload(self.model_dump(mode="json"))


class ResolvedComponent(_ProfileBase):
    type: ComponentType
    name: str
    version: str
    digest: str = Field(pattern=DIGEST_PATTERN)
    dependencies: list[str] = Field(default_factory=list)
    reason: str = ""
    source: str = ""
    variant: str = ""
    variant_reason: str = ""


class ResolvedProfile(_ProfileBase):
    kind: str = Field(default="ResolvedProfile")
    schema_version: str = Field(default=PROFILE_SCHEMA_VERSION)
    profile_name: str
    profile_version: str
    profile_digest: str = Field(pattern=DIGEST_PATTERN)
    # Covers resolved component digests + policy + variant choices; unlike the
    # source profile_digest this changes whenever component content changes.
    resolution_digest: str = Field(default="", pattern=r"^(sha256:[0-9a-f]{64})?$")
    components: list[ResolvedComponent] = Field(default_factory=list)
    harness_overlay: ResolvedComponent | None = None
    effective_policy: dict[str, dict] = Field(default_factory=dict)
    variant_selections: dict[str, dict] = Field(default_factory=dict)
    excluded_alternatives: list[dict] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    dependency_graph: dict[str, list[str]] = Field(default_factory=dict)
    resolution_context: dict[str, str] = Field(default_factory=dict)

    def component(self, name: str) -> ResolvedComponent | None:
        for comp in self.components:
            if comp.name == name:
                return comp
        return None


class LockComponent(_ProfileBase):
    type: ComponentType
    name: str
    version: str
    digest: str = Field(pattern=DIGEST_PATTERN)
    dependencies: list[str] = Field(default_factory=list)
    variant: str = ""
    source: str = ""


class Lockfile(_ProfileBase):
    """Frozen resolution result. No floating references may remain."""

    kind: str = Field(default="Lockfile")
    lockfile_version: str = Field(default=LOCKFILE_VERSION)
    hop_version: str
    profile_name: str
    profile_version: str
    profile_digest: str = Field(pattern=DIGEST_PATTERN)
    compiler_version: str = Field(default=COMPILER_VERSION)
    compilation_target: str
    components: list[LockComponent] = Field(default_factory=list)
    dependency_graph: dict[str, list[str]] = Field(default_factory=dict)
    harness_overlay: LockComponent | None = None
    policy_digest: str = Field(default="")
    lock_digest: str = Field(default="")

    def canonical_payload(self) -> dict:
        payload = self.model_dump(mode="json", exclude={"lock_digest"})
        return payload

    def compute_lock_digest(self) -> str:
        return digest_payload(self.canonical_payload())

    def verify(self) -> None:
        expected = self.compute_lock_digest()
        if self.lock_digest != expected:
            raise ValueError(
                f"lockfile digest mismatch: recorded {self.lock_digest!r}, "
                f"recomputed {expected!r} (tampered or non-canonical lock)"
            )


class RegistryComponent(_ProfileBase):
    """Immutable registry record for one (logical name, version)."""

    kind: str = Field(default="RegistryComponent")
    schema_version: str = Field(default=PROFILE_SCHEMA_VERSION)
    logical_name: str = Field(min_length=1, pattern=COMPONENT_NAME_PATTERN)
    version: str = Field(pattern=VERSION_PATTERN)
    component_type: ComponentType
    content_digest: str = Field(pattern=DIGEST_PATTERN)
    record_digest: str = Field(default="", pattern=r"^(sha256:[0-9a-f]{64})?$")
    metadata: dict = Field(default_factory=dict)
    dependencies: list[ComponentRef] = Field(default_factory=list)
    compatibility: dict = Field(default_factory=dict)
    source: str = ""
    source_revision: str = ""
    created_at: str = ""
    files: list[dict] = Field(default_factory=list)
    variants: list[dict] = Field(default_factory=list)
    policy_rules: dict[str, dict] = Field(default_factory=dict)
    skill_manifest: dict = Field(default_factory=dict)


class ProvenanceManifest(_ProfileBase):
    """HOP provenance embedded in an exported distribution artifact."""

    kind: str = Field(default="HopProvenance")
    schema_version: str = Field(default=PROFILE_SCHEMA_VERSION)
    hop_version: str
    profile_name: str
    profile_version: str
    profile_digest: str = Field(pattern=DIGEST_PATTERN)
    compiler_version: str
    compilation_target: str
    lock_digest: str
    export_target: str
    package_digest: str = Field(pattern=DIGEST_PATTERN)
    source_components: list[dict] = Field(default_factory=list)
    package_files: list[dict] = Field(default_factory=list)
    compatibility_version: str = Field(default=PROFILE_SCHEMA_VERSION)
    generated_at: str = ""
    deterministic_payload_digest: str = Field(default="", pattern=r"^(sha256:[0-9a-f]{64})?$")


class CompiledFile(_ProfileBase):
    path: str
    digest: str = Field(pattern=DIGEST_PATTERN)
    size_bytes: int = Field(ge=0)
    executable: bool = False


class CompiledArtifact(_ProfileBase):
    kind: str = Field(default="CompiledArtifact")
    schema_version: str = Field(default=PROFILE_SCHEMA_VERSION)
    target: str
    compiler_version: str = Field(default=COMPILER_VERSION)
    profile_digest: str = Field(pattern=DIGEST_PATTERN)
    lock_digest: str = Field(default="")
    artifact_digest: str = Field(default="", pattern=r"^(sha256:[0-9a-f]{64})?$")
    files: list[CompiledFile] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def digest_payload(payload) -> str:
    """Canonical JSON digest of a nested payload (sorted keys, no whitespace)."""
    import json

    return "sha256:" + sha256_hex(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    )


def tree_digest(files: dict[str, bytes]) -> str:
    """Deterministic digest over ``{relative_path: bytes}``.

    Ordering is by path, never by filesystem enumeration, so directory order
    cannot perturb identity. Path and byte length are length-prefixed to avoid
    boundary ambiguity.
    """
    import hashlib

    digest = hashlib.sha256()
    for rel in sorted(files):
        data = files[rel]
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()
