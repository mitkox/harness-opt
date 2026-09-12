"""Shared harness adapter lifecycle (BUILD_PLAN §6)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from ..contracts.harness import HarnessCapabilities


@dataclass
class PreparedSession:
    session_id: str
    harness: str
    layout_root: str
    extra_env: dict[str, str] = field(default_factory=dict)
    # M3 compiled-profile inputs (empty for legacy profile-less runs).
    system_prompt: str = ""
    skill_dirs: list[str] = field(default_factory=list)


@dataclass
class HarnessOutput:
    terminal_status: str  # exited | timeout | cancelled | crashed
    exit_code: int
    agent_claim: str = ""
    files_changed: list[str] = field(default_factory=list)


class HarnessAdapter(Protocol):
    adapter_revision: str

    def probe(self) -> HarnessCapabilities:
        ...

    def prepare(self, bundle_digest: str, layout_root: str, identity: str) -> PreparedSession:
        ...

    def start(self, task_prompt: str, session: PreparedSession, workspace: str,
              stdout_path: str, stderr_path: str, timeout_s: float,
              cancel) -> HarnessOutput:
        """Blocking run. Streams native events to stdout_path incrementally."""
        ...
