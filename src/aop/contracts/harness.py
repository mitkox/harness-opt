"""Harness build identity contract (BUILD_PLAN §4, §6)."""

from __future__ import annotations

from enum import Enum

from pydantic import Field

from .base import AopBase


class HarnessName(str, Enum):
    PI = "pi"
    PRIME = "prime"
    OPENCODE_V2 = "opencode_v2"
    DEEPSEEK_HARNESS = "deepseek_harness"


class HarnessStatus(str, Enum):
    DISCOVERED = "discovered"
    QUALIFIED = "qualified"
    BLOCKED = "blocked"


class HarnessBuild(AopBase):
    """Pinned identity of one harness build plus its adapter revision."""

    kind: str = Field(default="HarnessBuild", frozen=True)
    harness: HarnessName
    executable: str = Field(min_length=1)
    version: str = Field(min_length=1)
    source_revision: str = Field(default="")
    adapter_revision: str = Field(min_length=1)
    protocol: str = Field(default="")
    status: HarnessStatus = HarnessStatus.DISCOVERED
    blocked_reason: str = Field(default="")
    capabilities: dict[str, bool] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class HarnessCapabilities(AopBase):
    """Result of probe(): what this adapter/build actually supports."""

    kind: str = Field(default="HarnessCapabilities", frozen=True)
    harness: HarnessName
    build_version: str = Field(min_length=1)
    adapter_revision: str = Field(min_length=1)
    supports_headless: bool = False
    supports_streaming_events: bool = False
    supports_cancellation: bool = False
    supports_session_export: bool = False
    supports_isolated_home: bool = False
    supports_model_selection: bool = False
    unsupported: dict[str, str] = Field(default_factory=dict)
