"""Model deployment identity contract (BUILD_PLAN §4, §5)."""

from __future__ import annotations

from enum import Enum

from pydantic import Field

from .base import AopBase


class ModelStatus(str, Enum):
    PENDING_LOCAL_DISCOVERY = "pending_local_discovery"
    CAPACITY_BLOCKED = "capacity_blocked"
    NOT_AVAILABLE_LOCALLY = "not_available_locally"
    QUALIFIED = "qualified"
    QUARANTINED = "quarantined"
    RETIRED = "retired"


class ModelFamily(str, Enum):
    DEEPSEEK = "deepseek"
    QWEN = "qwen"
    GLM = "glm"
    OTHER = "other"


class LocalEndpoint(AopBase):
    """A registered local inference endpoint. Only loopback/host-local URLs allowed."""

    alias: str = Field(min_length=1)
    base_url: str = Field(min_length=1)
    api: str = Field(default="openai-completions")
    model_id: str = Field(min_length=1)


class WeightShard(AopBase):
    """One weight file belonging to a (possibly multi-file) deployment.

    Every shard is part of deployment identity: changing any shard must change
    the deployment digest and therefore the execution-bundle digest.
    """

    path: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)
    mtime_ns: int = Field(default=0, ge=0)
    partial_sha256_head_tail_4m: str = Field(default="")
    sha256: str = Field(default="", description="Full-file hash when available")
    note: str = Field(default="")


class ModelDeployment(AopBase):
    """Pinned identity of one local model deployment (AOP-002, AOP-007)."""

    kind: str = Field(default="ModelDeployment", frozen=True)
    deployment_id: str = Field(min_length=1)
    discovery_label: str = Field(min_length=1)
    family: ModelFamily
    # Provenance / fingerprint
    weight_path: str = Field(default="")
    weight_size_bytes: int = Field(default=0, ge=0)
    weight_partial_sha256: str = Field(default="")
    weight_shards: list[WeightShard] = Field(default_factory=list)
    weight_total_bytes: int = Field(default=0, ge=0)
    weight_manifest_digest: str = Field(default="")
    quantization: str = Field(default="")
    server_build: str = Field(default="")
    server_command: str = Field(default="")
    chat_template_sha256: str = Field(default="")
    context_length_configured: int = Field(default=0, ge=0)
    serving_config: dict = Field(default_factory=dict)
    endpoint: LocalEndpoint | None = None
    status: ModelStatus = ModelStatus.PENDING_LOCAL_DISCOVERY
    evidence: list[str] = Field(default_factory=list)

    def require_qualified(self) -> None:
        if self.status != ModelStatus.QUALIFIED:
            raise ValueError(
                f"deployment {self.deployment_id} is {self.status.value}, not qualified"
            )
        if self.endpoint is None:
            raise ValueError(f"deployment {self.deployment_id} has no registered endpoint")
        if not self.weight_shards:
            raise ValueError(
                f"deployment {self.deployment_id} has no weight-shard manifest; "
                "identity cannot be established"
            )
