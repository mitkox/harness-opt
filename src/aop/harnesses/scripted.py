"""Scripted harness for deterministic pass/fail/timeout/cancel/scope arms (M1).

No GPU needed. ``repair`` applies the known-good fix for the bundled debugging
cases; ``scope_violation`` additionally edits a protected file so the runner's
scope gate must reject the run even though hidden tests would pass.
"""
from __future__ import annotations

import os
import threading
import time

from ..contracts.harness import HarnessCapabilities, HarnessName
from .base import HarnessOutput, PreparedSession

BEHAVIORS = ("succeed", "fail", "hang", "claim_success", "repair", "scope_violation")

_REPAIRS = {
    "median_bug.py": (
        "return (ordered[mid] + ordered[mid + 1]) / 2",
        "return (ordered[mid - 1] + ordered[mid]) / 2",
    ),
    "counter.py": (
        'return len(text.strip().split(" "))',
        "return len(text.strip().split())",
    ),
}


def apply_known_repair(workspace: str) -> list[str]:
    changed = []
    for name, (old, new) in _REPAIRS.items():
        path = os.path.join(workspace, name)
        if not os.path.exists(path):
            continue
        with open(path) as fh:
            content = fh.read()
        if old in content:
            with open(path, "w") as fh:
                fh.write(content.replace(old, new))
            changed.append(name)
    return changed


class ScriptedHarness:
    """Behaves like an adapter but executes a deterministic script."""

    adapter_revision = "scripted-v1"

    def __init__(self, behavior: str = "succeed", delay_s: float = 0.0):
        assert behavior in BEHAVIORS, behavior
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
        if self.behavior in ("repair", "scope_violation"):
            apply_known_repair(workspace)
        if self.behavior == "scope_violation":
            protected = os.path.join(workspace, "reproducer_visible.py")
            with open(protected, "a") as fh:
                fh.write("\n# candidate poked a protected file\n")
        if self.behavior == "fail":
            return HarnessOutput(terminal_status="exited", exit_code=1,
                                 agent_claim="I could not fix it")
        if self.behavior == "claim_success":
            return HarnessOutput(terminal_status="exited", exit_code=0,
                                 agent_claim="Fixed! All tests pass (trust me)")
        if self.behavior == "repair":
            return HarnessOutput(terminal_status="exited", exit_code=0,
                                 agent_claim="Repair applied")
        return HarnessOutput(terminal_status="exited", exit_code=0,
                             agent_claim="done")
