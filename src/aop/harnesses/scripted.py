"""Scripted harness for deterministic timeout/cancel/fail arms (M1, no GPU needed)."""
from __future__ import annotations

import threading
import time

from ..contracts.harness import HarnessCapabilities, HarnessName
from .base import HarnessOutput, PreparedSession


class ScriptedHarness:
    """Behaves like an adapter but executes a script instead of a model."""

    adapter_revision = "scripted-v1"

    def __init__(self, behavior: str = "succeed", delay_s: float = 0.0):
        assert behavior in ("succeed", "fail", "hang", "claim_success")
        self.behavior = behavior
        self.delay_s = delay_s

    def probe(self) -> HarnessCapabilities:
        return HarnessCapabilities(
            harness=HarnessName.PI, build_version="scripted", adapter_revision="scripted-v1",
            supports_headless=True, supports_streaming_events=True,
            supports_cancellation=True, supports_session_export=False,
            supports_isolated_home=True, supports_model_selection=False)

    def prepare(self, bundle_digest: str, layout_root: str, identity: str) -> PreparedSession:
        return PreparedSession(session_id="scripted", harness="scripted",
                               layout_root=layout_root)

    def start(self, task_prompt: str, session: PreparedSession, workspace: str,
              stdout_path: str, stderr_path: str, timeout_s: float,
              cancel: threading.Event, on_native_event=None) -> HarnessOutput:
        deadline = time.monotonic() + timeout_s
        step = 0.05
        if self.behavior == "hang":
            while True:
                if cancel.is_set():
                    return HarnessOutput(terminal_status="cancelled", exit_code=-15)
                if time.monotonic() >= deadline:
                    return HarnessOutput(terminal_status="timeout", exit_code=124)
                time.sleep(step)
        elapsed = 0.0
        target = min(self.delay_s, timeout_s + 1)
        while elapsed < target:
            if cancel.is_set():
                return HarnessOutput(terminal_status="cancelled", exit_code=-15)
            if time.monotonic() >= deadline:
                return HarnessOutput(terminal_status="timeout", exit_code=124)
            time.sleep(step)
            elapsed += step
        if self.behavior == "fail":
            return HarnessOutput(terminal_status="exited", exit_code=1,
                                 agent_claim="I could not fix it")
        if self.behavior == "claim_success":
            return HarnessOutput(terminal_status="exited", exit_code=0,
                                 agent_claim="Fixed! All tests pass (trust me)")
        return HarnessOutput(terminal_status="exited", exit_code=0,
                             agent_claim="done")
